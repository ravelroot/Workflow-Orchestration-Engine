import csv
import json
import random
from datetime import UTC, datetime
from pathlib import Path

from delco import Fail, Param, Workflow

REGIOES = ["norte", "nordeste", "centro_oeste", "sudeste", "sul"]

vendas = Workflow(
    "relatorio_vendas_diario",
    description="Extrai os pedidos do dia, valida, agrega por região em paralelo e publica um relatório CSV.",
    schedule="0 7 * * *",
    params={
        "pasta_saida": Param("/tmp/delco/relatorios", description="Pasta onde o CSV é gravado"),
        "minimo_pedidos": Param(1, min=0, description="Com menos pedidos que isso, o dia é pulado (loja fechada)"),
    },
    tags=["exemplo", "etl"],
    default_retries=2,
    default_retry_delay=10,
    run_timeout=1800,
)


def _pasta(ctx) -> Path:
    pasta = Path(ctx.params["pasta_saida"])
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _ler(caminho: str) -> list[dict]:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


@vendas.task(
    retries=3,
    timeout=120,
    retry_on=(ConnectionError, TimeoutError),
    description="Lê os pedidos do dia (simulado) e grava num arquivo intermediário.",
)
def extrair_pedidos(ctx):
    dia = (ctx.logical_date or datetime.now(UTC)).date()
    rng = random.Random(str(dia))
    pedidos = [
        {
            "id": i,
            "regiao": rng.choice(REGIOES),
            "valor": round(rng.uniform(30, 900), 2),
            "itens": rng.randint(1, 6),
        }
        for i in range(rng.randint(800, 1500))
    ]
    if len(pedidos) < int(ctx.params["minimo_pedidos"]):
        ctx.skip(f"Só {len(pedidos)} pedido(s) em {dia}: nada para consolidar")
    pedidos += rng.sample(pedidos, 12)
    caminho = _pasta(ctx) / f"pedidos-{dia}.json"
    caminho.write_text(json.dumps(pedidos), encoding="utf-8")
    ctx.log(f"{len(pedidos)} pedidos lidos para {dia}")
    return {"arquivo": str(caminho), "dia": str(dia), "total": len(pedidos)}


@vendas.task(depends_on=[extrair_pedidos], description="Confere se todo pedido tem os campos esperados e valor positivo.")
def validar_esquema(ctx):
    pedidos = _ler(ctx.upstream["extrair_pedidos"]["arquivo"])
    invalidos = [p for p in pedidos if p.get("valor", 0) <= 0 or p.get("regiao") not in REGIOES]
    if invalidos:
        raise Fail(f"{len(invalidos)} pedidos fora do esquema: corrija a origem antes de reprocessar")
    ctx.log("Esquema conferido: nenhum pedido inválido")
    return {"validos": len(pedidos)}


@vendas.task(depends_on=[extrair_pedidos], description="Remove pedidos repetidos (mesmo id) e regrava o arquivo.")
def remover_duplicados(ctx):
    caminho = ctx.upstream["extrair_pedidos"]["arquivo"]
    pedidos = _ler(caminho)
    unicos = list({p["id"]: p for p in pedidos}.values())
    Path(caminho).write_text(json.dumps(unicos), encoding="utf-8")
    ctx.log(f"{len(pedidos) - len(unicos)} duplicados removidos")
    return {"unicos": len(unicos)}


for _regiao in REGIOES:

    def agregar(ctx, regiao=_regiao):
        pedidos = [p for p in _ler(ctx.upstream["extrair_pedidos"]["arquivo"]) if p["regiao"] == regiao]
        total = round(sum(p["valor"] for p in pedidos), 2)
        ctx.log(f"{regiao}: {len(pedidos)} pedidos, R$ {total:,.2f}")
        return {"regiao": regiao, "pedidos": len(pedidos), "faturamento": total}

    vendas.add_task(
        f"agregar_{_regiao}",
        agregar,
        depends_on=[extrair_pedidos, validar_esquema, remover_duplicados],
        description=f"Soma pedidos e faturamento da região {_regiao.replace('_', '-')}.",
    )


@vendas.task(depends_on=[f"agregar_{r}" for r in REGIOES], description="Junta as cinco regiões e calcula o ticket médio.")
def consolidar(ctx):
    regioes = [ctx.upstream[f"agregar_{r}"] for r in REGIOES]
    pedidos = sum(r["pedidos"] for r in regioes)
    faturamento = round(sum(r["faturamento"] for r in regioes), 2)
    return {
        "regioes": regioes,
        "pedidos": pedidos,
        "faturamento": faturamento,
        "ticket_medio": round(faturamento / max(pedidos, 1), 2),
    }


@vendas.task(depends_on=[consolidar, extrair_pedidos], description="Escreve o CSV final.")
def gerar_relatorio(ctx):
    dados = ctx.upstream["consolidar"]
    caminho = _pasta(ctx) / f"vendas-{ctx.upstream['extrair_pedidos']['dia']}.csv"
    with caminho.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["regiao", "pedidos", "faturamento"])
        for r in dados["regioes"]:
            w.writerow([r["regiao"], r["pedidos"], f"{r['faturamento']:.2f}"])
        w.writerow(["total", dados["pedidos"], f"{dados['faturamento']:.2f}"])
    ctx.log(f"Relatório gravado em {caminho}")
    return {"arquivo": str(caminho)}


@vendas.task(depends_on=[gerar_relatorio], retries=4, retry_delay=30, description="Publica o relatório (aqui só registra; troque por e-mail, S3, Drive…).")
def publicar(ctx):
    ctx.log(f"Publicando {ctx.upstream['gerar_relatorio']['arquivo']} · chave {ctx.idempotency_key}")
    return {"publicado": True}

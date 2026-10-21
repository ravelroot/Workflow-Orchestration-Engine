"""Integração com um fornecedor instável: retentativas com espera exponencial,
lotes em paralelo, idempotência e regras de disparo (all_done e one_failed)."""

import random
import time

from delco import Workflow

catalogo = Workflow(
    "sincronizar_catalogo",
    description="Busca alterações de preço e estoque num fornecedor (simulado), aplica em lotes paralelos "
    "e reindexa a busca.",
    schedule="*/15 * * * *",
    params={"chance_de_falha": 0.3, "lotes": 3},
    tags=["exemplo", "integração"],
)

LOTES = 3


@catalogo.task(retries=4, retry_delay=5, backoff=2, timeout=60)
def buscar_alteracoes(ctx):
    """Chama a API do fornecedor. Falha de propósito às vezes para mostrar as retentativas."""
    rng = random.Random(f"{ctx.run_id}:{ctx.attempt}")
    time.sleep(rng.uniform(0.5, 2))
    if rng.random() < float(ctx.params["chance_de_falha"]) and not ctx.is_last_attempt:
        raise ConnectionError("O fornecedor não respondeu em 10 s (simulado)")
    alteracoes = rng.randint(40, 400)
    ctx.log(f"{alteracoes} produtos alterados desde a última sincronização")
    return {"alteracoes": alteracoes}


for _n in range(1, LOTES + 1):

    def aplicar(ctx, n=_n):
        total = ctx.upstream["buscar_alteracoes"]["alteracoes"]
        tamanho = total // LOTES + (1 if n <= total % LOTES else 0)
        ctx.log(f"Aplicando {tamanho} alterações · chave de idempotência {ctx.idempotency_key}")
        for i in range(10):
            time.sleep(0.15)
            ctx.progress((i + 1) * 10)
        return {"lote": n, "aplicadas": tamanho}

    catalogo.add_task(
        f"aplicar_lote_{_n}",
        aplicar,
        depends_on=["buscar_alteracoes"],
        description=f"Grava o lote {_n} de {LOTES} no catálogo.",
    )


@catalogo.task(depends_on=[f"aplicar_lote_{n}" for n in range(1, LOTES + 1)], timeout=120)
def reindexar_busca(ctx):
    """Atualiza o índice de busca com os produtos alterados."""
    aplicadas = sum(v["aplicadas"] for v in ctx.upstream.values())
    time.sleep(1)
    ctx.log(f"Índice atualizado com {aplicadas} produtos")
    return {"indexados": aplicadas}


@catalogo.task(depends_on=[reindexar_busca], trigger_rule="all_done")
def registrar_execucao(ctx):
    """Roda sempre, com sucesso ou falha antes (trigger_rule='all_done')."""
    ctx.log("Registro da sincronização gravado")
    return {"registrado": True}


@catalogo.task(depends_on=[buscar_alteracoes, reindexar_busca], trigger_rule="one_failed")
def alertar_falha(ctx):
    """Só roda se alguma dependência falhar (trigger_rule='one_failed')."""
    ctx.warning("Sincronização com falha: equipe de catálogo avisada (simulado)")
    return {"alerta": True}

import time

from delco import Fail, Param, Workflow

controle = Workflow(
    "controle",
    description="Skip, Fail e retry_on",
    params={
        "pular": Param(False, description="Pula a primeira tarefa"),
        "modo": Param("ok", choices=["ok", "definitivo", "outro_erro"]),
        "lote": Param(10, min=1, max=100),
    },
)


@controle.task(retries=0)
def talvez_pular(ctx):
    if ctx.params["pular"]:
        ctx.skip("nada para processar hoje")
    return {"lote": ctx.params["lote"]}


@controle.task(depends_on=[talvez_pular], trigger_rule="all_done", retries=3, retry_delay=0.05, retry_on=(ConnectionError,))
def processar(ctx):
    if ctx.params["modo"] == "definitivo":
        raise Fail("planilha corrompida")
    if ctx.params["modo"] == "outro_erro":
        raise KeyError("coluna")
    return "ok"


@controle.task(depends_on=[processar])
def publicar(ctx):
    return "publicado"


cronometro = Workflow("cronometro", description="Estoura o tempo da execução", run_timeout=1.5)


@cronometro.task(retries=0, timeout=None)
def demora(ctx):
    for _ in range(300):
        time.sleep(0.1)


@cronometro.task(depends_on=[demora])
def depois(ctx):
    return "não deveria rodar"


diario = Workflow("diario", schedule="0 6 * * *", timezone="UTC", params={"regiao": Param("sul", choices=["sul", "norte"])})


@diario.task(retries=0)
def processar_dia(ctx):
    return {"dia": ctx.logical_date.date().isoformat() if ctx.logical_date else None, "regiao": ctx.params["regiao"]}


@diario.task(depends_on=[processar_dia], retries=0)
def resumir(ctx):
    return ctx.upstream["processar_dia"]

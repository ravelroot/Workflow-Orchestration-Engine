from delco import Workflow

wf = Workflow("linear", description="Três passos em sequência", params={"base": 10})


@wf.task
def extrair(ctx):
    print("lendo dados")
    return {"valor": ctx.params["base"]}


@wf.task(depends_on=[extrair])
def transformar(ctx):
    ctx.progress(50, "metade")
    return {"valor": ctx.upstream["extrair"]["valor"] * 2}


@wf.task(depends_on=[transformar])
def carregar(ctx):
    ctx.log(f"gravando {ctx.upstream['transformar']['valor']}")
    return ctx.upstream["transformar"]["valor"] + 1


agendado = Workflow("agendado", schedule="*/5 * * * *", max_active_runs=1)


@agendado.task
def unico():
    return "ok"

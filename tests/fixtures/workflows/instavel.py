from delco import Workflow

wf = Workflow("instavel", description="Falha duas vezes e depois funciona")


@wf.task(retries=3, retry_delay=0.05, backoff=1)
def flaky(ctx):
    if ctx.attempt < 3:
        raise ConnectionError(f"serviço indisponível (tentativa {ctx.attempt})")
    return {"tentativas": ctx.attempt, "chave": ctx.idempotency_key}


@wf.task(depends_on=[flaky])
def depois(ctx):
    return ctx.upstream["flaky"]["tentativas"]


quebra = Workflow("quebra", description="Falha sempre", params={"consertado": False})


@quebra.task(retries=1, retry_delay=0.05)
def preparar(ctx):
    return 1


@quebra.task(depends_on=[preparar], retries=1, retry_delay=0.05)
def processar(ctx):
    if not ctx.params.get("consertado"):
        raise ValueError("dado inválido na linha 42")
    return "processado"


@quebra.task(depends_on=[processar])
def publicar(ctx):
    return "publicado"


@quebra.task(depends_on=[processar], trigger_rule="all_done")
def limpar(ctx):
    return "limpo"


@quebra.task(depends_on=[processar], trigger_rule="one_failed")
def alertar(ctx):
    return "alerta enviado"

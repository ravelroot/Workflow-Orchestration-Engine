import os
import time

from delco import Workflow

wf = Workflow("estoura_tempo", description="Passa do tempo limite")


@wf.task(timeout=1, retries=0)
def demorada():
    time.sleep(30)


longo = Workflow("longo", description="Roda até ser cancelado", max_active_runs=2)


@longo.task(retries=0, timeout=None)
def espera(ctx):
    for i in range(600):
        ctx.progress(i / 6)
        time.sleep(0.1)


@longo.task(depends_on=[espera])
def nunca():
    return "não deveria rodar"


saida = Workflow("saida_abrupta")


@saida.task(retries=0)
def morre():
    os._exit(3)


grande = Workflow("saida_grande")


@grande.task(retries=0)
def enorme():
    return "x" * 2_000_000

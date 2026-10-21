import time
from pathlib import Path

from delco import Workflow

wf = Workflow(
    "cinquenta",
    description="50 passos em sequência",
    params={"marcador": "", "pausa": 0.0, "lento": 0, "pausa_lenta": 0.0},
)

anterior = None
for n in range(1, 51):

    def passo(ctx, n=n):
        if ctx.params["marcador"]:
            with Path(ctx.params["marcador"]).open("a") as f:
                f.write(f"{n}\n")
        lento = n == ctx.params["lento"] and ctx.attempt == 1
        time.sleep(ctx.params["pausa_lenta"] if lento else ctx.params["pausa"])
        return n

    wf.add_task(
        f"passo_{n:02d}", passo, depends_on=[anterior] if anterior else [], retries=2, retry_delay=0.05
    )
    anterior = f"passo_{n:02d}"


unico = Workflow("marcador", params={"marcador": ""})


@unico.task
def marcar(ctx):
    with Path(ctx.params["marcador"]).open("a") as f:
        f.write("x\n")
    time.sleep(0.3)
    return "ok"

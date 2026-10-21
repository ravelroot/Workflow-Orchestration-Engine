import time

from delco import Workflow

wf = Workflow("paralelo", description="Leque de tarefas em paralelo", params={"pausa": 0.6})


@wf.task
def inicio():
    return time.time()


for i in range(1, 5):

    def parte(ctx, i=i):
        comeco = time.time()
        time.sleep(ctx.params["pausa"])
        return {"parte": i, "comeco": comeco, "fim": time.time()}

    wf.add_task(f"parte_{i}", parte, depends_on=["inicio"])


@wf.task(depends_on=[f"parte_{i}" for i in range(1, 5)])
def juntar(ctx):
    return sorted(v["parte"] for v in ctx.upstream.values())

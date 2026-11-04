import time

from delco import Param, Workflow

migracao = Workflow(
    "migracao_em_50_passos",
    description="Migração simulada de 50 tabelas, uma por passo. Ideal para ver a retomada depois de uma queda.",
    params={"segundos_por_passo": Param(1.5, min=0.1, max=60, description="Quanto cada passo demora")},
    tags=["exemplo", "resiliência"],
    default_retries=3,
    default_retry_delay=2,
)

anterior = None
for numero in range(1, 51):

    def passo(ctx, numero=numero):
        ctx.log(f"Migrando tabela {numero:02d} de 50")
        duracao = float(ctx.params["segundos_por_passo"])
        for i in range(5):
            time.sleep(duracao / 5)
            ctx.progress((i + 1) * 20)
        return {"tabela": numero, "linhas": numero * 1000}

    nome = f"passo_{numero:02d}"
    migracao.add_task(
        nome,
        passo,
        depends_on=[anterior] if anterior else [],
        description=f"Copia a tabela {numero:02d} e confere a contagem de linhas.",
    )
    anterior = nome

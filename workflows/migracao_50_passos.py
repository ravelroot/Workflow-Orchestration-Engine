"""Fluxo longo de 50 passos em sequência para testar a retomada após queda.

Experimente: dispare pelo painel e, no meio, derrube o worker
(`docker compose kill worker`). Suba de novo (`docker compose up -d worker`):
o Delco percebe que o worker sumiu quando o lease vence, devolve só o passo
interrompido para a fila e continua do ponto exato em que parou. Os passos já
concluídos não rodam de novo.
"""

import time

from delco import Workflow

migracao = Workflow(
    "migracao_em_50_passos",
    description="Migração simulada de 50 tabelas, uma por passo. Ideal para ver a retomada depois de uma queda.",
    params={"segundos_por_passo": 1.5},
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

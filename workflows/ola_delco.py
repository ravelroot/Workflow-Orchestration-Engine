"""O exemplo mais simples possível: duas tarefas em sequência.

Rode pelo painel (botão Executar) ou pela CLI:

    delco trigger ola_delco -p nome=Delco --wait
"""

from delco import Workflow

ola = Workflow(
    "ola_delco",
    description="Exemplo mínimo: cumprimenta e conta as letras da mensagem.",
    params={"nome": "mundo"},
    tags=["exemplo"],
)


@ola.task
def cumprimentar(ctx):
    """Monta a mensagem com o parâmetro `nome`."""
    mensagem = f"Olá, {ctx.params['nome']}!"
    ctx.log(mensagem)
    return {"mensagem": mensagem}


@ola.task(depends_on=[cumprimentar])
def contar_letras(ctx):
    """Lê o resultado da tarefa anterior e conta as letras."""
    texto = ctx.upstream["cumprimentar"]["mensagem"]
    letras = sum(c.isalpha() for c in texto)
    ctx.log(f"'{texto}' tem {letras} letras")
    return {"letras": letras}

from delco import Param, Workflow

ola = Workflow(
    "ola_delco",
    description="Exemplo mínimo: cumprimenta e conta as letras da mensagem.",
    params={"nome": Param("mundo", description="Quem o workflow cumprimenta", min=1, max=60)},
    tags=["exemplo"],
)


@ola.task(description="Monta a mensagem com o parâmetro `nome`.")
def cumprimentar(ctx):
    mensagem = f"Olá, {ctx.params['nome']}!"
    ctx.log(mensagem)
    return {"mensagem": mensagem}


@ola.task(depends_on=[cumprimentar], description="Lê o resultado da tarefa anterior e conta as letras.")
def contar_letras(ctx):
    texto = ctx.upstream["cumprimentar"]["mensagem"]
    letras = sum(c.isalpha() for c in texto)
    ctx.log(f"'{texto}' tem {letras} letras")
    return {"letras": letras}

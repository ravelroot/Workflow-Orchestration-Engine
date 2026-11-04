<p align="center">
  <img src="docs/images/logo.png" width="200" alt="Delco">
</p>

<h1 align="center">Delco<br><sub><sup><sup><sub>Copyright (©) 2026 Erik Pimenta</sub></sup></sup></sub></h1>

<p align="center">
  Motor de orquestração de workflows: você escreve o pipeline em Python, o Delco agenda, distribui as tarefas entre workers, repete o que falhar, avisa o time no Slack, Discord, Teams, Google Chat, Telegram ou por e-mail e, se um servidor cair no meio, continua exatamente de onde parou.<br>
  Pronto para usar, estudar ou adaptar.
</p>

<p align="center">
  Python 3.13 · Django 6 · Django REST Framework · Django Channels · MySQL 8.4 · Redis Streams · WebSockets · Docker Compose · Pytest · Ruff
</p>

---

> **Manual completo:** [`Manual-Delco.pdf`](Manual-Delco.pdf) explica a arquitetura, a instalação, como escrever e testar workflows, cada tela do painel, as integrações, a CLI, a API, a configuração, a operação em produção e a solução de problemas, passo a passo (cópia em [`docs/`](docs/Manual-Delco.pdf)).

> **Banco de dados:** [`Banco-de-Dados-Delco.pdf`](Banco-de-Dados-Delco.pdf) traz o diagrama de relacionamentos, as 12 tabelas com campos, tipos e chaves, as máquinas de estado e como preparar o MySQL. As fontes dos dois PDFs ficam em [`docs/fonte/`](docs/fonte) e são geradas de novo com `make pdfs`.

<p align="center">
  <img src="docs/images/tela-painel.png" alt="Visão geral com o painel Agora (vagas dos workers acesas), indicadores de 24 h, tarefas por hora e próximas execuções">
</p>

## Novidades da versão 1.2.0

**Para quem escreve workflows**

- **Parâmetros tipados:** `Param(1, min=1, max=30, description="…")` declara tipo, padrão, opções e limites. O painel monta um formulário a partir disso e a API, a CLI e o gatilho por URL recusam valores errados com a mensagem no campo certo.
- **`ctx.skip()` e `ctx.fail()`:** uma tarefa pode se declarar pulada (dia sem dados) ou falhar de vez, sem gastar retentativas. `retry_on=(ConnectionError, TimeoutError)` limita as retentativas aos erros passageiros.
- **Tempo limite da execução:** `Workflow(run_timeout=1800)` encerra a execução inteira se ela passar do prazo e avisa as integrações.
- **`delco new` e `delco dev run`:** cria um workflow a partir de um modelo e roda tudo no próprio terminal, sem banco, Redis nem workers. `wf.run_local()` faz o mesmo dentro dos seus testes.
- **Versões e código no painel:** cada workflow mostra o histórico de versões do grafo com o que mudou (tarefas novas, removidas e alteradas) e o arquivo `.py` com destaque de sintaxe.

**Para quem opera**

- **Reprocessar período:** cria uma execução para cada horário da agenda entre duas datas, com prévia, sem repetir o que já rodou.
- **Marcar tarefa como sucesso, falha ou pulada:** e a execução segue dali, refazendo só o que tinha sido pulado por causa dela.
- **Ações em lote:** cancelar, retomar e excluir várias execuções de uma vez; pausar e retomar várias agendas.
- **Pausar worker:** termina o que está rodando e para de pegar tarefas novas, útil antes de mexer na máquina.
- **Tela Sistema e `delco doctor`:** MySQL, Redis, agendadores, workers, filas sem worker, contas de demonstração ativas, configuração em uso (segredos mascarados) e limpeza do histórico na hora.
- **Sinal do agendador:** se nenhum agendador estiver de pé, o painel avisa no topo e a saúde da API mostra `scheduler: false`.
- **Métricas para o Prometheus** em `/api/metrics`, logs em JSON (`DELCO_LOG_FORMAT=json`) e `delco healthcheck` para o Docker saber se agendador e workers estão vivos.

**Integrações e segurança**

- **Telegram e e-mail (SMTP)** como destinos de aviso, e o evento **voltou a funcionar** (sucesso logo depois de uma falha).
- **Gatilho por URL assinado:** opcionalmente exige `X-Delco-Signature` ou `X-Hub-Signature-256` (o formato dos webhooks do GitHub).
- **Reenviar entregas** que desistiram, e **limite de chamadas** por minuto no login e no gatilho por URL.
- **Auditoria com filtros** (ação, pessoa, período, busca) e exportação em CSV.

**No painel**

- **Preferências da conta:** tema, **paleta de cores** (a do Delco e outras quatro), fuso das datas (local ou UTC), densidade das tabelas, itens por página e quebra de linha nos logs, guardadas na conta e válidas em qualquer navegador.
- **Avisos do navegador** quando uma execução falha ou quando uma que você disparou termina.
- **Log da tarefa:** acompanhar o fim, quebrar linhas e ir direto ao primeiro erro.
- **Execução:** motivo da falha em destaque, duração comparada com a mediana das anteriores, observação editável e exclusão (administrador).

**Por dentro**

- Configuração conferida ao subir, com todos os problemas listados de uma vez; Ruff, sintaxe do JavaScript e os workflows de exemplo rodando no CI; Dependabot; `Makefile` com os comandos do dia a dia; 103 testes.

## O que o Delco faz

- **Workflows como código:** cada arquivo `.py` da pasta `workflows/` define um grafo de tarefas com dependências, parâmetros tipados, agenda cron, fila, tempo limite e política de retentativas.
- **Agendamento:** expressões cron com fuso horário, pausa e retomada pelo painel (uma a uma ou em lote), limite de execuções simultâneas, disparo manual com formulário de parâmetros e reprocessamento de períodos.
- **Fila com worker pool:** as tarefas prontas vão para Redis Streams com grupo de consumidores; cada worker tem N vagas e roda cada tentativa num processo filho isolado.
- **Paralelismo:** tarefas sem dependência entre si rodam ao mesmo tempo, em quantos workers houver.
- **Retentativas:** espera exponencial com variação aleatória, teto configurável, filtro por tipo de erro e regras de disparo (`all_success`, `all_done`, `one_failed`).
- **Retomada após queda:** o estado vive no MySQL. Worker que some perde a posse da tarefa quando o lease vence, e só o passo interrompido volta para a fila.
- **Painel em tempo real:** grafo ao vivo, grade de execuções, linha do tempo, logs acompanhando a tarefa enquanto ela roda, quadro de vagas dos workers, desempenho por workflow, versões e código, tela Sistema, paleta de comandos, tema claro e escuro e cinco paletas de cores.
- **Integrações:** avisos no Slack, Discord, Teams, Google Chat, Telegram e por e-mail, webhook assinado para outros sistemas e gatilho por URL (com assinatura opcional) para disparar workflows de fora.
- **CLI e API:** tudo o que o painel faz também está na linha de comando (`delco trigger ... --wait`, saída em `--json`) e na API REST em Django REST Framework, documentada em Swagger.

## Arquitetura

```mermaid
flowchart LR
    DEV["Arquivos .py<br/>workflows/"] -->|lidos a cada 30 s| SCH["Agendador<br/>delco scheduler"]
    SCH -- "cria execuções e<br/>libera tarefas" --> PG[("MySQL<br/>fonte da verdade")]
    SCH -- "XADD" --> RS[("Redis Streams<br/>uma fila por nome")]
    RS -- "XREADGROUP" --> W1["Worker<br/>N vagas"]
    RS -- "XREADGROUP" --> W2["Worker<br/>N vagas"]
    W1 -- "processo filho<br/>por tentativa" --> T1["tarefa"]
    W1 & W2 -- "posse, heartbeat,<br/>resultado e logs" --> PG
    W1 & W2 -- "eventos" --> PS[("Redis pub/sub")]
    PS --> API["API + painel<br/>Django · DRF · Channels"]
    API -- "WebSocket" --> UI["Navegador"]
    API --- PG
    CLI["CLI delco"] -->|HTTP| API
    CI["GitHub Actions, CI,<br/>outros sistemas"] -- "POST /api/hooks/token" --> API
    SCH -- "notificações<br/>com novas tentativas" --> NT["Slack · Discord · Teams<br/>Google Chat · Telegram<br/>e-mail · webhook"]
    SCH -- "sinal a cada 5 s" --> RS
    PR["Prometheus"] -- "GET /api/metrics" --> API
```

| Processo | Comando | Responsabilidade |
|---|---|---|
| API | `delco api` | Daphne (ASGI) com o Django: REST no DRF, WebSocket no Channels, painel pelo WhiteNoise, Swagger e migrações |
| Agendador | `delco scheduler` (`manage.py scheduler`) | Lê a pasta de workflows (e guarda o código de cada versão), cria execuções agendadas, libera tarefas cujas dependências terminaram, encerra execuções que passaram do tempo limite, recupera leases vencidos, republica mensagens perdidas, envia as notificações e manda um sinal de vida para o Redis |
| Worker | `delco worker` (`manage.py worker`) | Consome as filas, assume cada tarefa no banco, roda a tentativa num processo filho com heartbeat, tempo limite e cancelamento; pode ser pausado pelo painel sem derrubar o processo |

Pode haver vários agendadores e quantos workers quiser: as linhas são travadas com `select_for_update(skip_locked=True)` do ORM (`FOR UPDATE SKIP LOCKED` no MySQL 8) e as execuções agendadas têm chave única por horário, então nada roda em dobro.

### Como a retomada funciona

1. O agendador marca a tarefa como **na fila** no MySQL e só depois publica a mensagem no Redis.
2. O worker assume a tarefa travando a linha com `select_for_update()` e `status='queued'` no filtro. Se a mensagem chegar repetida, a segunda tentativa não encontra mais a tarefa na fila e é descartada.
3. Durante a tentativa, o worker renova o **lease** a cada heartbeat. Se o servidor cair, o lease vence e o agendador devolve a tarefa para nova tentativa.
4. Tarefas que já terminaram ficam com o resultado gravado e não rodam de novo. Num fluxo de 50 passos que cai no passo 23, só o passo 23 é refeito.
5. Se o Redis for apagado, nada se perde: o agendador percebe tarefas na fila sem mensagem e as republica.
7. Um worker pausado pelo painel devolve à fila qualquer mensagem que já tinha lido; outro worker a assume.
6. `ctx.idempotency_key` é igual em todas as tentativas da mesma tarefa, para você não repetir efeitos externos (cobranças, e-mails, inserts).

### Como o Django está organizado

| Peça | Onde | O que faz |
|---|---|---|
| Modelos e migrações | `delco/models.py` · `delco/migrations/` | As 12 tabelas com CHECK, índices, chave única por horário (índice funcional) e `ON DELETE` no próprio banco (`DB_CASCADE` e `DB_SET_NULL` do Django 6.1) |
| Transições de estado | `delco/store.py` | ORM com `transaction.atomic()`, `select_for_update(skip_locked=True)` e `UPDATE` condicional (compare-and-set) |
| Integrações | `delco/integrations.py` | Entregas criadas na mesma transação que muda a execução, envio com `httpx` e SMTP pelo agendador, assinatura HMAC na saída e na entrada, gatilho por URL |
| Parâmetros | `delco/params.py` | `Param` com tipo, padrão, opções, mínimo e máximo; a mesma validação vale para o painel, a API, a CLI, o gatilho por URL e o `delco dev run` |
| Diagnóstico | `delco/doctor.py` · `delco/api/metrics.py` | As verificações do `delco doctor` e da tela Sistema, e as métricas no formato do Prometheus |
| API REST | `delco/api/views.py` · `serializers.py` · `auth.py` | Django REST Framework com autenticação por token, permissões por papel e erros num formato único |
| Documentação | `/api/docs` | OpenAPI gerado pelo drf-spectacular, Swagger servido sem CDN |
| Tempo real | `delco/api/consumers.py` · `events.py` | Django Channels: o evento chega do Redis e vai para os grupos `delco.eventos` e `delco.tentativa.<id>` |
| Processos | `delco/management/commands/` | `worker`, `scheduler`, `seed` e `createuser` são comandos do Django; `delco <comando>` é só um atalho |
| Configuração | `delco/config.py` · `delco/settings.py` | Tudo vem das variáveis `DELCO_*`, conferidas ao subir (um valor errado vira uma mensagem clara, não um traceback); MySQL pelo mysqlclient com a sessão em UTC, CSP nativa do Django 6, WhiteNoise para o painel e logs em texto ou JSON |

<p align="center">
  <img src="docs/images/tela-execucao.png" alt="Execução de 50 passos em andamento com o grafo em zigue-zague">
</p>

## Como rodar

Você só precisa do **Docker** com Docker Compose.

**Windows:** dê dois cliques em `iniciar.cmd`.

**Linux ou macOS:**

```bash
./iniciar.sh
```

Na primeira vez o script cria o arquivo `.env` com senha do banco, chave secreta e token de métricas aleatórios, sobe os contêineres (MySQL, Redis, API, agendador e dois workers) e abre **http://localhost:8080**.

| Endereço | O que é |
|---|---|
| http://localhost:8080 | Painel |
| http://localhost:8080/api/docs | Documentação da API (Swagger) |
| http://localhost:8080/api/openapi.json | Esquema OpenAPI 3 |

Para rodar sem o script:

```bash
cp .env.example .env        # troque DELCO_DB_PASSWORD
docker compose up -d --build
docker compose up -d --scale worker=4   # mais workers quando precisar
docker compose exec api delco doctor    # confere se está tudo certo
```

No Linux e no macOS, o [`Makefile`](Makefile) junta os comandos do dia a dia: `make subir`, `make logs`, `make testes`, `make lint`, `make doutor` e `make novo NOME=meu_workflow`.

## Contas de demonstração

Todas usam a senha **`Delco@2026`**. Elas existem só para testes, para qualquer pessoa entrar logo depois de baixar o projeto. Os nomes são fictícios e os e-mails usam o domínio de exemplo `delco.dev`.

| Papel | E-mail | O que pode fazer |
|---|---|---|
| Administrador | `admin@delco.dev` | Tudo, incluindo usuários e auditoria |
| Operador | `operador@delco.dev` | Disparar, cancelar, retomar, pausar e consultar |
| Leitura | `leitura@delco.dev` | Só consultar |

Na primeira subida o Delco cria uma semana de histórico fictício para o painel não abrir vazio. Para usar de verdade, suba com `DELCO_SEED=false` num banco vazio e crie seu administrador com `docker compose exec api python manage.py createuser --role admin --email voce@empresa.com --name "Seu Nome"` (ou o atalho `delco users create`).

## Escrevendo um workflow

Crie um arquivo em `workflows/` (ou rode `delco new relatorio_semanal --schedule "0 8 * * 1"`). O agendador o encontra em até 30 segundos, sem reiniciar nada.

```python
from delco import Fail, Param, Workflow

vendas = Workflow(
    "relatorio_vendas",
    description="Extrai, agrega e publica o relatório do dia.",
    schedule="0 7 * * *",
    timezone="America/Sao_Paulo",
    params={
        "dias": Param(1, min=1, max=30, description="Quantos dias processar"),
        "formato": Param("csv", choices=["csv", "xlsx"]),
    },
    max_active_runs=1,
    run_timeout=1800,
)

@vendas.task(retries=3, retry_delay=10, timeout=120, retry_on=(ConnectionError, TimeoutError))
def extrair(ctx):
    linhas = buscar_pedidos(ctx.params["dias"])
    if not linhas:
        ctx.skip("Nenhum pedido no período")
    return {"linhas": len(linhas)}

@vendas.task(depends_on=[extrair], queue="critical")
def publicar(ctx):
    if ctx.upstream["extrair"]["linhas"] > 1_000_000:
        raise Fail("Volume fora do normal: confira a origem antes de publicar")
    ctx.progress(50, "metade")
    ctx.log(f"Publicado · chave {ctx.idempotency_key}")

@vendas.task(depends_on=[publicar], trigger_rule="all_done")
def limpar(ctx):
    ...
```

| Opção da tarefa | Padrão | O que faz |
|---|---|---|
| `depends_on` | `[]` | Tarefas (função ou nome) que precisam terminar antes |
| `retries` | `2` | Novas tentativas depois da primeira falha |
| `retry_delay` / `backoff` | `5` s / `2` | Espera = `retry_delay × backoff^(tentativa-1)`, com ±10% de variação |
| `retry_on` | qualquer erro | Só estes tipos de exceção geram nova tentativa; os outros falham de vez |
| `timeout` | `300` s | Tempo máximo da tentativa; `None` para sem limite |
| `queue` | `default` | Fila (workers escolhem quais filas consomem) |
| `trigger_rule` | `all_success` | `all_done` roda sempre; `one_failed` só se algo falhar |

| Dentro da tarefa | O que faz |
|---|---|
| `ctx.params` · `ctx.upstream` | Parâmetros já validados e o resultado das tarefas anteriores |
| `ctx.log()` · `ctx.progress()` | Linha de log e barra de andamento no painel |
| `ctx.skip("motivo")` | Marca a tarefa como pulada; as que dependem dela (com `all_success`) também são puladas |
| `ctx.fail("motivo")` ou `raise Fail(...)` | Falha sem gastar retentativas (`retry=True` para repetir mesmo assim) |
| `ctx.idempotency_key` · `ctx.logical_date` · `ctx.attempt` | Chave igual em todas as tentativas, data do horário agendado e número da tentativa |

`Param` aceita `type` (`str`, `int`, `float`, `bool`, `date`, `list`, `dict`), `choices`, `min`, `max`, `required`, `label` e `secret` (o valor aparece mascarado no painel e na auditoria). Parâmetros sem `Param` continuam funcionando e são tratados como livres.

**Testar sem servidor:**

```bash
delco dev run relatorio_vendas -p dias=3        # roda tudo aqui, na ordem, com logs e retentativas
delco dev run relatorio_vendas --json            # resultado de cada tarefa, para scripts
delco validate workflows                         # confere todos os arquivos sem rodar nada
```

Nos seus testes, `vendas.run_local({"dias": 3})` devolve o estado e o resultado de cada tarefa.

Exemplos prontos na pasta [`workflows/`](workflows): `ola_delco` (mínimo), `relatorio_vendas_diario` (leque paralelo, parâmetros, `skip`, `Fail` e `retry_on`), `sincronizar_catalogo` (fornecedor instável e regras de disparo), `backup_noturno` (fila `critical`, arquivos reais e tempo limite da execução) e `migracao_em_50_passos` (para testar a retomada derrubando um worker no meio).

| Tela | |
|---|---|
| <img src="docs/images/tela-workflows.png" alt="Lista de workflows com agenda, últimas execuções e seleção em lote"> | <img src="docs/images/tela-grade.png" alt="Grade de execuções por tarefa"> |
| <img src="docs/images/tela-parametros.png" alt="Executar workflow com formulário montado a partir dos parâmetros"> | <img src="docs/images/tela-reprocessar.png" alt="Reprocessar período com prévia dos horários"> |
| <img src="docs/images/tela-versoes.png" alt="Histórico de versões do grafo com o que mudou"> | <img src="docs/images/tela-codigo.png" alt="Código do workflow com destaque da tarefa"> |
| <img src="docs/images/tela-execucao.png" alt="Execução de 50 passos em andamento com o grafo em zigue-zague"> | <img src="docs/images/tela-tarefa.png" alt="Gaveta da tarefa com log ao vivo, busca, filtros e marcação manual"> |
| <img src="docs/images/tela-falha.png" alt="Execução com falha e as tentativas registradas"> | <img src="docs/images/tela-lote.png" alt="Execuções selecionadas com a barra de ações em lote"> |
| <img src="docs/images/tela-integracoes.png" alt="Integrações com Slack, Discord, Teams, Telegram, e-mail, webhook e gatilho por URL"> | <img src="docs/images/tela-desempenho.png" alt="Desempenho por workflow com p50, p95 e tarefas que mais falham"> |
| <img src="docs/images/tela-sistema.png" alt="Tela Sistema com verificações, agendadores e configuração"> | <img src="docs/images/tela-workers.png" alt="Quadro de vagas dos workers, com pausar e retomar"> |
| <img src="docs/images/tela-conta.png" alt="Minha conta com preferências, paletas de cores e tokens"> | <img src="docs/images/tela-escuro.png" alt="Execução de 50 passos no tema escuro"> |

## Linha de comando

```bash
delco login --url http://localhost:8080 --email operador@delco.dev   # salva um token pessoal
delco workflows
delco show relatorio_vendas_diario      # agenda, parâmetros e tarefas
delco versions relatorio_vendas_diario  # histórico de versões e o que mudou
delco trigger relatorio_vendas_diario -p pasta_saida=/tmp/rel --wait  # código de saída 1 se falhar
delco trigger relatorio_vendas_diario -p minimo_pedidos=abc --dry-run # só confere os parâmetros
delco backfill relatorio_vendas_diario --from 2026-10-01 --to 2026-10-07 --dry-run
delco runs --status failed --since 7 --json
delco logs 118 extrair_pedidos --follow --level error
delco retry 118                       # refaz só o que falhou
delco retry 118 --from consolidar     # refaz a partir de uma tarefa
delco mark 118 extrair_pedidos --status succeeded   # marca e segue a execução
delco note 118 "reprocessado depois do ajuste no fornecedor"
delco cancel 119
delco pause sincronizar_catalogo
delco workers pause c41e02d9a7f3-7    # termina o que está rodando e não pega mais nada
delco upcoming --hours 24
delco integrations
delco export execucoes.csv --status failed --since 30
```

Em servidores e CI, prefira `DELCO_URL` e `DELCO_TOKEN` (token pessoal criado em **Minha conta**). Os comandos de lista aceitam `--json`.

Ferramentas locais e de servidor:

| Comando | O que faz |
|---|---|
| `delco new nome [--schedule "0 7 * * *"]` | Cria `workflows/nome.py` a partir de um modelo, já com parâmetros e duas tarefas |
| `delco dev run nome -p chave=valor` | Roda o workflow no terminal, sem banco, Redis nem workers |
| `delco validate [pasta]` | Confere os arquivos de workflow sem rodar nada |
| `delco doctor [--json] [--strict]` | Confere configuração, MySQL, Redis, migrações, agendadores, workers, filas sem worker e contas de demonstração |
| `delco config [--json]` | Mostra a configuração em uso, com senhas e chaves mascaradas |
| `delco healthcheck api\|scheduler\|worker` | Usado pelo Docker para saber se cada processo está vivo |

Os comandos de servidor são comandos do Django. Os dois jeitos funcionam:

| Atalho | Comando do Django | O que faz |
|---|---|---|
| `delco api` | `python manage.py migrate` e depois `daphne -b 0.0.0.0 -p 8000 delco.asgi:application` | Sobe API, WebSocket e painel |
| `delco scheduler` | `python manage.py scheduler` | Sobe o agendador |
| `delco worker --concurrency 8` | `python manage.py worker --concurrency 8` | Sobe um worker |
| `delco migrate` | `python manage.py migrate` | Cria ou atualiza as tabelas (o atalho usa trava consultiva) |
| `delco seed` | `python manage.py seed` | Contas e histórico de demonstração |
| `delco users create ...` | `python manage.py createuser ...` | Cria um usuário |

## API

Todas as rotas ficam em `/api`, respondem JSON e exigem `Authorization: Bearer <token>`, exceto login e saúde. Erros sempre no mesmo formato: `{ "code": "...", "message": "...", "fields": { "campo": "mensagem" } }`.

| Grupo | Rotas principais |
|---|---|
| Acesso | `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` · `PUT /auth/me/password` · `GET` e `PATCH /auth/me/preferences` · `GET`, `POST` e `DELETE /tokens` |
| Workflows | `GET /workflows` · `GET /workflows/{nome}` · `/grid` · `/versions` · `/source` · `POST /workflows/{nome}/runs` (com `dry_run`) · `POST /workflows/{nome}/backfill` · `/pause` e `/resume` · `POST /workflows/bulk` |
| Execuções | `GET /runs` (filtros `status`, `workflow`, `trigger`, `since`, `q`) · `GET /runs/export` · `GET`, `PATCH` e `DELETE /runs/{id}` · `POST /runs/{id}/cancel` · `/retry` · `POST /runs/bulk` · `GET /runs/{id}/tasks/{tarefa}` · `POST /runs/{id}/tasks/{tarefa}/mark` · `GET /attempts/{id}/logs` |
| Monitoramento | `GET /overview` · `GET /insights?days=7` · `GET /upcoming?hours=24` · `GET /workers` · `POST /workers/{id}/pause` e `/resume` · `GET /queues` · `GET /health` · `GET /metrics` · `WS /ws` |
| Integrações | `GET` e `POST /integrations` · `GET`, `PATCH` e `DELETE /integrations/{id}` · `POST /integrations/{id}/test` · `GET /integrations/{id}/deliveries` · `POST /integrations/{id}/deliveries/{entrega}/retry` · `POST /hooks/{token}` |
| Administração | `GET`, `POST` e `PATCH /users` · `GET /audit` (filtros `action`, `user`, `q`, `since`) · `GET /audit/export` · `GET /system` · `POST /system/housekeeping` |

Parâmetros inválidos voltam com `422` e o erro em cada campo (`{"fields": {"params.dias": "Precisa ser no máximo 30."}}`).

```bash
TOKEN=$(curl -s -X POST localhost:8080/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"operador@delco.dev","password":"Delco@2026"}' | jq -r .token)

curl -s -X POST localhost:8080/api/workflows/ola_delco/runs -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"params": {"nome": "Delco"}}'
```

## Integrações

Em **Integrações** (menu lateral), um administrador conecta o Delco a outros serviços. Operadores veem as integrações e o histórico de entregas.

| Tipo | Para que serve | O que configurar |
|---|---|---|
| Slack | Mensagem num canal | URL do webhook de entrada do canal |
| Discord | Mensagem num canal | URL do webhook do canal |
| Microsoft Teams | Mensagem num canal | URL do webhook de entrada ou de um fluxo do Workflows |
| Google Chat | Mensagem num espaço | URL do webhook do espaço |
| Telegram | Mensagem num grupo ou canal | Token do bot (do @BotFather) e o ID do chat |
| E-mail | Mensagem para uma lista de pessoas | Destinatários; o servidor vem de `DELCO_SMTP_*` |
| Webhook genérico | Avisar outro sistema | Qualquer URL `https://`; o corpo é JSON assinado |
| Gatilho por URL | Disparar um workflow de fora | O workflow; o Delco gera a URL secreta |

Cada notificação escolhe os eventos (**começou**, **concluída**, **falhou**, **cancelada** e **voltou a funcionar**, que só chega quando uma execução dá certo logo depois de uma falha) e, se quiser, só alguns workflows. A entrega é criada na mesma transação que muda o estado da execução, então nada se perde se um processo cair; o agendador envia e, se o destino recusar, tenta de novo até 5 vezes (10 s, 20 s, 40 s e 80 s depois). **Enviar teste** confere a configuração na hora e entregas que desistiram podem ser **reenviadas** pelo histórico.

O webhook genérico recebe um `POST` com os cabeçalhos `X-Delco-Event`, `X-Delco-Delivery` e `X-Delco-Signature: sha256=<hex>` (HMAC-SHA256 do corpo com o segredo da integração):

```json
{"event": "run.failed", "delivery_id": 42, "sent_at": "2026-10-27T13:00:05+00:00",
 "run": {"id": 118, "workflow": "sincronizar_catalogo", "status": "failed", "trigger": "schedule",
         "duration_ms": 15200, "tasks_total": 7, "tasks_failed": 1, "url": "http://localhost:8080/#/runs/118"}}
```

O gatilho por URL mostra o endereço uma única vez, logo depois de criado (só o SHA-256 do token fica no banco). Com **Exigir assinatura**, o Delco mostra também um segredo e passa a recusar chamadas sem `X-Delco-Signature: sha256=<hex>` (ou `X-Hub-Signature-256`, como manda o GitHub). Cada gatilho aceita até `DELCO_HOOK_RATE_LIMIT` chamadas por minuto. Num GitHub Actions, por exemplo:

```bash
curl -X POST "$DELCO_HOOK_URL" -H 'Content-Type: application/json' \
  -d '{"params": {"pasta_saida": "/tmp/rel"}, "note": "Build ${{ github.run_number }}"}'
```

As integrações de demonstração apontam para endereços fictícios: troque a URL por uma de verdade para receber as mensagens.

## Configuração

Todas as variáveis ficam no arquivo `.env` (modelo em [`.env.example`](.env.example)). Valores errados não viram traceback: o processo para e lista todos os problemas de uma vez. `delco config` mostra o que está valendo.

| Variável | Padrão | Para que serve |
|---|---|---|
| `DELCO_PORT` | `8080` | Porta do painel e da API no seu computador |
| `DELCO_DB_USER` / `DELCO_DB_PASSWORD` / `DELCO_DB_NAME` | `delco` / gerada / `delco` | Acesso ao MySQL |
| `DELCO_SECRET_KEY` | gerada pelo `iniciar` | `SECRET_KEY` do Django |
| `DELCO_SEED` | `true` | Contas e histórico de demonstração num banco vazio |
| `DELCO_ALLOWED_HOSTS` | `*` | O domínio do painel em produção |
| `DELCO_PUBLIC_URL` | `http://localhost:8080` | Endereço usado nos links das notificações |
| `DELCO_TIMEZONE` | `America/Sao_Paulo` | Fuso padrão |
| `DELCO_RETENTION_DAYS` | `30` | Execuções terminadas mais antigas são apagadas |
| `DELCO_LOG_LEVEL` / `DELCO_LOG_FORMAT` | `INFO` / `text` | Nível e formato dos logs (`json` para Loki, Elastic, Datadog…) |
| `DELCO_WORKER_QUEUES` / `DELCO_WORKER_CONCURRENCY` | `default,critical` / `4` | Filas e vagas de cada worker |
| `DELCO_LEASE_SECONDS` / `DELCO_HEARTBEAT_SECONDS` | `30` / `5` | Quanto tempo sem sinal até a tarefa ser considerada perdida (o lease precisa ser pelo menos o dobro do heartbeat) |
| `DELCO_WEBHOOK_TIMEOUT` | `10` | Segundos de espera por resposta dos destinos das notificações |
| `DELCO_HOOK_RATE_LIMIT` / `DELCO_LOGIN_RATE_LIMIT` | `60` / `30` | Chamadas por minuto em cada gatilho por URL e tentativas de login por IP (`0` desliga) |
| `DELCO_METRICS_TOKEN` | gerado pelo `iniciar` | Token aceito em `GET /api/metrics` |
| `DELCO_MAX_BACKFILL_RUNS` | `366` | Máximo de execuções num reprocessamento |
| `DELCO_SMTP_HOST` / `PORT` / `USER` / `PASSWORD` / `FROM` / `SECURITY` | vazio / `587` / … / `starttls` | Servidor de e-mail para os avisos por e-mail |

A lista completa, com os limites de log, resultado e sessão, está no manual.

## Operação

| Ferramenta | Para que serve |
|---|---|
| Tela **Sistema** (administrador) e `delco doctor` | Mostram o que está quebrado e como corrigir: banco, migrações, Redis, agendadores, workers, filas que nenhum worker consome, entregas com falha, contas de demonstração com a senha pública e configuração de produção |
| `GET /api/health` | `status`, `database`, `redis` e `scheduler`; responde 200 enquanto o banco estiver de pé |
| `GET /api/metrics` | Execuções ativas e terminadas, último sucesso de cada workflow, tarefas por fila, vagas, workers, agendadores, entregas e arquivos com erro, no formato do Prometheus |
| `delco healthcheck` | Healthcheck do Docker para API, agendador e workers (já ligado no `docker-compose.yml`) |
| Pausar worker | Tira um worker de circulação sem derrubar o que está rodando |
| Limpeza | Automática a cada 10 minutos e manual pela tela Sistema |

## Segurança

| Regra | Como funciona |
|---|---|
| Senhas | Hasher scrypt do Django, com sal. Mínimo de 8 caracteres com letras e números. Cinco erros seguidos bloqueiam a conta por 5 minutos |
| Tokens | Opacos e aleatórios; só o SHA-256 fica no banco. Sessão do painel vale 12 horas; tokens pessoais têm validade escolhida e podem ser revogados |
| Limite de chamadas | Login limitado por IP e gatilho por URL limitado por token, com contagem no Redis |
| Integrações | URLs de webhook e o token do bot do Telegram ficam ocultos no painel e na API; o webhook genérico é assinado com HMAC-SHA256; o gatilho por URL guarda só o SHA-256 do token, pode exigir assinatura e pode ser desativado na hora |
| Parâmetros | `Param(secret=True)` mascara o valor no painel, na API e na auditoria |
| Papéis | Leitura, Operador e Administrador, checados em cada rota pelas permissões do DRF |
| Isolamento | Cada tentativa roda num processo filho; travar, estourar memória ou chamar `os._exit` derruba só a tarefa |
| Auditoria | Logins, disparos, reprocessamentos, marcações, cancelamentos, retomadas, exclusões, pausas de workflows e workers, tokens, usuários, integrações e limpezas ficam registrados, com filtros e exportação em CSV |
| Cabeçalhos | `Content-Security-Policy` (CSP nativa do Django 6), `X-Frame-Options`, `nosniff` e `Cache-Control: no-store` na API |
| Dados | Nenhum dado pessoal real no repositório. Segredos só no `.env`, que fica fora do Git |

## Estrutura

```
Workflow-Orchestration-Engine/
├── delco/
│   ├── sdk.py · params.py       definição de workflows, Param, Skip, Fail e execução local
│   ├── context.py               o objeto ctx
│   ├── scheduler.py             agendador, despacho, tempo limite e recuperação
│   ├── worker.py · runner.py    worker pool, pausa e processo filho de cada tentativa
│   ├── models.py                modelos do Django (as 12 tabelas)
│   ├── migrations/              migrações do Django
│   ├── store.py                 transições de estado com o ORM (compare-and-set)
│   ├── bus.py                   Redis Streams, pub/sub, sinais e limites de chamadas
│   ├── integrations.py          Slack, Discord, Teams, Google Chat, Telegram, e-mail, webhooks e gatilho por URL
│   ├── doctor.py                verificações do delco doctor e da tela Sistema
│   ├── config.py · logs.py      configuração conferida e logs em texto ou JSON
│   ├── api/                     DRF (views, serializers, permissões), Channels, consultas e métricas
│   ├── management/commands/     worker, scheduler, seed e createuser
│   ├── settings.py · urls.py    configuração e rotas do Django
│   ├── asgi.py                  HTTP + WebSocket para o Daphne
│   └── cli.py                   linha de comando (servidor, cliente e ferramentas locais)
├── manage.py                    utilitário do Django
├── web/                         painel (HTML, CSS e JavaScript sem build)
├── workflows/                   workflows de exemplo (montada nos contêineres)
├── tests/                       pytest com MySQL e Redis reais
├── docs/                        manuais em PDF, fontes dos manuais e imagens
├── Makefile                     comandos do dia a dia
└── docker-compose.yml
```

## Testes

```bash
docker run -d --name delco-mysql-teste -p 127.0.0.1:3307:3306 -e MYSQL_ROOT_PASSWORD=delco \
  -e MYSQL_DATABASE=delco_test -e MYSQL_USER=delco -e MYSQL_PASSWORD=delco mysql:8.4
docker run -d --name delco-redis-teste -p 127.0.0.1:6380:6379 redis:7-alpine
pip install -r requirements-dev.txt && pip install -e .
DELCO_TEST_DATABASE_URL=mysql://delco:delco@127.0.0.1:3307/delco_test \
DELCO_TEST_REDIS_URL=redis://127.0.0.1:6380/15 python -m pytest
python manage.py makemigrations --check   # modelos e migrações em dia
ruff check .                              # lint
```

No Linux e no macOS, o `mysqlclient` precisa da biblioteca cliente do MySQL para instalar (no Ubuntu: `sudo apt install pkg-config libmysqlclient-dev`); no Windows o pip baixa o pacote pronto.

São 103 testes com cobertura acima de 90% (mínimo exigido: 80%). Eles sobem workers e agendadores de verdade e cobrem: dependências e paralelismo, retentativas com espera, regras de disparo, tempo limite da tarefa e da execução, `skip`, `Fail` e `retry_on`, processo que morre, cancelamento no meio da tarefa, **queda do worker no passo 23 de 50 com retomada só daquele passo**, mensagem repetida, mensagem perdida, Redis apagado, dois agendadores disputando o mesmo horário, worker pausado, desligamento gracioso, parâmetros tipados, reprocessamento por período, marcação manual, ações em lote, versões e código-fonte, API, WebSocket, OpenAPI, papéis, tokens, preferências, auditoria, limites de chamadas, CLI, `delco dev run`, `delco doctor`, métricas, **notificações assinadas entregues a um servidor HTTP de verdade**, e-mail por SMTP, Telegram, "voltou a funcionar", gatilho por URL assinado, novas tentativas quando o destino cai, desempenho, próximas execuções e exportação em CSV.

O workflow [`ci.yml`](.github/workflows/ci.yml) roda o Ruff, confere a sintaxe do painel, executa os workflows de exemplo com `delco dev run`, roda a suíte com MySQL 8.4 e Redis e monta e testa a imagem Docker a cada envio e uma vez por dia. O Dependabot propõe atualizações de dependências, da imagem e das actions.

## Licença

[MIT](LICENSE). Use, copie, altere e distribua à vontade. As fontes Instrument Sans e JetBrains Mono são distribuídas sob a [SIL Open Font License](web/fonts/OFL.txt).

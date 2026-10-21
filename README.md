<p align="center">
  <img src="docs/images/logo.png" width="200" alt="Delco">
</p>

<h1 align="center">Delco<br><sub><sup><sup><sub>Copyright (©) 2026 Erik Pimenta</sub></sup></sup></sub></h1>

<p align="center">
  Motor de orquestração de workflows: você escreve o pipeline em Python, o Delco agenda, distribui as tarefas entre workers, repete o que falhar e, se um servidor cair no meio, continua exatamente de onde parou.<br>
  Pronto para usar, estudar ou adaptar.
</p>

<p align="center">
  Python 3.13 · Django 6 · Django REST Framework · Django Channels · MySQL 8.4 · Redis Streams · WebSockets · Docker Compose · Pytest
</p>

---

> **Manual completo:** [`Manual-Delco.pdf`](Manual-Delco.pdf) explica a arquitetura, a instalação, como escrever workflows, cada tela do painel, a CLI, a API, a configuração, a operação em produção e a solução de problemas, passo a passo (cópia em [`docs/`](docs/Manual-Delco.pdf)).

> **Banco de dados:** [`Banco-de-Dados-Delco.pdf`](Banco-de-Dados-Delco.pdf) traz o diagrama de relacionamentos, todas as tabelas com campos, tipos e chaves, a máquina de estados das execuções e tarefas e como preparar o MySQL.

<p align="center">
  <img src="docs/images/tela-painel.png" alt="Visão geral com execuções ativas, tarefas por hora, workers e execuções recentes">
</p>

## O que o Delco faz

- **Workflows como código:** cada arquivo `.py` da pasta `workflows/` define um grafo de tarefas com dependências, parâmetros, agenda cron, fila, tempo limite e política de retentativas.
- **Agendamento:** expressões cron com fuso horário, pausa e retomada pelo painel, limite de execuções simultâneas por workflow e disparo manual com parâmetros.
- **Fila com worker pool:** as tarefas prontas vão para Redis Streams com grupo de consumidores; cada worker tem N vagas e roda cada tentativa num processo filho isolado.
- **Paralelismo:** tarefas sem dependência entre si rodam ao mesmo tempo, em quantos workers houver.
- **Retentativas:** espera exponencial com variação aleatória, teto configurável e regras de disparo (`all_success`, `all_done`, `one_failed`).
- **Retomada após queda:** o estado vive no MySQL. Worker que some perde a posse da tarefa quando o lease vence, e só o passo interrompido volta para a fila.
- **Painel em tempo real:** grafo ao vivo, grade de execuções, linha do tempo, logs acompanhando a tarefa enquanto ela roda, quadro de vagas dos workers, tema claro e escuro.
- **CLI e API:** tudo o que o painel faz também está na linha de comando (`delco trigger ... --wait`) e na API REST em Django REST Framework, documentada em Swagger.

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
```

| Processo | Comando | Responsabilidade |
|---|---|---|
| API | `delco api` | Daphne (ASGI) com o Django: REST no DRF, WebSocket no Channels, painel pelo WhiteNoise, Swagger e migrações |
| Agendador | `delco scheduler` (`manage.py scheduler`) | Lê a pasta de workflows, cria execuções agendadas, libera tarefas cujas dependências terminaram, recupera leases vencidos e republica mensagens perdidas |
| Worker | `delco worker` (`manage.py worker`) | Consome as filas, assume cada tarefa no banco, roda a tentativa num processo filho com heartbeat, tempo limite e cancelamento |

Pode haver vários agendadores e quantos workers quiser: as linhas são travadas com `select_for_update(skip_locked=True)` do ORM (`FOR UPDATE SKIP LOCKED` no MySQL 8) e as execuções agendadas têm chave única por horário, então nada roda em dobro.

### Como a retomada funciona

1. O agendador marca a tarefa como **na fila** no MySQL e só depois publica a mensagem no Redis.
2. O worker assume a tarefa travando a linha com `select_for_update()` e `status='queued'` no filtro. Se a mensagem chegar repetida, a segunda tentativa não encontra mais a tarefa na fila e é descartada.
3. Durante a tentativa, o worker renova o **lease** a cada heartbeat. Se o servidor cair, o lease vence e o agendador devolve a tarefa para nova tentativa.
4. Tarefas que já terminaram ficam com o resultado gravado e não rodam de novo. Num fluxo de 50 passos que cai no passo 23, só o passo 23 é refeito.
5. Se o Redis for apagado, nada se perde: o agendador percebe tarefas na fila sem mensagem e as republica.
6. `ctx.idempotency_key` é igual em todas as tentativas da mesma tarefa, para você não repetir efeitos externos (cobranças, e-mails, inserts).

### Como o Django está organizado

| Peça | Onde | O que faz |
|---|---|---|
| Modelos e migrações | `delco/models.py` · `delco/migrations/` | As 10 tabelas com CHECK, índices, chave única por horário (índice funcional) e `ON DELETE` no próprio banco (`DB_CASCADE` e `DB_SET_NULL` do Django 6.1) |
| Transições de estado | `delco/store.py` | ORM com `transaction.atomic()`, `select_for_update(skip_locked=True)` e `UPDATE` condicional (compare-and-set) |
| API REST | `delco/api/views.py` · `serializers.py` · `auth.py` | Django REST Framework com autenticação por token, permissões por papel e erros num formato único |
| Documentação | `/api/docs` | OpenAPI gerado pelo drf-spectacular, Swagger servido sem CDN |
| Tempo real | `delco/api/consumers.py` · `events.py` | Django Channels: o evento chega do Redis e vai para os grupos `delco.eventos` e `delco.tentativa.<id>` |
| Processos | `delco/management/commands/` | `worker`, `scheduler`, `seed` e `createuser` são comandos do Django; `delco <comando>` é só um atalho |
| Configuração | `delco/settings.py` | Tudo vem das variáveis `DELCO_*`; MySQL pelo mysqlclient com a sessão em UTC, CSP nativa do Django 6 e WhiteNoise para o painel |

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

Na primeira vez o script cria o arquivo `.env` com uma senha aleatória para o banco, sobe os contêineres (MySQL, Redis, API, agendador e dois workers) e abre **http://localhost:8080**.

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
```

## Contas de demonstração

Todas usam a senha **`Delco@2026`**. Elas existem só para testes, para qualquer pessoa entrar logo depois de baixar o projeto. Os nomes são fictícios e os e-mails usam o domínio de exemplo `delco.dev`.

| Papel | E-mail | O que pode fazer |
|---|---|---|
| Administrador | `admin@delco.dev` | Tudo, incluindo usuários e auditoria |
| Operador | `operador@delco.dev` | Disparar, cancelar, retomar, pausar e consultar |
| Leitura | `leitura@delco.dev` | Só consultar |

Na primeira subida o Delco cria uma semana de histórico fictício para o painel não abrir vazio. Para usar de verdade, suba com `DELCO_SEED=false` num banco vazio e crie seu administrador com `docker compose exec api python manage.py createuser --role admin --email voce@empresa.com --name "Seu Nome"` (ou o atalho `delco users create`).

## Escrevendo um workflow

Crie um arquivo em `workflows/`. O agendador o encontra em até 30 segundos (sem reiniciar nada).

```python
from delco import Workflow

vendas = Workflow(
    "relatorio_vendas",
    description="Extrai, agrega e publica o relatório do dia.",
    schedule="0 7 * * *",              # cron de 5 campos, no fuso abaixo
    timezone="America/Sao_Paulo",
    params={"dias": 1},                # padrões; o disparo manual pode sobrescrever
    max_active_runs=1,
)

@vendas.task(retries=3, retry_delay=10, timeout=120)
def extrair(ctx):
    ctx.log(f"Lendo {ctx.params['dias']} dia(s)")
    return {"linhas": 1200}             # vai para ctx.upstream das próximas tarefas

@vendas.task(depends_on=[extrair], queue="critical")
def publicar(ctx):
    linhas = ctx.upstream["extrair"]["linhas"]
    ctx.progress(50, "metade")
    ctx.log(f"{linhas} linhas publicadas · chave {ctx.idempotency_key}")

@vendas.task(depends_on=[publicar], trigger_rule="all_done")
def limpar(ctx):
    ...                                 # roda mesmo se publicar falhar
```

| Opção da tarefa | Padrão | O que faz |
|---|---|---|
| `depends_on` | `[]` | Tarefas (função ou nome) que precisam terminar antes |
| `retries` | `2` | Novas tentativas depois da primeira falha |
| `retry_delay` / `backoff` | `5` s / `2` | Espera = `retry_delay × backoff^(tentativa-1)`, com ±10% de variação |
| `timeout` | `300` s | Tempo máximo da tentativa; `None` para sem limite |
| `queue` | `default` | Fila (workers escolhem quais filas consomem) |
| `trigger_rule` | `all_success` | `all_done` roda sempre; `one_failed` só se algo falhar |

Para gerar tarefas em laço, use `wf.add_task(nome, funcao, depends_on=[...])`. Funções `async def` também funcionam. Confira tudo sem rodar nada com `delco validate workflows`.

Exemplos prontos na pasta [`workflows/`](workflows): `ola_delco` (mínimo), `relatorio_vendas_diario` (leque paralelo por região), `sincronizar_catalogo` (fornecedor instável, retentativas e regras de disparo), `backup_noturno` (fila `critical`, arquivos reais) e `migracao_em_50_passos` (para testar a retomada derrubando um worker no meio).

| Tela | |
|---|---|
| <img src="docs/images/tela-workflows.png" alt="Lista de workflows com agenda e últimas execuções"> | <img src="docs/images/tela-grade.png" alt="Grade de execuções por tarefa"> |
| <img src="docs/images/tela-tarefa.png" alt="Gaveta da tarefa com log, resultado e tentativas"> | <img src="docs/images/tela-falha.png" alt="Execução com falha e as quatro tentativas registradas"> |
| <img src="docs/images/tela-workers.png" alt="Quadro de vagas dos workers e filas"> | <img src="docs/images/tela-escuro.png" alt="Painel no tema escuro"> |

## Linha de comando

```bash
delco login --url http://localhost:8080 --email operador@delco.dev   # salva um token pessoal
delco workflows
delco trigger relatorio_vendas_diario -p pasta_saida=/tmp/rel --wait  # código de saída 1 se falhar
delco runs --status failed
delco logs 118 extrair_pedidos
delco retry 118                       # refaz só o que falhou
delco retry 118 --from consolidar     # refaz a partir de uma tarefa
delco cancel 119
delco pause sincronizar_catalogo
```

Em servidores e CI, prefira `DELCO_URL` e `DELCO_TOKEN` (token pessoal criado em **Minha conta**).

Os comandos de servidor são comandos do Django. Os dois jeitos funcionam:

| Atalho | Comando do Django | O que faz |
|---|---|---|
| `delco api` | `python manage.py migrate` e depois `daphne -b 0.0.0.0 -p 8000 delco.asgi:application` | Sobe API, WebSocket e painel (em desenvolvimento, `python manage.py runserver` também serve tudo pelo Daphne) |
| `delco scheduler` | `python manage.py scheduler` | Sobe o agendador |
| `delco worker --concurrency 8` | `python manage.py worker --concurrency 8` | Sobe um worker |
| `delco migrate` | `python manage.py migrate` | Cria ou atualiza as tabelas (o atalho usa trava consultiva) |
| `delco seed` | `python manage.py seed` | Contas e histórico de demonstração |
| `delco users create ...` | `python manage.py createuser ...` | Cria um usuário |

## API

Todas as rotas ficam em `/api`, respondem JSON e exigem `Authorization: Bearer <token>`, exceto login e saúde. Erros sempre no mesmo formato: `{ "code": "...", "message": "...", "fields": { "campo": "mensagem" } }`.

| Grupo | Rotas principais |
|---|---|
| Acesso | `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` · `PUT /auth/me/password` · `GET`, `POST` e `DELETE /tokens` |
| Workflows | `GET /workflows` · `GET /workflows/{nome}` · `GET /workflows/{nome}/grid` · `POST /workflows/{nome}/runs` · `POST /workflows/{nome}/pause` e `/resume` |
| Execuções | `GET /runs` · `GET /runs/{id}` · `POST /runs/{id}/cancel` · `POST /runs/{id}/retry` · `GET /runs/{id}/tasks/{tarefa}` · `GET /attempts/{id}/logs` |
| Monitoramento | `GET /overview` · `GET /workers` · `GET /queues` · `GET /health` · `WS /ws` |
| Administração | `GET`, `POST` e `PATCH /users` · `GET /audit` |

```bash
TOKEN=$(curl -s -X POST localhost:8080/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"operador@delco.dev","password":"Delco@2026"}' | jq -r .token)

curl -s -X POST localhost:8080/api/workflows/ola_delco/runs -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"params": {"nome": "Delco"}}'
```

## Configuração

Todas as variáveis ficam no arquivo `.env` (modelo em [`.env.example`](.env.example)).

| Variável | Padrão | Para que serve |
|---|---|---|
| `DELCO_PORT` | `8080` | Porta do painel e da API no seu computador |
| `DELCO_DB_USER` / `DELCO_DB_PASSWORD` / `DELCO_DB_NAME` | `delco` / gerada / `delco` | Acesso ao MySQL |
| `DELCO_SEED` | `true` | Contas e histórico de demonstração num banco vazio |
| `DELCO_TIMEZONE` | `America/Sao_Paulo` | Fuso padrão |
| `DELCO_WORKER_QUEUES` | `default,critical` | Filas que cada worker consome |
| `DELCO_WORKER_CONCURRENCY` | `4` | Tarefas simultâneas por worker |
| `DELCO_RETENTION_DAYS` | `30` | Execuções terminadas mais antigas são apagadas |
| `DELCO_LEASE_SECONDS` / `DELCO_HEARTBEAT_SECONDS` | `30` / `5` | Quanto tempo sem sinal até a tarefa ser considerada perdida |
| `DELCO_ALLOWED_HOSTS` | `*` | `ALLOWED_HOSTS` do Django: o domínio do painel em produção |
| `DELCO_SECRET_KEY` | sorteada | `SECRET_KEY` do Django (o Delco não assina cookies com ela) |

A lista completa, com os limites de log, resultado e sessão, está no manual.

## Segurança

| Regra | Como funciona |
|---|---|
| Senhas | Hasher scrypt do Django, com sal. Mínimo de 8 caracteres com letras e números. Cinco erros seguidos bloqueiam a conta por 5 minutos |
| Tokens | Opacos e aleatórios; só o SHA-256 fica no banco. Sessão do painel vale 12 horas; tokens pessoais têm validade escolhida e podem ser revogados |
| Papéis | Leitura, Operador e Administrador, checados em cada rota pelas permissões do DRF |
| Isolamento | Cada tentativa roda num processo filho; travar, estourar memória ou chamar `os._exit` derruba só a tarefa |
| Auditoria | Logins, disparos, cancelamentos, retomadas, pausas, tokens e usuários ficam registrados |
| Cabeçalhos | `Content-Security-Policy` (CSP nativa do Django 6), `X-Frame-Options`, `nosniff` e `Cache-Control: no-store` na API |
| Dados | Nenhum dado pessoal real no repositório. Segredos só no `.env`, que fica fora do Git |

## Estrutura

```
Workflow-Orchestration-Engine/
├── delco/
│   ├── sdk.py · context.py      definição de workflows e o objeto ctx
│   ├── scheduler.py             agendador, despacho e recuperação
│   ├── worker.py · runner.py    worker pool e processo filho de cada tentativa
│   ├── models.py                modelos do Django (as 10 tabelas)
│   ├── migrations/              migrações do Django
│   ├── store.py                 transições de estado com o ORM (compare-and-set)
│   ├── bus.py                   Redis Streams, pub/sub e sinal de despertar
│   ├── api/                     DRF (views, serializers, permissões), Channels e consultas do painel
│   ├── management/commands/     worker, scheduler, seed e createuser
│   ├── settings.py · urls.py    configuração e rotas do Django
│   ├── asgi.py                  HTTP + WebSocket para o Daphne
│   └── cli.py                   linha de comando (atalhos e cliente da API)
├── manage.py                    utilitário do Django
├── web/                         painel (HTML, CSS e JavaScript sem build)
├── workflows/                   workflows de exemplo (montada nos contêineres)
├── tests/                       pytest com MySQL e Redis reais
├── docs/                        manuais em PDF e imagens
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
```

No Linux e no macOS, o `mysqlclient` precisa da biblioteca cliente do MySQL para instalar (no Ubuntu: `sudo apt install pkg-config libmysqlclient-dev`); no Windows o pip baixa o pacote pronto.

São 77 testes com cobertura acima de 90% (mínimo exigido: 80%). Eles sobem workers e agendadores de verdade e cobrem: dependências e paralelismo, retentativas com espera, regras de disparo, tempo limite, processo que morre, cancelamento no meio da tarefa, **queda do worker no passo 23 de 50 com retomada só daquele passo**, mensagem repetida, mensagem perdida, Redis apagado, dois agendadores disputando o mesmo horário, desligamento gracioso, API, WebSocket, OpenAPI, papéis, tokens e CLI.

O workflow [`ci.yml`](.github/workflows/ci.yml) roda a suíte com MySQL 8.4 e Redis e monta a imagem Docker a cada envio e uma vez por dia.

## Licença

[MIT](LICENSE). Use, copie, altere e distribua à vontade. A fonte Public Sans é distribuída sob a [SIL Open Font License](web/fonts/OFL.txt).

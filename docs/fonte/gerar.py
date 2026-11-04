from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

FONTE = Path(__file__).resolve().parent
RAIZ = FONTE.parent.parent
DIAGRAMAS = FONTE / "diagramas"
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "delco.settings")

TABELAS = {
    "users": ("Acesso", "Pessoas que entram no painel e na API.", {
        "email": "E-mail de login (único, sem diferenciar maiúsculas)",
        "name": "Nome mostrado no painel e na auditoria",
        "password_hash": "Senha com o hasher scrypt do Django",
        "role": "admin, operator ou viewer",
        "is_active": "Conta desativada não entra e perde os tokens",
        "failed_logins": "Erros de senha seguidos",
        "locked_until": "Bloqueio temporário depois de muitos erros",
        "last_login_at": "Último login com sucesso",
        "preferences": "Preferências do painel: tema, paleta, fuso, densidade, itens por página e avisos (1.2.0)",
    }),
    "api_tokens": ("Acesso", "Sessões do painel e tokens pessoais; só o SHA-256 fica gravado.", {
        "user_id": "Dono do token",
        "name": "Nome dado pelo usuário (ou Sessão no painel)",
        "kind": "session ou personal",
        "token_hash": "SHA-256 do token (o valor nunca é gravado)",
        "prefix": "Começo do token, para reconhecer na lista",
        "last_used_at": "Último uso (atualizado no máximo a cada minuto)",
        "expires_at": "Validade; vazio = sem validade",
        "revoked_at": "Quando foi revogado",
    }),
    "audit_log": ("Acesso", "Registro de tudo que muda algo, guardado por 180 dias.", {
        "user_id": "Quem fez (vazio quando foi o próprio sistema)",
        "action": "Ação, como run.trigger, run.backfill, run.mark ou worker.pause",
        "target": "Alvo, como run:118 ou workflow:ola_delco",
        "details": "Detalhes da ação em JSON (parâmetros secretos mascarados)",
    }),
    "workflows": ("Definições", "Um registro por workflow encontrado na pasta.", {
        "name": "Nome do workflow (único)",
        "description": "Descrição do arquivo",
        "schedule": "Cron de 5 campos; vazio = só manual",
        "timezone": "Fuso da agenda",
        "max_active_runs": "Execuções simultâneas permitidas",
        "default_params": "Valores padrão dos parâmetros",
        "params_schema": "Tipo, padrão, opções e limites de cada parâmetro (1.2.0)",
        "tags": "Etiquetas",
        "source_file": "Arquivo relativo à pasta de workflows",
        "source_code": "Código do arquivo na última leitura, mostrado no painel (1.2.0)",
        "source_hash": "SHA-256 do código (1.2.0)",
        "current_version_id": "Versão atual do grafo",
        "is_paused": "Agenda pausada",
        "is_active": "Falso quando o arquivo saiu da pasta",
        "next_run_at": "Próximo horário agendado",
    }),
    "workflow_versions": ("Definições", "Cada mudança no grafo vira uma versão; execuções antigas guardam a sua.", {
        "workflow_id": "Workflow dono da versão",
        "version": "Número sequencial por workflow",
        "fingerprint": "SHA-256 do grafo (tarefas, dependências e opções)",
        "graph": "Grafo completo em JSON, inclusive run_timeout e retry_on quando usados",
        "source_code": "Código do arquivo quando a versão nasceu (1.2.0)",
    }),
    "workflow_runs": ("Execução", "Uma execução de um workflow.", {
        "workflow_id": "Workflow",
        "version_id": "Versão do grafo usada",
        "status": "queued, running, succeeded, failed ou cancelled",
        "trigger": "schedule, manual, api, cli, webhook ou backfill (1.2.0)",
        "triggered_by_id": "Quem disparou",
        "logical_date": "Horário agendado que a execução representa",
        "params": "Parâmetros já validados",
        "note": "Observação (editável)",
        "error": "Motivo da falha da execução, como o tempo limite (1.2.0)",
        "started_at": "Quando saiu da fila",
        "finished_at": "Quando terminou",
    }),
    "task_instances": ("Execução", "Uma tarefa dentro de uma execução.", {
        "run_id": "Execução",
        "task_name": "Nome da tarefa",
        "status": "pending, queued, running, retry_wait, succeeded, failed, skipped ou cancelled",
        "upstream": "Tarefas das quais depende",
        "trigger_rule": "all_success, all_done ou one_failed",
        "queue": "Fila do Redis",
        "retries": "Novas tentativas permitidas",
        "max_attempts": "Total de tentativas permitidas",
        "retry_delay": "Espera base entre tentativas (s)",
        "retry_backoff": "Multiplicador da espera",
        "max_retry_delay": "Teto da espera (s)",
        "timeout_seconds": "Tempo limite da tentativa",
        "attempts": "Tentativas feitas",
        "worker_id": "Worker com a posse da tarefa",
        "lease_expires_at": "Fim da posse; renovado a cada heartbeat",
        "queue_message_id": "ID da mensagem no Redis Stream",
        "cancel_requested": "Pedido de parada pendente",
        "progress": "Andamento de 0 a 100",
        "output": "Resultado da tarefa (vai para ctx.upstream)",
        "error": "Erro ou motivo da última tentativa",
        "ready_at": "Quando a nova tentativa pode começar",
        "queued_at": "Quando foi para a fila",
        "started_at": "Início da primeira tentativa",
        "finished_at": "Fim",
    }),
    "task_attempts": ("Execução", "Cada tentativa de uma tarefa.", {
        "task_instance_id": "Tarefa",
        "number": "Número da tentativa",
        "worker_id": "Worker que rodou",
        "status": "running, succeeded, failed, timed_out, lost, cancelled ou skipped (1.2.0)",
        "started_at": "Início",
        "finished_at": "Fim",
        "duration_ms": "Duração",
        "error": "Erro resumido",
        "traceback": "Traceback completo do Python",
    }),
    "task_logs": ("Execução", "Linhas de log de cada tentativa.", {
        "attempt_id": "Tentativa",
        "logged_at": "Momento da linha",
        "level": "debug, info, warning ou error",
        "message": "Texto (até 8000 caracteres)",
    }),
    "workers": ("Execução", "Processos worker conectados.", {
        "id": "Nome do worker (host + pid ou DELCO_WORKER_NAME)",
        "hostname": "Máquina",
        "pid": "Processo",
        "queues": "Filas que consome",
        "concurrency": "Vagas",
        "version": "Versão do Delco",
        "status": "online, paused (1.2.0), stopping ou offline",
        "started_at": "Início",
        "last_heartbeat_at": "Último sinal",
        "stopped_at": "Quando parou",
    }),
    "integrations": ("Integrações", "Destinos de aviso e gatilhos por URL.", {
        "name": "Nome",
        "kind": "slack, discord, teams, google_chat, telegram (1.2.0), email (1.2.0), webhook ou trigger",
        "url": "URL do webhook (mascarada na API)",
        "secret": "Segredo do HMAC, token do bot do Telegram ou segredo da assinatura do gatilho",
        "token_hash": "SHA-256 do token do gatilho por URL",
        "token_prefix": "Começo do token do gatilho",
        "workflow_id": "Workflow que o gatilho dispara",
        "events": "Eventos assinados, inclusive run.recovered (1.2.0)",
        "workflows": "Só estes workflows (vazio = todos)",
        "config": "chat_id do Telegram, destinatários do e-mail e require_signature do gatilho (1.2.0)",
        "is_active": "Ligada ou desligada",
        "created_by_id": "Quem criou",
        "last_delivery_at": "Última entrega ou chamada",
        "last_status": "sent ou error",
    }),
    "integration_deliveries": ("Integrações", "Cada aviso a enviar, com novas tentativas.", {
        "integration_id": "Integração",
        "run_id": "Execução do evento",
        "event": "run.started, run.succeeded, run.failed, run.cancelled, run.recovered ou test",
        "status": "pending, sent ou failed",
        "attempts": "Tentativas feitas",
        "next_attempt_at": "Próxima tentativa",
        "response_code": "Código HTTP (250 no e-mail)",
        "error": "Erro da última tentativa",
        "payload": "Corpo do evento em JSON",
        "sent_at": "Quando foi entregue",
    }),
}
COMUNS = {"id": "Identificador", "created_at": "Criação", "updated_at": "Última mudança"}


def _tipo(field) -> str:
    from django.db import connection

    try:
        tipo = field.db_type(connection) or ""
    except Exception:
        tipo = ""
    tipo = tipo.replace(" AUTO_INCREMENT", "").replace("bigint AUTO", "bigint")
    return tipo.split("(")[0] + ("(" + tipo.split("(")[1] if "(" in tipo else "")


def _padrao(field) -> str:
    from django.db.models import NOT_PROVIDED

    db = getattr(field, "db_default", NOT_PROVIDED)
    if db is not NOT_PROVIDED:
        valor = getattr(db, "value", db)
        if hasattr(valor, "__class__") and valor.__class__.__name__ == "Now":
            return "now()"
        return html.escape(str(valor).lower() if isinstance(valor, bool) else str(valor))
    return ""


def tabelas_html() -> str:
    import django
    from django.apps import apps

    django.setup()
    modelos = {m._meta.db_table: m for m in apps.get_app_config("delco").get_models()}
    partes = []
    for nome, (area, resumo, desc) in TABELAS.items():
        m = modelos[nome]
        linhas = []
        for f in m._meta.concrete_fields:
            coluna = f.column
            chave = "PK" if f.primary_key else "FK" if f.is_relation else ("UK" if f.unique else "")
            linhas.append(
                f"<tr><td><code>{coluna}</code></td><td>{html.escape(_tipo(f))}</td>"
                f"<td>{'sim' if f.null else 'não'}</td><td>{chave}</td><td>{_padrao(f)}</td>"
                f"<td>{html.escape(desc.get(coluna, COMUNS.get(coluna, '')))}</td></tr>"
            )
        indices = [
            f"<li><code>{i.name}</code>: {', '.join(i.fields) if i.fields else 'expressão'}</li>" for i in m._meta.indexes
        ]
        restr = []
        for c in m._meta.constraints:
            tipo = "UNIQUE" if c.__class__.__name__ == "UniqueConstraint" else "CHECK"
            alvo = ", ".join(getattr(c, "fields", ()) or ()) or "expressão"
            restr.append(f"<li><code>{c.name}</code> ({tipo}): {html.escape(alvo)}</li>")
        partes.append(
            f'<section class="tabela-doc"><h2 id="t-{nome}"><code>{nome}</code></h2>'
            f'<p><span class="tag">{area}</span> {html.escape(resumo)} Modelo <code>{m.__name__}</code>.</p>'
            "<table><thead><tr><th>Campo</th><th>Tipo</th><th>Nulo</th><th>Chave</th><th>Padrão</th><th>Descrição</th></tr></thead>"
            f"<tbody>{''.join(linhas)}</tbody></table>"
            + (f"<p><strong>Índices</strong></p><ul>{''.join(indices)}</ul>" if indices else "")
            + (f"<p><strong>Restrições</strong></p><ul>{''.join(restr)}</ul>" if restr else "")
            + "</section>"
        )
    return "".join(partes)


def diagramas() -> None:
    DIAGRAMAS.mkdir(exist_ok=True)
    if not shutil.which("dot"):
        return
    for fonte in DIAGRAMAS.glob("*.dot"):
        destino = fonte.with_suffix(".svg")
        subprocess.run(["dot", "-Tsvg", str(fonte), "-o", str(destino)], check=True)
        texto = destino.read_text(encoding="utf-8")
        destino.write_text(re.sub(r"<!--.*?-->\n?", "", texto, flags=re.S), encoding="utf-8")


def gerar(nome: str, pdf: str, preencher: dict[str, str]) -> None:
    from weasyprint import HTML

    texto = (FONTE / nome).read_text(encoding="utf-8")
    for marcador, conteudo in preencher.items():
        texto = texto.replace(marcador, conteudo)
    destino = RAIZ / pdf
    HTML(string=texto, base_url=str(FONTE) + "/").write_pdf(destino)
    shutil.copyfile(destino, RAIZ / "docs" / pdf)
    print(f"{pdf} gerado")


if __name__ == "__main__":
    diagramas()
    gerar("manual.html", "Manual-Delco.pdf", {})
    gerar("banco-de-dados.html", "Banco-de-Dados-Delco.pdf", {'<div id="tabelas"></div>': tabelas_html()})

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from html import escape
from typing import Any
from urllib.parse import urlparse

import httpx
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Now

from .config import get_settings
from .models import Integration, IntegrationDelivery, IntegrationKind, WorkflowRun
from .schedule import utcnow

log = logging.getLogger("delco.integrations")

EVENTS = {
    "run.started": "Execução começou",
    "run.succeeded": "Execução concluída",
    "run.failed": "Execução falhou",
    "run.cancelled": "Execução cancelada",
    "run.recovered": "Execução voltou a funcionar",
}
URL_KINDS = ("slack", "discord", "teams", "google_chat", "webhook")
OUTBOUND = (*URL_KINDS, "telegram", "email")
MAX_ATTEMPTS = 5
BASE_DELAY = 10.0
CLAIM_SECONDS = 120
TOKEN_PREFIX = "dlh_"
MAX_RECIPIENTS = 20
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
_EMOJI = {
    "run.started": "▶️",
    "run.succeeded": "✅",
    "run.failed": "❌",
    "run.cancelled": "⏹️",
    "run.recovered": "💚",
    "test": "🔔",
}


class IntegrationError(ValueError):
    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("; ".join(fields.values()))
        self.fields = fields


def validate(data: dict[str, Any], *, current: Integration | None = None) -> dict[str, Any]:
    errors: dict[str, str] = {}
    kind = current.kind if current else data.get("kind")
    out: dict[str, Any] = {}
    config = dict(current.config or {}) if current else {}
    if not current:
        if kind not in IntegrationKind.values:
            errors["kind"] = "Escolha um tipo de integração válido."
        out["kind"] = kind
    if "name" in data or not current:
        name = (data.get("name") or "").strip()
        if not name:
            errors["name"] = "Dê um nome para reconhecer a integração."
        elif len(name) > 120:
            errors["name"] = "Use no máximo 120 caracteres."
        out["name"] = name
    if kind in URL_KINDS and ("url" in data or not current):
        url = (data.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            errors["url"] = "Informe a URL completa do webhook, começando com https://."
        elif len(url) > 1000:
            errors["url"] = "A URL passa de 1000 caracteres."
        out["url"] = url
    if kind == "telegram":
        if "token" in data or not current:
            token = (data.get("token") or "").strip()
            if not TELEGRAM_TOKEN_RE.match(token):
                errors["token"] = "Cole o token do bot como o BotFather mostra, no formato 123456:ABC…"
            out["secret"] = token
        if "chat_id" in data or not current:
            chat = str(data.get("chat_id") or "").strip()
            if not re.match(r"^(-?\d{3,20}|@[A-Za-z0-9_]{5,64})$", chat):
                errors["chat_id"] = "Informe o ID do chat (como -1001234567890) ou o @canal."
            config["chat_id"] = chat
    if kind == "email":
        if not get_settings().smtp_enabled:
            errors["kind"] = "Configure o servidor de e-mail (DELCO_SMTP_HOST) antes de criar avisos por e-mail."
        if "recipients" in data or not current:
            raw = data.get("recipients") or []
            if isinstance(raw, str):
                raw = re.split(r"[,;\s]+", raw)
            recipients = sorted({str(r).strip().lower() for r in raw if str(r).strip()})
            if not recipients:
                errors["recipients"] = "Informe pelo menos um e-mail."
            elif len(recipients) > MAX_RECIPIENTS:
                errors["recipients"] = f"Use no máximo {MAX_RECIPIENTS} destinatários."
            elif bad := [r for r in recipients if not EMAIL_RE.match(r) or len(r) > 200]:
                errors["recipients"] = f"E-mail inválido: {bad[0]}"
            config["to"] = recipients
    if kind in OUTBOUND and ("events" in data or not current):
        events = data.get("events") or []
        if not isinstance(events, list) or not events or any(e not in EVENTS for e in events):
            errors["events"] = "Marque pelo menos um evento."
        else:
            out["events"] = [e for e in EVENTS if e in events]
    if kind in OUTBOUND and "workflows" in data:
        names = data.get("workflows") or []
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            errors["workflows"] = "Lista de workflows inválida."
        else:
            out["workflows"] = sorted({n.strip() for n in names if n.strip()})
    if kind == "trigger" and ("workflow" in data or not current):
        from .models import Workflow

        wf = Workflow.objects.filter(name=data.get("workflow") or "").only("id", "is_active").first()
        if wf is None or not wf.is_active:
            errors["workflow"] = "Escolha o workflow que a URL vai disparar."
        else:
            out["workflow_id"] = wf.id
    if kind == "trigger" and data.get("require_signature") is not None:
        config["require_signature"] = bool(data["require_signature"])
    if "is_active" in data and data["is_active"] is not None:
        out["is_active"] = bool(data["is_active"])
    if errors:
        raise IntegrationError(errors)
    if config != (dict(current.config or {}) if current else {}):
        out["config"] = config
    return out


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(data: dict[str, Any], *, user_id: int | None) -> tuple[Integration, dict[str, str]]:
    from .store import audit

    fields = validate(data)
    extras: dict[str, str] = {}
    with transaction.atomic():
        integ = Integration(**fields, created_by_id=user_id)
        if integ.kind == "webhook":
            integ.secret = secrets.token_hex(24)
        if integ.kind == "trigger":
            token = TOKEN_PREFIX + secrets.token_urlsafe(30)
            integ.token_hash = hash_token(token)
            integ.token_prefix = token[:10]
            integ.events = []
            extras["token"] = token
            if (integ.config or {}).get("require_signature"):
                integ.secret = secrets.token_hex(24)
                extras["signing_secret"] = integ.secret
        integ.save()
        audit(user_id, "integration.create", f"integration:{integ.id}", {"kind": integ.kind, "name": integ.name})
    return integ, extras


def update(integ: Integration, data: dict[str, Any], *, user_id: int | None) -> tuple[Integration, dict[str, str]]:
    from .store import audit

    fields = validate(data, current=integ)
    extras: dict[str, str] = {}
    if integ.kind == "trigger" and "config" in fields:
        wants = fields["config"].get("require_signature")
        had = bool((integ.config or {}).get("require_signature"))
        if wants and not had:
            fields["secret"] = secrets.token_hex(24)
            extras["signing_secret"] = fields["secret"]
        elif not wants and had:
            fields["secret"] = None
    if data.get("rotate_secret") and integ.kind in ("webhook", "trigger"):
        if integ.kind == "webhook" or (fields.get("config") or integ.config or {}).get("require_signature"):
            fields["secret"] = secrets.token_hex(24)
            extras["signing_secret"] = fields["secret"]
    if not fields:
        return integ, extras
    with transaction.atomic():
        Integration.objects.filter(pk=integ.pk).update(**fields, updated_at=Now())
        audit(
            user_id,
            "integration.update",
            f"integration:{integ.id}",
            {k: v for k, v in fields.items() if k not in ("url", "secret")},
        )
    integ.refresh_from_db()
    return integ, extras


def delete(integ: Integration, *, user_id: int | None) -> None:
    from .store import audit

    with transaction.atomic():
        audit(user_id, "integration.delete", f"integration:{integ.id}", {"name": integ.name})
        integ.delete()


def resolve_token(token: str) -> Integration | None:
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    return (
        Integration.objects.select_related("workflow")
        .filter(kind="trigger", is_active=True, token_hash=hash_token(token))
        .first()
    )


def signature_ok(integ: Integration, body: bytes, headers: dict[str, str]) -> bool:
    if not (integ.config or {}).get("require_signature"):
        return True
    if not integ.secret:
        return False
    given = headers.get("x-delco-signature") or headers.get("x-hub-signature-256") or ""
    expected = "sha256=" + hmac.new(integ.secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(given.strip().lower(), expected)


def run_payload(run: WorkflowRun, event: str) -> dict[str, Any]:
    counts = {s: 0 for s in ("total", "succeeded", "failed")}
    for status in run.tasks.values_list("status", flat=True):
        counts["total"] += 1
        if status in counts:
            counts[status] += 1
    failed_tasks = list(run.tasks.filter(status="failed").order_by("id").values_list("task_name", flat=True)[:5])
    started, finished = run.started_at, run.finished_at
    return {
        "event": event,
        "run": {
            "id": run.id,
            "workflow": run.workflow.name,
            "status": run.status,
            "trigger": run.trigger,
            "triggered_by": run.triggered_by.name if run.triggered_by_id else None,
            "note": run.note,
            "error": run.error,
            "logical_date": run.logical_date.isoformat() if run.logical_date else None,
            "started_at": started.isoformat() if started else None,
            "finished_at": finished.isoformat() if finished else None,
            "duration_ms": int((finished - started).total_seconds() * 1000) if started and finished else None,
            "tasks_total": counts["total"],
            "tasks_succeeded": counts["succeeded"],
            "tasks_failed": counts["failed"],
            "failed_tasks": failed_tasks,
            "url": f"{get_settings().public_url.rstrip('/')}/#/runs/{run.id}",
        },
    }


def _recovered(runs: list[WorkflowRun]) -> list[WorkflowRun]:
    out = []
    for run in runs:
        previous = (
            WorkflowRun.objects.filter(
                workflow_id=run.workflow_id, id__lt=run.id, status__in=("succeeded", "failed")
            )
            .order_by("-id")
            .values_list("status", flat=True)
            .first()
        )
        if previous == "failed":
            out.append(run)
    return out


def enqueue(run_ids: list[int], event: str) -> int:
    if not run_ids:
        return 0
    events = [event, "run.recovered"] if event == "run.succeeded" else [event]
    subs = [
        i
        for i in Integration.objects.filter(is_active=True, kind__in=OUTBOUND).only("id", "events", "workflows")
        if any(e in (i.events or []) for e in events)
    ]
    if not subs:
        return 0
    runs = list(WorkflowRun.objects.select_related("workflow", "triggered_by").filter(id__in=run_ids))
    targets = {event: runs}
    if "run.recovered" in events and any("run.recovered" in (i.events or []) for i in subs):
        targets["run.recovered"] = _recovered(runs)
    rows = []
    for ev, ev_runs in targets.items():
        for run in ev_runs:
            payload = None
            for integ in subs:
                if ev not in (integ.events or []):
                    continue
                if integ.workflows and run.workflow.name not in integ.workflows:
                    continue
                payload = payload or run_payload(run, ev)
                rows.append(IntegrationDelivery(integration_id=integ.id, run_id=run.id, event=ev, payload=payload))
    IntegrationDelivery.objects.bulk_create(rows)
    return len(rows)


def summary(payload: dict[str, Any]) -> tuple[str, list[str], str | None]:
    event = payload["event"]
    run = payload.get("run") or {}
    if event == "test":
        return "🔔 Delco: teste de integração", ["Se você está lendo isto, a integração está funcionando."], run.get("url")
    label = EVENTS.get(event, event)
    title = f"{_EMOJI.get(event, '•')} {label}: {run.get('workflow')} #{run.get('id')}"
    lines = [f"Tarefas: {run.get('tasks_succeeded', 0)} de {run.get('tasks_total', 0)} com sucesso"]
    if run.get("tasks_failed"):
        names = ", ".join(run.get("failed_tasks") or [])
        lines.append(f"Com falha: {run['tasks_failed']}" + (f" ({names})" if names else ""))
    if run.get("error"):
        lines.append(f"Motivo: {run['error']}")
    if run.get("duration_ms") is not None:
        lines.append(f"Duração: {run['duration_ms'] / 1000:.1f} s".replace(".", ","))
    if run.get("note"):
        lines.append(f"Observação: {run['note']}")
    return title, lines, run.get("url")


def render(kind: str, payload: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    if kind == "webhook":
        return payload
    title, lines, link = summary(payload)
    if kind == "slack":
        text = f"*{title}*\n" + "\n".join(lines) + (f"\n<{link}|Abrir no Delco>" if link else "")
        return {"text": text}
    if kind == "discord":
        return {"content": f"**{title}**\n" + "\n".join(lines) + (f"\n{link}" if link else "")}
    if kind == "teams":
        return {"text": f"**{title}**  \n" + "  \n".join(lines) + (f"  \n[Abrir no Delco]({link})" if link else "")}
    if kind == "google_chat":
        return {"text": f"*{title}*\n" + "\n".join(lines) + (f"\n{link}" if link else "")}
    if kind == "telegram":
        body = f"<b>{escape(title)}</b>\n" + "\n".join(escape(x) for x in lines)
        if link:
            body += f'\n<a href="{escape(link)}">Abrir no Delco</a>'
        return {
            "chat_id": (config or {}).get("chat_id"),
            "text": body,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    raise ValueError(kind)


def _email(integ: Integration, delivery_id: int, event: str, payload: dict[str, Any]) -> tuple[int | None, str | None]:
    s = get_settings()
    if not s.smtp_enabled:
        return None, "Servidor de e-mail não configurado (DELCO_SMTP_HOST)."
    recipients = (integ.config or {}).get("to") or []
    if not recipients:
        return None, "Nenhum destinatário configurado."
    title, lines, link = summary(payload)
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = s.smtp_from
    msg["To"] = ", ".join(recipients)
    msg["X-Delco-Event"] = event
    msg["X-Delco-Delivery"] = str(delivery_id)
    msg.set_content("\n".join(lines) + (f"\n\nAbrir no Delco: {link}" if link else "") + "\n")
    items = "".join(f"<li>{escape(x)}</li>" for x in lines)
    button = (
        f'<p><a href="{escape(link)}" style="background:#191d20;color:#fff;padding:8px 14px;'
        f'border-radius:6px;text-decoration:none">Abrir no Delco</a></p>'
        if link
        else ""
    )
    msg.add_alternative(
        f'<div style="font-family:Arial,sans-serif;font-size:14px;color:#191d20">'
        f"<h2 style=\"font-size:17px\">{escape(title)}</h2><ul>{items}</ul>{button}</div>",
        subtype="html",
    )
    try:
        if s.smtp_security == "ssl":
            client = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.webhook_timeout)
        else:
            client = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.webhook_timeout)
        with client:
            if s.smtp_security == "starttls":
                client.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                client.login(s.smtp_user, s.smtp_password)
            client.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"[:500]
    return 250, None


def _post(integ: Integration, delivery_id: int, event: str, payload: dict[str, Any]) -> tuple[int | None, str | None]:
    full = {**payload, "delivery_id": delivery_id, "sent_at": utcnow().isoformat()}
    if integ.kind == "email":
        return _email(integ, delivery_id, event, full)
    body_obj = render(integ.kind, full, integ.config)
    body = json.dumps(body_obj, ensure_ascii=False, default=str).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"Delco/{_version()}",
        "X-Delco-Event": event,
        "X-Delco-Delivery": str(delivery_id),
    }
    if integ.kind == "webhook" and integ.secret:
        sig = hmac.new(integ.secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Delco-Signature"] = f"sha256={sig}"
    url = integ.url or ""
    if integ.kind == "telegram":
        url = f"{get_settings().telegram_api_url.rstrip('/')}/bot{integ.secret}/sendMessage"
    try:
        with httpx.Client(timeout=get_settings().webhook_timeout, follow_redirects=False) as client:
            resp = client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        text = f"{type(exc).__name__}: {exc}"
        if integ.secret and integ.kind == "telegram":
            text = text.replace(integ.secret, "•••")
        return None, text[:500]
    if 200 <= resp.status_code < 300:
        return resp.status_code, None
    return resp.status_code, f"HTTP {resp.status_code}: {resp.text[:300]}"


def _version() -> str:
    from . import __version__

    return __version__


def backoff(attempts: int) -> float:
    return BASE_DELAY * 2 ** max(0, attempts - 1)


def deliver_due(limit: int = 20) -> dict[str, int]:
    now = utcnow()
    with transaction.atomic():
        ids = list(
            IntegrationDelivery.objects.select_for_update(skip_locked=True)
            .filter(status="pending", next_attempt_at__lte=now)
            .order_by("next_attempt_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        if not ids:
            return {"sent": 0, "failed": 0, "retry": 0}
        IntegrationDelivery.objects.filter(id__in=ids).update(next_attempt_at=now + timedelta(seconds=CLAIM_SECONDS))
    result = {"sent": 0, "failed": 0, "retry": 0}
    for d in IntegrationDelivery.objects.select_related("integration").filter(id__in=ids).order_by("id"):
        code, err = _post(d.integration, d.id, d.event, d.payload)
        attempts = d.attempts + 1
        if err is None:
            status, nxt = "sent", None
        elif attempts >= MAX_ATTEMPTS:
            status, nxt = "failed", None
        else:
            status, nxt = "pending", utcnow() + timedelta(seconds=backoff(attempts))
        IntegrationDelivery.objects.filter(id=d.id).update(
            status=status,
            attempts=F("attempts") + 1,
            response_code=code,
            error=err,
            sent_at=utcnow() if status == "sent" else None,
            **({"next_attempt_at": nxt} if nxt else {}),
        )
        Integration.objects.filter(id=d.integration_id).update(
            last_delivery_at=utcnow(), last_status="sent" if err is None else "error"
        )
        result["sent" if status == "sent" else "failed" if status == "failed" else "retry"] += 1
        if err:
            log.warning("Entrega %s para %s falhou (%s/%s): %s", d.id, d.integration.name, attempts, MAX_ATTEMPTS, err)
    return result


def redeliver(delivery_id: int, *, user_id: int | None) -> IntegrationDelivery:
    from .store import audit

    d = IntegrationDelivery.objects.filter(id=delivery_id).first()
    if d is None:
        raise LookupError(delivery_id)
    if d.status == "pending":
        raise IntegrationError({"status": "Esta entrega já está na fila."})
    IntegrationDelivery.objects.filter(id=d.id).update(
        status="pending", attempts=0, next_attempt_at=Now(), error=None, response_code=None, sent_at=None
    )
    audit(user_id, "integration.redeliver", f"integration:{d.integration_id}", {"delivery": d.id})
    d.refresh_from_db()
    return d


def send_test(integ: Integration, *, user_id: int | None) -> IntegrationDelivery:
    from .store import audit

    if integ.kind not in OUTBOUND:
        raise IntegrationError({"kind": "O gatilho por URL recebe chamadas; não envia mensagens."})
    payload = {"event": "test", "run": {"url": f"{get_settings().public_url.rstrip('/')}/#/integrations"}}
    d = IntegrationDelivery.objects.create(integration=integ, event="test", payload=payload)
    code, err = _post(integ, d.id, "test", payload)
    IntegrationDelivery.objects.filter(id=d.id).update(
        status="sent" if err is None else "failed",
        attempts=1,
        response_code=code,
        error=err,
        sent_at=utcnow() if err is None else None,
    )
    Integration.objects.filter(id=integ.id).update(
        last_delivery_at=utcnow(), last_status="sent" if err is None else "error"
    )
    audit(user_id, "integration.test", f"integration:{integ.id}", {"ok": err is None, "code": code})
    d.refresh_from_db()
    return d


def as_dict(i: Integration, *, stats: dict[str, int] | None = None) -> dict[str, Any]:
    config = i.config or {}
    return {
        "id": i.id,
        "name": i.name,
        "kind": i.kind,
        "url": _mask(i.url) if i.url else None,
        "events": i.events or [],
        "workflows": i.workflows or [],
        "workflow": i.workflow.name if i.workflow_id and i.workflow else None,
        "token_prefix": i.token_prefix,
        "hook_path": f"/api/hooks/{i.token_prefix}…" if i.kind == "trigger" else None,
        "has_secret": bool(i.secret),
        "require_signature": bool(config.get("require_signature")) if i.kind == "trigger" else None,
        "chat_id": config.get("chat_id") if i.kind == "telegram" else None,
        "recipients": config.get("to") if i.kind == "email" else None,
        "is_active": i.is_active,
        "created_by": i.created_by.name if i.created_by_id and i.created_by else None,
        "created_at": i.created_at,
        "last_delivery_at": i.last_delivery_at,
        "last_status": i.last_status,
        "stats": stats or {"sent": 0, "failed": 0, "pending": 0},
    }


def _mask(url: str) -> str:
    p = urlparse(url)
    path = p.path or ""
    shown = path[:12] + ("…" if len(path) > 12 else "")
    return f"{p.scheme}://{p.netloc}{shown}"


def list_all() -> list[dict[str, Any]]:
    from django.db.models import Count, Q

    stats = {
        row["integration_id"]: row
        for row in IntegrationDelivery.objects.values("integration_id").annotate(
            sent=Count("id", filter=Q(status="sent")),
            failed=Count("id", filter=Q(status="failed")),
            pending=Count("id", filter=Q(status="pending")),
        )
    }
    out = []
    for i in Integration.objects.select_related("workflow", "created_by").order_by("kind", "name"):
        s = stats.get(i.id, {})
        out.append(as_dict(i, stats={k: s.get(k, 0) for k in ("sent", "failed", "pending")}))
    return out


def deliveries(integration_id: int, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    qs = IntegrationDelivery.objects.filter(integration_id=integration_id)
    if status:
        qs = qs.filter(status=status)
    rows = qs.select_related("run__workflow").order_by("-id")[:limit]
    return [
        {
            "id": d.id,
            "event": d.event,
            "status": d.status,
            "attempts": d.attempts,
            "response_code": d.response_code,
            "error": d.error,
            "run_id": d.run_id,
            "workflow": d.run.workflow.name if d.run_id and d.run else None,
            "created_at": d.created_at,
            "sent_at": d.sent_at,
            "next_attempt_at": d.next_attempt_at if d.status == "pending" else None,
        }
        for d in rows
    ]

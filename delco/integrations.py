"""Integrações: notificações de saída (Slack, Discord, Teams, Google Chat, webhook) e gatilho por URL.

As notificações nascem como linhas em ``integration_deliveries`` na MESMA
transação que muda o estado da execução, então nenhuma se perde se o processo
cair logo depois. O agendador envia as pendentes fora da transação, com até
``MAX_ATTEMPTS`` tentativas e espera exponencial. O gatilho por URL guarda só
o SHA-256 do token, como os tokens pessoais.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import timedelta
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
}
OUTBOUND = ("slack", "discord", "teams", "google_chat", "webhook")
MAX_ATTEMPTS = 5
BASE_DELAY = 10.0
CLAIM_SECONDS = 120
TOKEN_PREFIX = "dlh_"
_EMOJI = {"run.started": "▶️", "run.succeeded": "✅", "run.failed": "❌", "run.cancelled": "⏹️", "test": "🔔"}


class IntegrationError(ValueError):
    """Dados inválidos para uma integração (vira 422 na API)."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("; ".join(fields.values()))
        self.fields = fields


# ------------------------------------------------------------------ validação
def validate(data: dict[str, Any], *, current: Integration | None = None) -> dict[str, Any]:
    """Confere e normaliza os campos de criação/edição. Devolve só o que pode ser gravado."""
    errors: dict[str, str] = {}
    kind = current.kind if current else data.get("kind")
    out: dict[str, Any] = {}
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
    if kind in OUTBOUND and ("url" in data or not current):
        url = (data.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            errors["url"] = "Informe a URL completa do webhook, começando com https://."
        elif len(url) > 1000:
            errors["url"] = "A URL passa de 1000 caracteres."
        out["url"] = url
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
    if "is_active" in data and data["is_active"] is not None:
        out["is_active"] = bool(data["is_active"])
    if errors:
        raise IntegrationError(errors)
    return out


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(data: dict[str, Any], *, user_id: int | None) -> tuple[Integration, str | None]:
    """Cria a integração. Para o gatilho por URL, devolve também o token (aparece uma vez só)."""
    from .store import audit

    fields = validate(data)
    token = None
    with transaction.atomic():
        integ = Integration(**fields, created_by_id=user_id)
        if integ.kind == "webhook":
            integ.secret = secrets.token_hex(24)
        if integ.kind == "trigger":
            token = TOKEN_PREFIX + secrets.token_urlsafe(30)
            integ.token_hash = hash_token(token)
            integ.token_prefix = token[:10]
            integ.events = []
        integ.save()
        audit(user_id, "integration.create", f"integration:{integ.id}", {"kind": integ.kind, "name": integ.name})
    return integ, token


def update(integ: Integration, data: dict[str, Any], *, user_id: int | None) -> Integration:
    from .store import audit

    fields = validate(data, current=integ)
    if not fields:
        return integ
    with transaction.atomic():
        Integration.objects.filter(pk=integ.pk).update(**fields, updated_at=Now())
        audit(user_id, "integration.update", f"integration:{integ.id}", {k: v for k, v in fields.items() if k != "url"})
    integ.refresh_from_db()
    return integ


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


# ------------------------------------------------------------------- eventos
def run_payload(run: WorkflowRun, event: str) -> dict[str, Any]:
    counts = {s: 0 for s in ("total", "succeeded", "failed")}
    for status in run.tasks.values_list("status", flat=True):
        counts["total"] += 1
        if status in counts:
            counts[status] += 1
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
            "started_at": started.isoformat() if started else None,
            "finished_at": finished.isoformat() if finished else None,
            "duration_ms": int((finished - started).total_seconds() * 1000) if started and finished else None,
            "tasks_total": counts["total"],
            "tasks_succeeded": counts["succeeded"],
            "tasks_failed": counts["failed"],
            "url": f"{get_settings().public_url.rstrip('/')}/#/runs/{run.id}",
        },
    }


def enqueue(run_ids: list[int], event: str) -> int:
    """Cria as entregas das integrações inscritas no evento. Chame dentro da transação da mudança."""
    if not run_ids:
        return 0
    subs = [
        i
        for i in Integration.objects.filter(is_active=True, kind__in=OUTBOUND).only("id", "events", "workflows")
        if event in (i.events or [])
    ]
    if not subs:
        return 0
    runs = WorkflowRun.objects.select_related("workflow", "triggered_by").filter(id__in=run_ids)
    rows = []
    for run in runs:
        payload = None
        for integ in subs:
            if integ.workflows and run.workflow.name not in integ.workflows:
                continue
            payload = payload or run_payload(run, event)
            rows.append(IntegrationDelivery(integration_id=integ.id, run_id=run.id, event=event, payload=payload))
    IntegrationDelivery.objects.bulk_create(rows)
    return len(rows)


# --------------------------------------------------------------------- envio
def render(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Corpo da requisição no formato que cada serviço espera."""
    event = payload["event"]
    if kind == "webhook":
        return payload
    run = payload.get("run") or {}
    label = EVENTS.get(event, "Teste de integração")
    if event == "test":
        title = "🔔 Delco: teste de integração"
        lines = ["Se você está lendo isto, a integração está funcionando."]
    else:
        title = f"{_EMOJI.get(event, '•')} {label}: {run.get('workflow')} #{run.get('id')}"
        lines = [f"Tarefas: {run.get('tasks_succeeded', 0)} de {run.get('tasks_total', 0)} com sucesso"]
        if run.get("tasks_failed"):
            lines.append(f"Com falha: {run['tasks_failed']}")
        if run.get("duration_ms") is not None:
            lines.append(f"Duração: {run['duration_ms'] / 1000:.1f} s".replace(".", ","))
        if run.get("note"):
            lines.append(f"Observação: {run['note']}")
    link = run.get("url")
    if kind == "slack":
        text = f"*{title}*\n" + "\n".join(lines) + (f"\n<{link}|Abrir no Delco>" if link else "")
        return {"text": text}
    if kind == "discord":
        return {"content": f"**{title}**\n" + "\n".join(lines) + (f"\n{link}" if link else "")}
    if kind == "teams":
        return {"text": f"**{title}**  \n" + "  \n".join(lines) + (f"  \n[Abrir no Delco]({link})" if link else "")}
    if kind == "google_chat":
        return {"text": f"*{title}*\n" + "\n".join(lines) + (f"\n{link}" if link else "")}
    raise ValueError(kind)


def _post(integ: Integration, delivery_id: int, event: str, payload: dict[str, Any]) -> tuple[int | None, str | None]:
    body_obj = render(integ.kind, {**payload, "delivery_id": delivery_id, "sent_at": utcnow().isoformat()})
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
    try:
        with httpx.Client(timeout=get_settings().webhook_timeout, follow_redirects=False) as client:
            resp = client.post(integ.url or "", content=body, headers=headers)
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"[:500]
    if 200 <= resp.status_code < 300:
        return resp.status_code, None
    return resp.status_code, f"HTTP {resp.status_code}: {resp.text[:300]}"


def _version() -> str:
    from . import __version__

    return __version__


def backoff(attempts: int) -> float:
    return BASE_DELAY * 2 ** max(0, attempts - 1)


def deliver_due(limit: int = 20) -> dict[str, int]:
    """Envia as entregas pendentes cujo horário chegou. Seguro com vários agendadores."""
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
        # posse temporária: outro agendador não pega a mesma entrega enquanto esta envia
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


def send_test(integ: Integration, *, user_id: int | None) -> IntegrationDelivery:
    """Envia na hora uma mensagem de teste e registra a entrega."""
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


# ------------------------------------------------------------------ leitura
def as_dict(i: Integration, *, stats: dict[str, int] | None = None) -> dict[str, Any]:
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
        "is_active": i.is_active,
        "created_by": i.created_by.name if i.created_by_id and i.created_by else None,
        "created_at": i.created_at,
        "last_delivery_at": i.last_delivery_at,
        "last_status": i.last_status,
        "stats": stats or {"sent": 0, "failed": 0, "pending": 0},
    }


def _mask(url: str) -> str:
    """Mostra o destino sem expor o segredo que costuma vir no caminho da URL."""
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


def deliveries(integration_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        IntegrationDelivery.objects.filter(integration_id=integration_id)
        .select_related("run__workflow")
        .order_by("-id")[:limit]
    )
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

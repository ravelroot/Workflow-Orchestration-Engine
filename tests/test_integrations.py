"""Integrações (notificações e gatilho por URL), desempenho, próximas execuções e exportação."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from delco import integrations, store
from delco.models import Integration, IntegrationDelivery
from delco.schedule import utcnow

from .conftest import finished, run_status, wait_for
from .test_api import auth, client  # noqa: F401  (fixture reaproveitada)


class Receiver:
    """Servidor HTTP local que faz o papel do Slack/webhook e guarda o que recebeu."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append({"headers": dict(self.headers), "body": body})
                self.send_response(outer.status)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


def test_crud_validacao_e_papeis(client):  # noqa: F811
    admin = auth(client)
    r = client.post("/api/integrations", headers=admin, json={"name": "", "kind": "slack", "url": "ftp://x", "events": []})
    assert r.status_code == 422
    assert set(r.json()["fields"]) == {"name", "url", "events"}
    r = client.post(
        "/api/integrations",
        headers=admin,
        json={"name": "ERP", "kind": "webhook", "url": "https://erp.exemplo.com/delco/eventos/segredo",
              "events": ["run.failed", "run.succeeded"], "workflows": ["linear"]},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["has_secret"] and created["events"] == ["run.succeeded", "run.failed"]
    assert "segredo" not in created["url"]  # a URL aparece mascarada
    operator = auth(client, "operador@delco.dev")
    assert client.get("/api/integrations", headers=operator).status_code == 200
    assert client.post("/api/integrations", headers=operator, json={"name": "x", "kind": "slack"}).status_code == 403
    assert client.get("/api/integrations", headers=auth(client, "leitura@delco.dev")).status_code == 403
    r = client.patch(f"/api/integrations/{created['id']}", headers=admin, json={"is_active": False, "name": "ERP 2"})
    assert r.status_code == 200 and r.json()["is_active"] is False and r.json()["name"] == "ERP 2"
    assert client.get(f"/api/integrations/{created['id']}/deliveries", headers=admin).json() == []
    assert client.delete(f"/api/integrations/{created['id']}", headers=admin).status_code == 204
    assert client.get(f"/api/integrations/{created['id']}", headers=admin).status_code == 404
    actions = [a["action"] for a in client.get("/api/audit", headers=admin).json()["items"]]
    assert {"integration.create", "integration.update", "integration.delete"} <= set(actions)


def test_notificacao_assinada_chega_ao_destino(db, scheduler, workers, receiver):
    integ, _ = integrations.create(
        {"name": "Webhook", "kind": "webhook", "url": receiver.url, "events": ["run.started", "run.succeeded"]},
        user_id=None,
    )
    integrations.create(  # filtrada por workflow: não deve receber nada
        {"name": "Outro", "kind": "slack", "url": receiver.url, "events": ["run.succeeded"], "workflows": ["outro"]},
        user_id=None,
    )
    workers()
    run_id = store.trigger_run("linear", params={"base": 1})
    wait_for(scheduler, finished(db, run_id))
    assert run_status(db, run_id) == "succeeded"
    assert scheduler.deliver()["sent"] == 2
    events = [r["headers"]["X-Delco-Event"] for r in receiver.requests]
    assert events == ["run.started", "run.succeeded"]
    last = receiver.requests[-1]
    body = json.loads(last["body"])
    assert body["run"]["id"] == run_id and body["run"]["status"] == "succeeded" and body["run"]["tasks_total"] == 3
    expected = hmac.new(Integration.objects.get(id=integ.id).secret.encode(), last["body"], hashlib.sha256).hexdigest()
    assert last["headers"]["X-Delco-Signature"] == f"sha256={expected}"
    assert set(IntegrationDelivery.objects.values_list("status", flat=True)) == {"sent"}


def test_formatos_de_cada_servico():
    payload = {"event": "run.failed", "run": {"id": 7, "workflow": "vendas", "tasks_total": 3, "tasks_succeeded": 2,
                                               "tasks_failed": 1, "duration_ms": 1500, "note": "teste", "url": "http://x/#/runs/7"}}
    assert "vendas #7" in integrations.render("slack", payload)["text"]
    assert "Com falha: 1" in integrations.render("discord", payload)["content"]
    assert "[Abrir no Delco](http://x/#/runs/7)" in integrations.render("teams", payload)["text"]
    assert "Duração: 1,5 s" in integrations.render("google_chat", payload)["text"]
    assert integrations.render("webhook", payload) is payload
    assert "teste de integração" in integrations.render("slack", {"event": "test", "run": {}})["text"]
    with pytest.raises(ValueError):
        integrations.render("trigger", payload)


def test_destino_fora_do_ar_tenta_de_novo_e_desiste(db, scheduler, receiver):
    receiver.status = 503
    integ, _ = integrations.create(
        {"name": "Slack", "kind": "slack", "url": receiver.url, "events": ["run.cancelled"]}, user_id=None
    )
    run_id = store.trigger_run("linear")
    store.cancel_run(run_id)
    for attempt in range(1, integrations.MAX_ATTEMPTS + 1):
        IntegrationDelivery.objects.update(next_attempt_at=utcnow() - timedelta(seconds=1))
        integrations.deliver_due()
        d = IntegrationDelivery.objects.get()
        assert d.attempts == attempt and d.response_code == 503
    assert d.status == "failed" and "HTTP 503" in d.error
    assert Integration.objects.get(id=integ.id).last_status == "error"
    receiver.status = 200
    test = integrations.send_test(Integration.objects.get(id=integ.id), user_id=None)
    assert test.status == "sent" and test.response_code == 200


def test_gatilho_por_url_dispara_o_workflow(client, db):  # noqa: F811
    admin = auth(client)
    assert client.post("/api/integrations", headers=admin, json={"name": "CI", "kind": "trigger", "workflow": "nao_existe"}).status_code == 422
    r = client.post("/api/integrations", headers=admin, json={"name": "CI", "kind": "trigger", "workflow": "linear"})
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    assert r.json()["hook_url"].endswith(f"/api/hooks/{token}")
    hook = client.post(f"/api/hooks/{token}", json={"params": {"base": 3}, "note": "build 42"})
    assert hook.status_code == 201, hook.text
    run = client.get(f"/api/runs/{hook.json()['id']}", headers=admin).json()
    assert run["trigger"] == "webhook" and run["params"]["base"] == 3 and run["note"] == "build 42"
    assert client.post("/api/hooks/dlh_invalido").status_code == 404
    assert client.post(f"/api/integrations/{r.json()['id']}/test", headers=admin).status_code == 422
    filtered = client.get("/api/runs?trigger=webhook&since=1", headers=admin).json()
    assert [x["id"] for x in filtered["items"]] == [hook.json()["id"]]
    assert client.get(f"/api/runs?q=%23{hook.json()['id']}", headers=admin).json()["total"] == 1
    assert client.get("/api/runs?q=build", headers=admin).json()["total"] == 1


def test_desempenho_proximas_e_exportacao(client, db):  # noqa: F811
    from delco.seed import seed_history

    seed_history()
    admin = auth(client)
    data = client.get("/api/insights?days=7", headers=admin).json()
    assert data["totals"]["runs"] > 0 and data["workflows"] and len(data["workflows"][0]["daily"]) == 7
    assert {"failing_tasks", "slowest_tasks"} <= set(data)
    assert client.get("/api/insights?days=3", headers=admin).status_code == 422
    upcoming = client.get("/api/upcoming?hours=1", headers=admin).json()
    assert upcoming and all(u["workflow"] == "agendado" for u in upcoming)
    r = client.get("/api/runs/export?status=succeeded", headers=admin)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("id,workflow,estado,origem")
    assert len(lines) > 1 and all(",succeeded," in line for line in lines[1:])

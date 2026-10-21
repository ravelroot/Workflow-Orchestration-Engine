"""API HTTP (Django REST Framework) e WebSocket (Django Channels)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from channels.testing import WebsocketCommunicator

from delco import store
from delco.seed import DEMO_PASSWORD, seed_history, seed_users

from .conftest import tasks_of


def django_client(**kwargs) -> httpx.Client:
    """Cliente HTTP de verdade passando por toda a pilha do Django (middlewares, DRF, WhiteNoise)."""
    from delco.wsgi import application

    return httpx.Client(
        transport=httpx.WSGITransport(app=application), base_url="http://testserver", **kwargs
    )


@pytest.fixture
def client(db, bus, settings, scheduler):
    seed_users()
    with django_client() as c:
        yield c


def auth(client: httpx.Client, email: str = "admin@delco.dev") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_saude_e_painel(client):
    assert client.get("/api/health").json() == {
        "status": "ok",
        "database": True,
        "redis": True,
        "version": "1.0.0",
    }
    page = client.get("/")
    assert page.status_code == 200 and "Delco" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]


def test_login_logout_e_bloqueio(client, settings):
    headers = auth(client)
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["email"] == "admin@delco.dev" and me["role"] == "admin"
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/workflows").json()["code"] == "unauthenticated"
    for _ in range(settings.login_max_failures):
        r = client.post("/api/auth/login", json={"email": "leitura@delco.dev", "password": "errada123"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"email": "leitura@delco.dev", "password": DEMO_PASSWORD})
    assert r.status_code == 423 and "bloqueada" in r.json()["message"]
    assert client.post("/api/auth/login", json={"email": "ninguem@x.dev", "password": "x"}).status_code == 401


def test_papeis_controlam_as_acoes(client):
    viewer = auth(client, "leitura@delco.dev")
    operator = auth(client, "operador@delco.dev")
    assert client.get("/api/workflows", headers=viewer).status_code == 200
    r = client.post("/api/workflows/linear/runs", headers=viewer, json={})
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert client.get("/api/users", headers=operator).status_code == 403
    r = client.post(
        "/api/workflows/linear/runs", headers=operator, json={"params": {"base": 3}, "note": "teste"}
    )
    assert r.status_code == 201
    run = client.get(f"/api/runs/{r.json()['id']}", headers=viewer).json()
    assert run["params"] == {"base": 3} and run["note"] == "teste" and run["triggered_by"] == "Tiago Moura"
    assert len(run["tasks"]) == 3 and run["graph"]["workflow"] == "linear"


def test_workflows_detalhe_grade_e_pausa(client, db):
    headers = auth(client)
    names = [w["name"] for w in client.get("/api/workflows", headers=headers).json()]
    assert {"linear", "paralelo", "agendado", "cinquenta"} <= set(names)
    wf = client.get("/api/workflows/paralelo", headers=headers).json()
    assert wf["version"] == 1 and len(wf["graph"]["tasks"]) == 6 and wf["schedule_text"] == "Somente manual"
    store.trigger_run("paralelo")
    grid = client.get("/api/workflows/paralelo/grid", headers=headers).json()
    assert grid["tasks"][0] == "inicio" and len(grid["runs"]) == 1
    assert grid["runs"][0]["cells"]["juntar"]["status"] == "pending"
    assert client.post("/api/workflows/agendado/pause", headers=headers).json()["is_paused"] is True
    assert client.post("/api/workflows/agendado/resume", headers=headers).json()["is_paused"] is False
    assert client.get("/api/workflows/nada", headers=headers).status_code == 404
    assert client.post("/api/workflows/nada/runs", headers=headers, json={}).status_code == 404
    assert client.get("/api/load-errors", headers=headers).json() == {}


def test_execucoes_listar_cancelar_e_retomar(client, db, scheduler):
    headers = auth(client)
    a = store.trigger_run("linear")
    b = store.trigger_run("longo")
    scheduler.tick()
    page = client.get("/api/runs?status=running&size=10", headers=headers).json()
    assert {r["id"] for r in page["items"]} == {a, b} and page["total"] == 2
    assert client.get("/api/runs?workflow=linear", headers=headers).json()["total"] == 1
    assert client.get("/api/runs?status=invalido", headers=headers).status_code == 422
    assert client.post(f"/api/runs/{b}/retry", headers=headers, json={}).status_code == 409
    r = client.post(f"/api/runs/{b}/cancel", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.post(f"/api/runs/{b}/cancel", headers=headers).status_code == 409
    r = client.post(f"/api/runs/{b}/retry", headers=headers, json={})
    assert r.status_code == 200 and r.json()["reset"] == ["espera", "nunca"]
    assert client.post(f"/api/runs/{a}/retry", headers=headers, json={"from_task": "nada"}).status_code == 409
    assert client.get("/api/runs/99999", headers=headers).status_code == 404
    task = client.get(f"/api/runs/{a}/tasks/extrair", headers=headers).json()
    assert task["status"] == "queued" and task["attempts_list"] == []
    assert client.get(f"/api/runs/{a}/tasks/nada", headers=headers).status_code == 404
    assert client.post("/api/runs/99999/cancel", headers=headers).status_code == 404


def test_tarefa_tentativas_e_logs(client, db, scheduler, workers):
    from .conftest import finished, wait_for

    headers = auth(client)
    workers()
    run_id = store.trigger_run("instavel")
    wait_for(scheduler, finished(db, run_id))
    task = client.get(f"/api/runs/{run_id}/tasks/flaky", headers=headers).json()
    assert [a["status"] for a in task["attempts_list"]] == ["succeeded", "failed", "failed"]
    assert task["output"]["tentativas"] == 3
    failed = task["attempts_list"][-1]
    assert "ConnectionError" in failed["error"] and "Traceback" in failed["traceback"]
    logs = client.get(f"/api/attempts/{failed['id']}/logs?limit=1", headers=headers).json()
    assert len(logs["lines"]) == 1 and logs["has_more"] is True
    assert client.get("/api/attempts/999999/logs", headers=headers).status_code == 404
    overview = client.get("/api/overview", headers=headers).json()
    assert overview["kpis"]["runs_succeeded_24h"] == 1 and len(overview["throughput"]) == 24
    assert overview["throughput"][-1]["failed"] == 2
    workers_list = client.get("/api/workers", headers=headers).json()
    assert workers_list[0]["status"] == "online" and workers_list[0]["executed_24h"] == 4
    queues = client.get("/api/queues", headers=headers).json()
    assert queues[0]["queue"] == "default" and queues[0]["stream_length"] == 0
    r = client.post(f"/api/runs/{run_id}/retry", headers=headers, json={"from_task": "depois"})
    assert r.status_code == 200 and r.json()["reset"] == ["depois"]
    assert tasks_of(db, run_id)["flaky"]["status"] == "succeeded"


def test_tokens_pessoais(client):
    headers = auth(client, "operador@delco.dev")
    r = client.post("/api/tokens", headers=headers, json={"name": "CLI", "expires_in_days": 30})
    assert r.status_code == 201
    token = r.json()["token"]
    assert token.startswith("dlc_")
    personal = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=personal).json()["email"] == "operador@delco.dev"
    listed = client.get("/api/tokens", headers=headers).json()
    assert [t["name"] for t in listed] == ["CLI"] and "token" not in listed[0]
    assert client.post("/api/tokens", headers=headers, json={"name": ""}).status_code == 422
    assert (
        client.post("/api/tokens", headers=headers, json={"name": "x", "expires_in_days": 0}).status_code
        == 422
    )
    assert client.delete(f"/api/tokens/{listed[0]['id']}", headers=headers).status_code == 204
    assert client.delete(f"/api/tokens/{listed[0]['id']}", headers=headers).status_code == 404
    assert client.get("/api/auth/me", headers=personal).status_code == 401


def test_troca_de_senha(client):
    headers = auth(client, "operador@delco.dev")
    other = auth(client, "operador@delco.dev")
    r = client.put(
        "/api/auth/me/password",
        headers=headers,
        json={"current_password": "errada", "new_password": "Nova12345"},
    )
    assert r.status_code == 422 and "current_password" in r.json()["fields"]
    r = client.put(
        "/api/auth/me/password",
        headers=headers,
        json={"current_password": DEMO_PASSWORD, "new_password": "curta"},
    )
    assert "new_password" in r.json()["fields"]
    r = client.put(
        "/api/auth/me/password",
        headers=headers,
        json={"current_password": DEMO_PASSWORD, "new_password": "Nova12345"},
    )
    assert r.status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=other).status_code == 401  # outras sessões encerradas
    assert (
        client.post(
            "/api/auth/login", json={"email": "operador@delco.dev", "password": "Nova12345"}
        ).status_code
        == 200
    )


def test_administracao_de_usuarios_e_auditoria(client):
    admin = auth(client)
    r = client.post(
        "/api/users",
        headers=admin,
        json={"email": "nova@delco.dev", "name": "Nova", "role": "chefe", "password": "x"},
    )
    assert r.status_code == 422 and {"role", "password"} <= set(r.json()["fields"])
    r = client.post(
        "/api/users",
        headers=admin,
        json={"email": "nova@delco.dev", "name": "Nova", "role": "viewer", "password": "Senha1234"},
    )
    assert r.status_code == 201
    uid = r.json()["id"]
    dup = client.post(
        "/api/users",
        headers=admin,
        json={"email": "NOVA@delco.dev", "name": "Outra", "role": "viewer", "password": "Senha1234"},
    )
    assert dup.status_code == 422 and "email" in dup.json()["fields"]
    login = client.post("/api/auth/login", json={"email": "nova@delco.dev", "password": "Senha1234"})
    nova = {"Authorization": f"Bearer {login.json()['token']}"}
    r = client.patch(f"/api/users/{uid}", headers=admin, json={"role": "operator", "name": "Nova Silva"})
    assert r.json()["role"] == "operator" and r.json()["name"] == "Nova Silva"
    r = client.patch(f"/api/users/{uid}", headers=admin, json={"is_active": False})
    assert r.json()["is_active"] is False
    assert client.get("/api/auth/me", headers=nova).status_code == 401
    me = client.get("/api/auth/me", headers=admin).json()
    assert client.patch(f"/api/users/{me['id']}", headers=admin, json={"role": "viewer"}).status_code == 422
    assert client.patch(f"/api/users/{me['id']}", headers=admin, json={}).status_code == 422
    assert client.patch("/api/users/99999", headers=admin, json={"name": "x"}).status_code == 404
    users = client.get("/api/users", headers=admin).json()
    assert len(users) == 4
    actions = [a["action"] for a in client.get("/api/audit", headers=admin).json()["items"]]
    assert "user.create" in actions and "user.update" in actions and "auth.login" in actions


def test_requisicao_invalida_tem_formato_padrao(client):
    headers = auth(client)
    r = client.post("/api/workflows/linear/runs", headers=headers, json={"params": "não é objeto"})
    assert r.status_code == 422 and r.json()["code"] == "validation_error" and "params" in r.json()["fields"]
    assert client.get("/api/nao-existe", headers=headers).status_code == 404


def test_websocket_recebe_eventos(client, bus):
    from delco.api.events import listener
    from delco.asgi import application

    token = auth(client)["Authorization"][7:]

    async def scenario():
        recusado = WebsocketCommunicator(application, "/api/ws?token=invalido")
        connected, code = await recusado.connect()
        assert not connected and code == 4401
        ws = WebsocketCommunicator(application, f"/api/ws?token={token}")
        connected, _ = await ws.connect()
        assert connected
        assert await ws.receive_json_from() == {"type": "hello"}
        await ws.send_json_to({"action": "logs", "attempt_id": 7})
        await ws.send_json_to({"action": "ping"})
        assert await ws.receive_json_from() == {"type": "pong"}
        bus.publish("log", attempt_id=99, lines=[])  # outra tentativa: não chega
        bus.publish("log", attempt_id=7, lines=[{"message": "oi"}])
        assert (await ws.receive_json_from(timeout=3))["lines"] == [{"message": "oi"}]
        bus.publish("run", id=5, status="running")
        assert await ws.receive_json_from(timeout=3) == {"type": "run", "id": 5, "status": "running"}
        await ws.send_json_to({"action": "unlogs"})
        await ws.send_json_to({"action": "ping"})
        assert await ws.receive_json_from() == {"type": "pong"}
        bus.publish("log", attempt_id=7, lines=[{"message": "não chega mais"}])
        assert await ws.receive_nothing(timeout=0.5)
        await ws.disconnect()
        listener.task.cancel()

    asyncio.run(scenario())


def test_documentacao_openapi(client):
    schema = client.get("/api/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/workflows/{name}/runs" in paths and "/api/runs/{run_id}/retry" in paths
    docs = client.get("/api/docs")
    assert docs.status_code == 200 and "swagger" in docs.text.lower()


def test_historico_de_demonstracao(db, scheduler):
    seed_users()
    assert seed_users() is False
    created = seed_history()
    assert created > 0
    assert seed_history() == 0
    statuses = {r["status"] for r in db.fetchall("SELECT DISTINCT status FROM workflow_runs")}
    assert statuses <= {"succeeded", "failed"}

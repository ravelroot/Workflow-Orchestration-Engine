from __future__ import annotations

import dataclasses
import email
import email.policy
import json
import socket
import threading
import time
from datetime import UTC, datetime

import pytest

from delco import Fail, Param, Skip, Workflow, WorkflowError, integrations, store
from delco.config import ConfigError, load_settings
from delco.logs import JsonFormatter
from delco.models import AuditLog, Integration, TaskInstance, WorkflowRun
from delco.params import ParamError, coerce, resolve
from delco.sdk import diff_graphs

from .conftest import finished, run_status, tasks_of, wait_for
from .test_api import auth, client
from .test_integrations import receiver


@pytest.mark.nodb
def test_parametros_tipados_convertem_e_validam():
    assert coerce({"type": "integer"}, "7") == 7
    assert coerce({"type": "number"}, "2,5") == 2.5
    assert coerce({"type": "boolean"}, "sim") is True
    assert coerce({"type": "date"}, "2026-11-04T10:00") == "2026-11-04"
    assert coerce({"type": "array"}, "a, b") == ["a", "b"]
    assert coerce({"type": "object"}, '{"x": 1}') == {"x": 1}
    with pytest.raises(ParamError, match="inteiro"):
        coerce({"type": "integer"}, "dois")
    with pytest.raises(ParamError, match="no máximo 30"):
        coerce({"type": "integer", "max": 30}, 31)
    with pytest.raises(ParamError, match="opções"):
        coerce({"type": "string", "choices": ["a", "b"]}, "c")
    wf = Workflow(
        "p",
        params={
            "dias": Param(1, min=1, max=30, description="Dias"),
            "conta": Param(type=str, description="Obrigatório"),
            "livre": 0.3,
        },
    )
    assert wf.params == {"dias": 1, "livre": 0.3}
    assert wf.params_schema["conta"]["required"] and wf.params_schema["livre"]["inferred"]
    merged, errors = resolve(wf.params_schema, wf.params, {"dias": "40", "livre": "abc"})
    assert errors == {"params.dias": "Precisa ser no máximo 30.", "params.conta": "Obrigatório."}
    merged, errors = resolve(wf.params_schema, wf.params, {"conta": 123, "extra": True})
    assert not errors and merged == {"dias": 1, "conta": "123", "livre": 0.3, "extra": True}
    with pytest.raises(WorkflowError, match="agendado"):
        Workflow("q", schedule="@daily", params={"x": Param(type=int)})
    with pytest.raises(WorkflowError, match="desconhecido"):
        Param(type="moeda")


@pytest.mark.nodb
def test_retry_on_e_tempo_da_execucao_entram_no_grafo_so_quando_usados():
    wf = Workflow("g", run_timeout=60)
    wf.add_task("a", lambda: 1)
    wf.add_task("b", lambda: 2, depends_on=["a"], retry_on=(TimeoutError, ConnectionError))
    graph = wf.graph()
    assert "retry_on" not in graph["tasks"][0]
    assert graph["tasks"][1]["retry_on"] == ["ConnectionError", "TimeoutError"]
    assert graph["run_timeout"] == 60
    assert wf.tasks["b"].should_retry(TimeoutError()) and not wf.tasks["b"].should_retry(KeyError())
    assert not wf.tasks["a"].should_retry(Fail("x")) and wf.tasks["a"].should_retry(Fail("x", retry=True))
    with pytest.raises(WorkflowError, match="classes de exceção"):
        wf.add_task("c", lambda: 3, retry_on=["TimeoutError"])
    with pytest.raises(WorkflowError, match="run_timeout"):
        Workflow("h", run_timeout=0)
    old = {"tasks": [{"name": "a", "retries": 1, "line": 3}, {"name": "x"}]}
    new = {"tasks": [{"name": "a", "retries": 2, "line": 9}, {"name": "y"}], "run_timeout": 5}
    assert diff_graphs(old, new) == {
        "added": ["y"],
        "removed": ["x"],
        "changed": [{"task": "a", "fields": ["retries"]}],
        "run_timeout": {"before": None, "after": 5},
    }


@pytest.mark.nodb
def test_execucao_local_respeita_regras_skip_fail_e_retentativas():
    wf = Workflow("local", params={"falhar": Param(False)})
    tentativas = []

    @wf.task(retries=2, retry_delay=0)
    def instavel(ctx):
        tentativas.append(ctx.attempt)
        if ctx.attempt < 2:
            raise ConnectionError("rede")
        ctx.log("passou")
        return {"n": ctx.attempt}

    @wf.task(depends_on=[instavel])
    def decidir(ctx):
        if ctx.params["falhar"]:
            raise Fail("sem retentativa")
        raise Skip("hoje não")

    @wf.task(depends_on=[decidir])
    def depois(ctx):
        return "nunca"

    @wf.task(depends_on=[decidir], trigger_rule="one_failed")
    def alertar(ctx):
        return "alerta"

    events = []
    run = wf.run_local(on_event=lambda kind, data: events.append(kind))
    assert run.succeeded and tentativas == [1, 2]
    assert {n: t.status for n, t in run.tasks.items()} == {
        "instavel": "succeeded",
        "decidir": "skipped",
        "depois": "skipped",
        "alertar": "skipped",
    }
    assert run.outputs == {"instavel": {"n": 2}} and ("info", "passou") in run.tasks["instavel"].logs
    assert {"start", "log", "error", "end", "skipped"} <= set(events)
    run = wf.run_local({"falhar": "true"})
    assert run.status == "failed" and run.tasks["decidir"].attempts == 1
    assert run.tasks["alertar"].status == "succeeded" and run.tasks["depois"].status == "skipped"
    with pytest.raises(WorkflowError, match="falhar"):
        wf.run_local({"falhar": "talvez"})


@pytest.mark.nodb
def test_configuracao_invalida_explica_cada_problema(monkeypatch):
    monkeypatch.setenv("DELCO_WORKER_CONCURRENCY", "muitos")
    monkeypatch.setenv("DELCO_HEARTBEAT_SECONDS", "20")
    monkeypatch.setenv("DELCO_LEASE_SECONDS", "30")
    monkeypatch.setenv("DELCO_LOG_FORMAT", "xml")
    monkeypatch.setenv("DELCO_TIMEZONE", "Lua/Base")
    with pytest.raises(ConfigError) as info:
        load_settings()
    text = str(info.value)
    for piece in ("DELCO_WORKER_CONCURRENCY", "DELCO_LEASE_SECONDS", "DELCO_LOG_FORMAT", "DELCO_TIMEZONE"):
        assert piece in text
    monkeypatch.delenv("DELCO_WORKER_CONCURRENCY")
    monkeypatch.setenv("DELCO_HEARTBEAT_SECONDS", "5")
    monkeypatch.setenv("DELCO_LOG_FORMAT", "json")
    monkeypatch.setenv("DELCO_TIMEZONE", "UTC")
    monkeypatch.setenv("DELCO_SMTP_PASSWORD", "segredo")
    s = load_settings()
    public = s.public()
    assert s.log_format == "json" and public["smtp_password"] == "•••• definido"
    assert "delco_test" in public["database_url"] and "segredo" not in json.dumps(public)


@pytest.mark.nodb
def test_log_em_json():
    import logging

    record = logging.makeLogRecord({"name": "delco.x", "levelname": "INFO", "msg": "olá %s", "args": ("mundo",)})
    record.run_id = 7
    data = json.loads(JsonFormatter().format(record))
    assert data["message"] == "olá mundo" and data["level"] == "info" and data["run_id"] == 7


@pytest.mark.nodb
def test_cli_new_dev_run_e_healthcheck(tmp_path, capsys, monkeypatch):
    from delco.cli import main

    assert main(["new", "relatorio_semanal", "--folder", str(tmp_path), "--schedule", "0 7 * * 1"]) == 0
    code = (tmp_path / "relatorio_semanal.py").read_text()
    assert "#" not in code and 'schedule="0 7 * * 1"' in code
    with pytest.raises(SystemExit):
        main(["new", "relatorio_semanal", "--folder", str(tmp_path)])
    assert main(["dev", "run", "relatorio_semanal", "--folder", str(tmp_path), "-p", "dias=3"]) == 0
    out = capsys.readouterr().out
    assert "Processando 3 dia(s)" in out and "Resultado: sucesso" in out
    assert main(["dev", "run", "relatorio_semanal", "--folder", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "succeeded" and data["tasks"]["carregar"]["output"] == {"gravadas": 0}
    with pytest.raises(SystemExit, match="dias"):
        main(["dev", "run", "relatorio_semanal", "--folder", str(tmp_path), "-p", "dias=99"])
    monkeypatch.setenv("DELCO_HEALTH_DIR", str(tmp_path))
    from delco import config

    config.get_settings.cache_clear()
    try:
        assert main(["healthcheck", "worker"]) == 1
        (tmp_path / "delco-worker.health").write_text("x")
        assert main(["healthcheck", "worker", "--max-age", "30"]) == 0
    finally:
        config.get_settings.cache_clear()


def test_parametros_validados_no_disparo(client):
    operator = auth(client, "operador@delco.dev")
    r = client.post("/api/workflows/controle/runs", headers=operator, json={"params": {"lote": 500, "modo": "x"}})
    assert r.status_code == 422
    assert r.json()["fields"] == {
        "params.modo": "Escolha uma das opções: ok, definitivo, outro_erro.",
        "params.lote": "Precisa ser no máximo 100.",
    }
    r = client.post("/api/workflows/controle/runs", headers=operator, json={"params": {"lote": "20"}, "dry_run": True})
    assert r.status_code == 200 and r.json()["params"] == {"pular": False, "modo": "ok", "lote": 20}
    assert WorkflowRun.objects.count() == 0
    wf = client.get("/api/workflows/controle", headers=operator).json()
    assert wf["params_schema"]["lote"] == {"type": "integer", "required": False, "default": 10, "min": 1, "max": 100}


def test_skip_fail_e_retry_on_no_worker(db, scheduler, workers):
    workers()
    pulado = store.trigger_run("controle", params={"pular": True})
    definitivo = store.trigger_run("controle", params={"modo": "definitivo"})
    outro = store.trigger_run("controle", params={"modo": "outro_erro"})
    for run_id in (pulado, definitivo, outro):
        wait_for(scheduler, finished(db, run_id), timeout=30)
    t = tasks_of(db, pulado)
    assert run_status(db, pulado) == "succeeded"
    assert t["talvez_pular"]["status"] == "skipped" and t["talvez_pular"]["error"] == "nada para processar hoje"
    assert t["processar"]["status"] == "succeeded" and t["publicar"]["status"] == "succeeded"
    for run_id, erro in ((definitivo, "Fail: planilha corrompida"), (outro, "KeyError: 'coluna'")):
        t = tasks_of(db, run_id)
        assert run_status(db, run_id) == "failed"
        assert t["processar"]["attempts"] == 1 and t["processar"]["error"] == erro
        assert t["publicar"]["status"] == "skipped"
    attempt = db.fetchone(
        "SELECT a.status FROM task_attempts a JOIN task_instances t ON t.id = a.task_instance_id "
        "WHERE t.run_id = %s AND t.task_name = 'talvez_pular'",
        [pulado],
    )
    assert attempt["status"] == "skipped"


def test_tempo_limite_da_execucao(db, scheduler, workers):
    workers()
    run_id = store.trigger_run("cronometro")
    wait_for(scheduler, finished(db, run_id), timeout=20, recover=True)
    run = WorkflowRun.objects.get(id=run_id)
    assert run.status == "failed" and "Tempo limite da execução" in run.error
    t = tasks_of(db, run_id)
    assert t["depois"]["status"] == "cancelled"
    wait_for(scheduler, lambda: tasks_of(db, run_id)["demora"]["status"] == "cancelled", timeout=10)
    assert AuditLog.objects.filter(action="run.timeout").exists()


def test_reprocessamento_por_periodo(client, db, scheduler, workers):
    operator = auth(client, "operador@delco.dev")
    body = {"start": "2026-10-01", "end": "2026-10-05", "dry_run": True}
    r = client.post("/api/workflows/diario/backfill", headers=operator, json=body)
    assert r.status_code == 200 and len(r.json()["slots"]) == 5 and not r.json()["created"]
    assert r.json()["slots"][0] == "2026-10-01T06:00:00+00:00"
    r = client.post("/api/workflows/diario/backfill", headers=operator, json={**body, "dry_run": False, "params": {"regiao": "norte"}})
    assert r.status_code == 201 and len(r.json()["created"]) == 5
    workers()
    for run_id in r.json()["created"]:
        wait_for(scheduler, finished(db, run_id), timeout=30)
    out = TaskInstance.objects.get(run_id=r.json()["created"][2], task_name="resumir").output
    assert out == {"dia": "2026-10-03", "regiao": "norte"}
    again = client.post("/api/workflows/diario/backfill", headers=operator, json={**body, "end": "2026-10-06", "dry_run": False})
    assert len(again.json()["created"]) == 1 and len(again.json()["skipped"]) == 5
    run = client.get(f"/api/runs/{again.json()['created'][0]}", headers=operator).json()
    assert run["trigger"] == "backfill" and run["logical_date"].startswith("2026-10-06T06:00")
    r = client.post("/api/workflows/diario/backfill", headers=operator, json={"start": "2020-01-01", "end": "2026-01-01"})
    assert r.status_code == 422 and "intervalo menor" in r.json()["fields"]["end"]
    r = client.post("/api/workflows/linear/backfill", headers=operator, json=body)
    assert r.status_code == 409
    r = client.post("/api/workflows/diario/backfill", headers=operator, json={"start": "2026-10-05", "end": "2026-10-01"})
    assert r.status_code == 422 and "end" in r.json()["fields"]
    viewer = auth(client, "leitura@delco.dev")
    assert client.post("/api/workflows/diario/backfill", headers=viewer, json=body).status_code == 403


def test_marcar_tarefa_e_seguir(client, db, scheduler, workers):
    workers()
    run_id = store.trigger_run("quebra")
    wait_for(scheduler, finished(db, run_id), timeout=30)
    assert tasks_of(db, run_id)["publicar"]["status"] == "skipped"
    operator = auth(client, "operador@delco.dev")
    r = client.post(f"/api/runs/{run_id}/tasks/processar/mark", headers=operator, json={"status": "succeeded"})
    assert r.status_code == 200 and r.json()["reset"] == ["publicar"] and r.json()["run_status"] == "queued"
    wait_for(scheduler, finished(db, run_id), timeout=30)
    t = tasks_of(db, run_id)
    assert run_status(db, run_id) == "succeeded" and t["publicar"]["status"] == "succeeded"
    r = client.post(f"/api/runs/{run_id}/tasks/alertar/mark", headers=operator, json={"status": "failed"})
    assert r.json()["run_status"] == "failed" and r.json()["reset"] == []
    assert tasks_of(db, run_id)["alertar"]["error"] == "Marcada como falha pelo operador."
    assert client.post(f"/api/runs/{run_id}/tasks/alertar/mark", headers=operator, json={"status": "failed"}).status_code == 409
    assert client.post(f"/api/runs/{run_id}/tasks/x/mark", headers=operator, json={"status": "failed"}).status_code == 404
    active = store.trigger_run("linear")
    assert client.post(f"/api/runs/{active}/tasks/extrair/mark", headers=operator, json={"status": "failed"}).status_code == 409


def test_acoes_em_lote_e_observacao(client, db, scheduler):
    operator = auth(client, "operador@delco.dev")
    admin = auth(client)
    ids = [store.trigger_run("linear") for _ in range(3)]
    r = client.post("/api/runs/bulk", headers=operator, json={"action": "cancel", "ids": [*ids, 999999]})
    assert r.json()["done"] == ids and r.json()["errors"] == {"999999": "Execução não encontrada."}
    r = client.post("/api/runs/bulk", headers=operator, json={"action": "retry", "ids": ids[:2]})
    assert r.json()["done"] == ids[:2]
    assert client.post("/api/runs/bulk", headers=operator, json={"action": "delete", "ids": ids}).status_code == 403
    r = client.post("/api/runs/bulk", headers=admin, json={"action": "delete", "ids": ids})
    assert r.json()["done"] == [ids[2]] and set(r.json()["errors"]) == {str(ids[0]), str(ids[1])}
    assert not WorkflowRun.objects.filter(id=ids[2]).exists()
    r = client.patch(f"/api/runs/{ids[0]}", headers=operator, json={"note": "  reprocessar amanhã "})
    assert r.status_code == 200 and r.json()["note"] == "reprocessar amanhã"
    assert client.delete(f"/api/runs/{ids[0]}", headers=operator).status_code == 403
    assert client.post("/api/workflows/bulk", headers=operator, json={"action": "pause"}).json()["changed"] == [
        "agendado",
        "diario",
    ]
    r = client.post("/api/workflows/bulk", headers=operator, json={"action": "resume", "names": ["diario"]})
    assert r.json()["changed"] == ["diario"]
    assert client.get("/api/workflows/agendado", headers=operator).json()["is_paused"] is True


def test_versoes_e_codigo_fonte(client, db, scheduler, tmp_path, bus, settings):
    from delco.scheduler import Scheduler

    folder = tmp_path / "wf"
    folder.mkdir()
    arquivo = folder / "evolui.py"
    arquivo.write_text(
        'from delco import Workflow\n\nwf = Workflow("evolui")\n\n\n@wf.task\ndef a():\n    return 1\n'
    )
    s = Scheduler(bus, settings, workflows_dir=folder)
    s.sync()
    arquivo.write_text(
        'from delco import Workflow\n\nwf = Workflow("evolui")\n\n\n@wf.task(retries=5)\ndef a():\n    return 1\n'
        '\n\n@wf.task(depends_on=[a])\ndef b():\n    return 2\n'
    )
    s.sync()
    viewer = auth(client, "leitura@delco.dev")
    versions = client.get("/api/workflows/evolui/versions", headers=viewer).json()
    assert [v["version"] for v in versions] == [2, 1] and versions[0]["current"]
    assert versions[0]["changes"] == {"added": ["b"], "removed": [], "changed": [{"task": "a", "fields": ["retries"]}]}
    assert versions[1]["changes"] is None
    src = client.get("/api/workflows/evolui/source", headers=viewer).json()
    assert src["file"] == "evolui.py" and "def b():" in src["code"] and src["tasks"] == {"a": 6, "b": 11}
    assert client.get("/api/workflows/nada/versions", headers=viewer).status_code == 404


def test_pausar_worker(client, db, scheduler, workers, bus):
    w = workers(concurrency=2)
    operator = auth(client, "operador@delco.dev")
    assert client.post(f"/api/workers/{w.id}/pause", headers=operator).status_code == 200
    assert client.post("/api/workers/fantasma/pause", headers=operator).status_code == 404
    deadline = time.monotonic() + 5
    while not w.paused.is_set() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert w.paused.is_set()
    run_id = store.trigger_run("linear")
    for _ in range(30):
        scheduler.tick()
        time.sleep(0.05)
    assert run_status(db, run_id) == "running" and tasks_of(db, run_id)["extrair"]["status"] == "queued"
    rows = client.get("/api/workers", headers=operator).json()
    assert rows[0]["status"] == "paused" and rows[0]["pause_requested"] is True
    client.post(f"/api/workers/{w.id}/resume", headers=operator)
    wait_for(scheduler, finished(db, run_id), timeout=20)
    assert run_status(db, run_id) == "succeeded"


def test_metricas_sistema_e_doctor(client, db, scheduler, workers, settings, monkeypatch):
    viewer = auth(client, "leitura@delco.dev")
    admin = auth(client)
    assert client.get("/api/metrics").status_code == 401
    monkeypatch.setattr("delco.api.views.get_settings", lambda: dataclasses.replace(settings, metrics_token="segredo"))
    r = client.get("/api/metrics", headers={"Authorization": "Bearer segredo"})
    assert r.status_code == 200 and "text/plain" in r.headers["content-type"]
    assert 'delco_info{version="1.2.0"} 1' in r.text and "delco_schedulers 0" in r.text
    store.trigger_run("linear")
    text = client.get("/api/metrics", headers=viewer).text
    assert 'delco_runs_active{workflow="linear",status="queued"} 1' in text
    assert client.get("/api/system", headers=viewer).status_code == 403
    data = client.get("/api/system", headers=admin).json()
    checks = {c["id"]: c for c in data["checks"]}
    assert data["status"] == "error" and checks["scheduler"]["status"] == "error"
    assert checks["demo_accounts"]["status"] == "warn" and checks["database"]["status"] == "ok"
    assert data["config"]["database_url"].startswith("mysql://delco:•••@") and data["versions"]["delco"] == "1.2.0"
    scheduler.beat()
    workers(queues=["outra"])
    checks = {c["id"]: c for c in client.get("/api/system", headers=admin).json()["checks"]}
    assert checks["scheduler"]["status"] == "ok" and checks["workers"]["status"] == "ok"
    assert checks["queues"]["status"] == "warn" and "default" in checks["queues"]["message"]
    r = client.post("/api/system/housekeeping", headers=admin)
    assert r.status_code == 200 and "runs" in r.json()["removed"]


def test_preferencias_auditoria_e_limite_de_entrada(client, db, bus, settings, monkeypatch):
    headers = auth(client, "leitura@delco.dev")
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["preferences"]["theme"] == "system" and me["preferences"]["page_size"] == 25
    r = client.patch("/api/auth/me/preferences", headers=headers, json={"theme": "dark", "page_size": 50, "palette": "3"})
    assert r.json()["theme"] == "dark" and r.json()["page_size"] == 50 and r.json()["palette"] == "3"
    assert client.patch("/api/auth/me/preferences", headers=headers, json={"palette": "9"}).status_code == 422
    r = client.patch("/api/auth/me/preferences", headers=headers, json={"theme": "rosa", "x": 1})
    assert r.status_code == 422 and set(r.json()["fields"]) == {"theme", "x"}
    login = client.post("/api/auth/login", json={"email": "leitura@delco.dev", "password": "Delco@2026"}).json()
    assert login["preferences"]["theme"] == "dark"
    admin = auth(client)
    page = client.get("/api/audit?action=auth&size=5", headers=admin).json()
    assert page["items"] and all(i["action"].startswith("auth.") for i in page["items"]) and "auth.login" in page["actions"]
    csv_text = client.get("/api/audit/export?action=auth.login", headers=admin).text
    assert csv_text.startswith("﻿id,quando,usuario") and "auth.login" in csv_text
    for key in bus.redis.scan_iter(match="delco:rate:*"):
        bus.redis.delete(key)
    monkeypatch.setattr("delco.api.views.get_settings", lambda: dataclasses.replace(settings, login_rate_limit=2))
    codes = [client.post("/api/auth/login", json={"email": "x@x.dev", "password": "y"}).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


class SMTPStub:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                f = conn.makefile("rwb")
                f.write(b"220 stub\r\n")
                f.flush()
                data = False
                lines: list[bytes] = []
                for raw in f:
                    if data:
                        if raw in (b".\r\n", b".\n"):
                            data = False
                            self.messages.append(b"".join(lines).decode())
                            f.write(b"250 ok\r\n")
                        else:
                            lines.append(raw)
                            continue
                    else:
                        cmd = raw.strip().upper()
                        if cmd.startswith(b"EHLO"):
                            f.write(b"250-stub\r\n250 8BITMIME\r\n")
                        elif cmd == b"DATA":
                            data = True
                            f.write(b"354 go\r\n")
                        elif cmd == b"QUIT":
                            f.write(b"221 bye\r\n")
                            f.flush()
                            break
                        else:
                            f.write(b"250 ok\r\n")
                    f.flush()

    def close(self) -> None:
        self.sock.close()


def test_email_telegram_e_voltou_a_funcionar(client, db, settings, receiver, monkeypatch):
    smtp = SMTPStub()
    patched = dataclasses.replace(
        settings,
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_security="none",
        telegram_api_url=receiver.url.rsplit("/", 1)[0],
    )
    monkeypatch.setattr("delco.integrations.get_settings", lambda: patched)
    admin = auth(client)
    r = client.post("/api/integrations", headers=admin, json={"name": "Time", "kind": "email", "recipients": ["x"], "events": ["run.failed"]})
    assert r.status_code == 422 and "recipients" in r.json()["fields"]
    r = client.post(
        "/api/integrations",
        headers=admin,
        json={"name": "Time", "kind": "email", "recipients": ["Dados@Empresa.com", "ops@empresa.com"], "events": ["run.failed", "run.recovered"]},
    )
    assert r.status_code == 201 and r.json()["recipients"] == ["dados@empresa.com", "ops@empresa.com"]
    mail_id = r.json()["id"]
    r = client.post(
        "/api/integrations",
        headers=admin,
        json={"name": "Bot", "kind": "telegram", "token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX", "chat_id": "-1001234567890", "events": ["run.recovered"]},
    )
    assert r.status_code == 201 and r.json()["chat_id"] == "-1001234567890" and r.json()["url"] is None
    test = client.post(f"/api/integrations/{mail_id}/test", headers=admin).json()
    first = email.message_from_string(smtp.messages[0], policy=email.policy.default)
    assert test["status"] == "sent" and "Delco: teste" in first["Subject"]
    falhou = store.trigger_run("linear")
    WorkflowRun.objects.filter(id=falhou).update(status="failed", started_at=datetime.now(UTC), finished_at=datetime.now(UTC))
    voltou = store.trigger_run("linear")
    WorkflowRun.objects.filter(id=voltou).update(status="succeeded", started_at=datetime.now(UTC), finished_at=datetime.now(UTC))
    assert integrations.enqueue([voltou], "run.succeeded") == 2
    assert integrations.deliver_due() == {"sent": 2, "failed": 0, "retry": 0}
    body = json.loads(receiver.requests[-1]["body"])
    assert receiver.requests[-1]["headers"]["X-Delco-Event"] == "run.recovered"
    assert body["chat_id"] == "-1001234567890" and "voltou a funcionar" in body["text"]
    assert "/bot123456:ABCDEFGHIJKLMNOPQRSTUVWX/sendMessage" not in json.dumps(client.get("/api/integrations", headers=admin).json())
    last = email.message_from_string(smtp.messages[-1], policy=email.policy.default)
    assert "voltou a funcionar" in last["Subject"] and last["To"] == "dados@empresa.com, ops@empresa.com"
    entrega = Integration.objects.get(name="Bot").deliveries.first()
    r = client.post(f"/api/integrations/{entrega.integration_id}/deliveries/{entrega.id}/retry", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "pending"
    smtp.close()


def test_gatilho_assinado_e_limite(client, db, settings, monkeypatch):
    import hashlib
    import hmac

    admin = auth(client)
    r = client.post("/api/integrations", headers=admin, json={"name": "CI", "kind": "trigger", "workflow": "linear", "require_signature": True})
    assert r.status_code == 201 and r.json()["require_signature"] and r.json()["signing_secret"]
    url = "/api/hooks/" + r.json()["token"]
    secret = r.json()["signing_secret"]
    body = json.dumps({"params": {"base": 2}}).encode()
    headers = {"Content-Type": "application/json"}
    assert client.post(url, content=body, headers=headers).status_code == 401
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(url, content=body, headers={**headers, "X-Hub-Signature-256": sig}).status_code == 201
    r = client.patch(f"/api/integrations/{r.json()['id']}", headers=admin, json={"require_signature": False})
    assert r.json()["require_signature"] is False and "signing_secret" not in r.json()
    assert client.post(url, json={}).status_code == 201
    monkeypatch.setattr("delco.api.views.get_settings", lambda: dataclasses.replace(settings, hook_rate_limit=1))
    assert client.post(url, json={}).status_code == 429

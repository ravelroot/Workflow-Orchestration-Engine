"""Confiabilidade: queda de worker, mensagens repetidas ou perdidas, vários agendadores."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path

from delco import store
from delco.bus import stream_key
from delco.schedule import utcnow
from delco.scheduler import Scheduler

from .conftest import FIXTURES, finished, run_status, tasks_of, wait_for


def _lines(path: Path) -> list[str]:
    return path.read_text().split() if path.exists() else []


def test_queda_no_meio_de_50_passos_retoma_exatamente_de_onde_parou(db, scheduler, workers, tmp_path):
    marcador = tmp_path / "passos.txt"
    primeiro = workers(worker_id="primeiro", concurrency=1)
    params = {"marcador": str(marcador), "pausa": 0.01, "lento": 23, "pausa_lenta": 5}
    run_id = store.trigger_run("cinquenta", params=params)
    wait_for(scheduler, lambda: tasks_of(db, run_id)["passo_23"]["status"] == "running", timeout=40)
    primeiro.kill()  # queda no passo 23: nada mais é gravado por este worker
    interrompido = "passo_23"
    assert tasks_of(db, run_id)["passo_22"]["status"] == "succeeded"

    workers(worker_id="segundo", concurrency=1)
    wait_for(scheduler, finished(db, run_id), timeout=60, recover=True)

    tasks = tasks_of(db, run_id)
    assert run_status(db, run_id) == "succeeded"
    executados = [int(x) for x in _lines(marcador)]
    numero = int(interrompido.split("_")[1])
    for n in range(1, 51):
        esperado = 2 if n == numero else 1  # só o passo interrompido roda de novo
        assert executados.count(n) == esperado, (n, executados.count(n))
    tentativas = db.fetchall(
        "SELECT status, worker_id FROM task_attempts WHERE task_instance_id = %s ORDER BY number",
        (tasks[interrompido]["id"],),
    )
    assert [t["status"] for t in tentativas] == ["lost", "succeeded"]
    assert [t["worker_id"] for t in tentativas] == ["primeiro", "segundo"]


def test_mensagem_repetida_nao_executa_duas_vezes(db, scheduler, bus, workers, tmp_path):
    marcador = tmp_path / "marcas.txt"
    run_id = store.trigger_run("marcador", params={"marcador": str(marcador)})
    scheduler.tick()
    task = tasks_of(db, run_id)["marcar"]
    assert task["status"] == "queued" and task["queue_message_id"]
    for _ in range(3):
        bus.enqueue("default", task["id"])  # mesma tarefa publicada mais 3 vezes
    workers(concurrency=4)
    wait_for(scheduler, finished(db, run_id))
    time.sleep(0.5)
    assert _lines(marcador) == ["x"]
    assert tasks_of(db, run_id)["marcar"]["attempts"] == 1
    assert bus.redis.xlen(stream_key("default")) == 0


def test_mensagem_perdida_e_republicada(db, scheduler, bus, workers, tmp_path):
    run_id = store.trigger_run("marcador", params={"marcador": str(tmp_path / "m.txt")})
    scheduler.tick()
    task = tasks_of(db, run_id)["marcar"]
    bus.redis.xdel(stream_key("default"), task["queue_message_id"])  # some do Redis
    db.execute(
        "UPDATE task_instances SET queued_at = NOW(6) - INTERVAL 1 MINUTE WHERE id = %s", (task["id"],)
    )
    assert scheduler.recover()["republished"] == 1
    workers()
    wait_for(scheduler, finished(db, run_id))
    assert run_status(db, run_id) == "succeeded"


def test_redis_apagado_inteiro_nao_perde_execucoes(db, scheduler, bus, workers, tmp_path):
    workers()
    run_id = store.trigger_run("marcador", params={"marcador": str(tmp_path / "m.txt")})
    bus.redis.flushdb()
    db.execute(
        "UPDATE task_instances SET queued_at = NOW(6) - INTERVAL 1 MINUTE WHERE run_id = %s", (run_id,)
    )
    wait_for(scheduler, finished(db, run_id), recover=True)
    assert run_status(db, run_id) == "succeeded"


def test_lease_vencido_vira_nova_tentativa_ou_falha(db, scheduler):
    run_id = store.trigger_run("marcador", params={"marcador": "/dev/null"})
    scheduler.tick()
    task = tasks_of(db, run_id)["marcar"]
    claim = store.claim(task["id"], None, lease_seconds=60)
    assert claim is not None and store.claim(task["id"], None, lease_seconds=60) is None
    db.execute(
        "UPDATE task_instances SET lease_expires_at = NOW(6) - INTERVAL 1 SECOND WHERE id = %s",
        (task["id"],),
    )
    recovered = store.recover_expired_leases()
    assert [r["status"] for r in recovered] == ["retry_wait"]
    # o worker antigo "acorda" e tenta gravar o resultado: é recusado
    assert store.finish(claim, "succeeded", output=1) is None
    assert store.task_heartbeat(task["id"], claim.attempt, 30) == (False, False)
    attempt = db.fetchone("SELECT status FROM task_attempts WHERE id = %s", (claim.attempt_id,))
    assert attempt["status"] == "lost"


def test_max_active_runs_segura_execucoes_na_fila(db, scheduler):
    ids = [store.trigger_run("longo") for _ in range(3)]
    scheduler.tick()
    assert [run_status(db, i) for i in ids] == ["running", "running", "queued"]
    store.cancel_run(ids[0])
    scheduler.tick()
    assert run_status(db, ids[2]) == "running"


def test_dois_agendadores_criam_uma_unica_execucao_por_horario(db, bus, settings):
    a = Scheduler(bus, settings, workflows_dir=FIXTURES)
    b = Scheduler(bus, settings, workflows_dir=FIXTURES)
    a.sync()
    b.sync()
    vencido = utcnow() - timedelta(minutes=1)
    db.execute("UPDATE workflows SET next_run_at = %s WHERE name = 'agendado'", (vencido,))
    barrier = threading.Barrier(2)
    created: list[list[int]] = []

    def go(s: Scheduler) -> None:
        barrier.wait()
        created.append(store.schedule_due())

    threads = [threading.Thread(target=go, args=(s,)) for s in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    runs = db.fetchall("SELECT logical_date, `trigger` FROM workflow_runs")
    assert len(runs) == 1 and runs[0]["trigger"] == "schedule"
    assert runs[0]["logical_date"] == vencido
    nxt = db.fetchone("SELECT next_run_at FROM workflows WHERE name = 'agendado'")["next_run_at"]
    assert nxt > utcnow()


def test_pausar_impede_agendamento_e_retomar_segue_a_agenda(db, scheduler):
    db.execute("UPDATE workflows SET next_run_at = NOW(6) - INTERVAL 1 HOUR WHERE name = 'agendado'")
    store.set_paused("agendado", True)
    assert store.schedule_due() == []
    store.set_paused("agendado", False)
    nxt = db.fetchone("SELECT next_run_at FROM workflows WHERE name = 'agendado'")["next_run_at"]
    assert nxt > utcnow()
    assert store.schedule_due() == []


def test_worker_sem_sinal_vira_offline(db, scheduler):
    store.register_worker("fantasma", "host", 1, ["default"], 2, "1.0.0")
    db.execute("UPDATE workers SET last_heartbeat_at = NOW(6) - INTERVAL 5 MINUTE WHERE id = 'fantasma'")
    assert scheduler.recover()["offline"] == ["fantasma"]


def test_desligamento_gracioso_espera_a_tarefa_terminar(db, scheduler, workers, tmp_path):
    w = workers()
    run_id = store.trigger_run("marcador", params={"marcador": str(tmp_path / "m.txt")})
    wait_for(scheduler, lambda: tasks_of(db, run_id)["marcar"]["status"] == "running")
    w.stop(grace=5)
    assert tasks_of(db, run_id)["marcar"]["status"] == "succeeded"
    assert db.fetchone("SELECT status FROM workers WHERE id = %s", (w.id,))["status"] == "offline"


def test_desligamento_sem_tempo_devolve_a_tarefa_para_a_fila(db, scheduler, workers):
    w = workers()
    run_id = store.trigger_run("longo")
    wait_for(scheduler, lambda: tasks_of(db, run_id)["espera"]["status"] == "running")
    w.stop(grace=0.2)
    task = tasks_of(db, run_id)["espera"]
    assert task["status"] == "failed"  # retries=0: a tentativa interrompida esgota o limite
    attempt = db.fetchone("SELECT status FROM task_attempts WHERE task_instance_id = %s", (task["id"],))
    assert attempt["status"] == "lost"


def test_limpeza_remove_execucoes_antigas(db, scheduler, settings):
    run_id = store.trigger_run("linear")
    db.execute(
        "UPDATE workflow_runs SET status = 'succeeded', finished_at = NOW(6) - INTERVAL 90 DAY WHERE id = %s",
        (run_id,),
    )
    assert scheduler.housekeeping()["runs"] == 1
    assert db.fetchone("SELECT count(*) AS n FROM task_instances")["n"] == 0

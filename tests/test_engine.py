"""Fluxo completo: agendador + workers reais + MySQL + Redis."""

from __future__ import annotations

import time

from delco import store

from .conftest import finished, run_status, tasks_of, wait_for


def test_fluxo_linear_passa_resultados_adiante(db, scheduler, workers):
    workers()
    run_id = store.trigger_run("linear", params={"base": 7})
    wait_for(scheduler, finished(db, run_id))
    assert run_status(db, run_id) == "succeeded"
    tasks = tasks_of(db, run_id)
    assert tasks["extrair"]["output"] == {"valor": 7}
    assert tasks["transformar"]["output"] == {"valor": 14}
    assert tasks["carregar"]["output"] == 15
    assert all(t["attempts"] == 1 and t["progress"] == 100 for t in tasks.values())
    logs = db.fetchall(
        """SELECT l.message FROM task_logs l JOIN task_attempts a ON a.id = l.attempt_id
            WHERE a.task_instance_id = %s ORDER BY l.id""",
        (tasks["extrair"]["id"],),
    )
    assert "lendo dados" in [r["message"] for r in logs]


def test_tarefas_independentes_rodam_em_paralelo(db, scheduler, workers):
    workers(concurrency=4)
    run_id = store.trigger_run("paralelo")
    wait_for(scheduler, finished(db, run_id))
    tasks = tasks_of(db, run_id)
    assert run_status(db, run_id) == "succeeded"
    assert tasks["juntar"]["output"] == [1, 2, 3, 4]
    partes = [tasks[f"parte_{i}"]["output"] for i in range(1, 5)]
    # as quatro partes ficaram abertas ao mesmo tempo
    assert max(p["comeco"] for p in partes) < min(p["fim"] for p in partes)


def test_retentativas_com_espera_ate_dar_certo(db, scheduler, workers):
    workers()
    run_id = store.trigger_run("instavel")
    wait_for(scheduler, finished(db, run_id))
    tasks = tasks_of(db, run_id)
    assert run_status(db, run_id) == "succeeded"
    assert tasks["flaky"]["attempts"] == 3
    assert tasks["flaky"]["output"]["chave"] == f"delco:instavel:{run_id}:flaky"
    attempts = db.fetchall(
        "SELECT number, status, error FROM task_attempts WHERE task_instance_id = %s ORDER BY number",
        (tasks["flaky"]["id"],),
    )
    assert [a["status"] for a in attempts] == ["failed", "failed", "succeeded"]
    assert "ConnectionError" in attempts[0]["error"]
    assert tasks["depois"]["output"] == 3


def test_falha_definitiva_pula_dependentes_e_respeita_trigger_rules(db, scheduler, workers):
    workers()
    run_id = store.trigger_run("quebra")
    wait_for(scheduler, finished(db, run_id))
    tasks = tasks_of(db, run_id)
    assert run_status(db, run_id) == "failed"
    assert tasks["preparar"]["status"] == "succeeded"
    assert tasks["processar"]["status"] == "failed"
    assert tasks["processar"]["attempts"] == 2
    assert "dado inválido" in tasks["processar"]["error"]
    assert tasks["publicar"]["status"] == "skipped"
    assert tasks["limpar"]["status"] == "succeeded"
    assert tasks["alertar"]["status"] == "succeeded"
    tb = db.fetchone(
        "SELECT traceback FROM task_attempts WHERE task_instance_id = %s ORDER BY number DESC LIMIT 1",
        (tasks["processar"]["id"],),
    )["traceback"]
    assert "Traceback" in tb and "ValueError" in tb


def test_retomar_da_falha_mantem_o_que_ja_deu_certo(db, scheduler, workers):
    workers()
    run_id = store.trigger_run("quebra")
    wait_for(scheduler, finished(db, run_id))
    preparar_antes = tasks_of(db, run_id)["preparar"]
    db.execute("""UPDATE workflow_runs SET params = '{"consertado": true}' WHERE id = %s""", (run_id,))
    reset = store.retry_run(run_id)["reset"]
    assert set(reset) == {"processar", "publicar", "limpar", "alertar"}
    wait_for(scheduler, finished(db, run_id))
    tasks = tasks_of(db, run_id)
    assert run_status(db, run_id) == "succeeded"
    assert tasks["preparar"]["attempts"] == 1
    assert tasks["preparar"]["finished_at"] == preparar_antes["finished_at"]
    assert tasks["processar"]["attempts"] == 3  # 2 da primeira vez + 1 agora
    assert tasks["publicar"]["status"] == "succeeded"
    assert tasks["alertar"]["status"] == "skipped"  # nada falhou desta vez


def test_reexecutar_a_partir_de_uma_tarefa(db, scheduler, workers):
    workers()
    run_id = store.trigger_run("linear")
    wait_for(scheduler, finished(db, run_id))
    reset = store.retry_run(run_id, from_task="transformar")["reset"]
    assert reset == ["carregar", "transformar"]
    wait_for(scheduler, finished(db, run_id))
    tasks = tasks_of(db, run_id)
    assert tasks["extrair"]["attempts"] == 1
    assert tasks["transformar"]["attempts"] == 2
    assert tasks["carregar"]["output"] == 21


def test_tempo_limite_encerra_a_tarefa(db, scheduler, workers):
    workers()
    run_id = store.trigger_run("estoura_tempo")
    started = time.monotonic()
    wait_for(scheduler, finished(db, run_id), timeout=15)
    assert time.monotonic() - started < 10
    task = tasks_of(db, run_id)["demorada"]
    assert task["status"] == "failed"
    attempt = db.fetchone("SELECT status FROM task_attempts WHERE task_instance_id = %s", (task["id"],))
    assert attempt["status"] == "timed_out"


def test_processo_que_morre_sem_resultado_vira_falha(db, scheduler, workers):
    workers()
    run_id = store.trigger_run("saida_abrupta")
    wait_for(scheduler, finished(db, run_id))
    assert "código 3" in tasks_of(db, run_id)["morre"]["error"]


def test_resultado_grande_demais_e_recusado(db, scheduler, workers):
    workers()
    run_id = store.trigger_run("saida_grande")
    wait_for(scheduler, finished(db, run_id))
    assert "OutputTooLarge" in tasks_of(db, run_id)["enorme"]["error"]


def test_cancelar_execucao_mata_a_tarefa_em_andamento(db, scheduler, workers):
    workers()
    run_id = store.trigger_run("longo")
    wait_for(scheduler, lambda: tasks_of(db, run_id)["espera"]["status"] == "running")
    time.sleep(0.5)
    store.cancel_run(run_id)
    wait_for(scheduler, lambda: tasks_of(db, run_id)["espera"]["status"] == "cancelled", timeout=10)
    tasks = tasks_of(db, run_id)
    assert run_status(db, run_id) == "cancelled"
    assert tasks["nunca"]["status"] == "cancelled"
    attempt = db.fetchone(
        "SELECT status FROM task_attempts WHERE task_instance_id = %s", (tasks["espera"]["id"],)
    )
    assert attempt["status"] == "cancelled"

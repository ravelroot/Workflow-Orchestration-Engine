"""Testes de unidade: não precisam de MySQL nem Redis."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from delco import Workflow, WorkflowError
from delco.cli import duration, parse_params, table
from delco.context import TaskContext
from delco.loader import load_folder
from delco.schedule import describe, next_fire, retry_delay, validate_cron
from delco.sdk import descendants
from delco.security import hash_password, password_problem, token_hash, verify_password
from delco.store import decide, run_outcome

pytestmark = pytest.mark.nodb


def test_workflow_monta_grafo_em_ordem_topologica():
    wf = Workflow("etl", schedule="@daily")

    @wf.task
    def a():
        return 1

    @wf.task(depends_on=[a])
    def b():
        return 2

    wf.add_task("c", lambda: 3, depends_on=["a", b], retries=0, timeout=None, queue="critical")
    graph = wf.graph()
    assert [t["name"] for t in graph["tasks"]] == ["a", "b", "c"]
    c = graph["tasks"][2]
    assert (
        c["upstream"] == ["a", "b"]
        and c["retries"] == 0
        and c["timeout"] is None
        and c["queue"] == "critical"
    )
    assert wf.fingerprint() == wf.fingerprint()
    assert descendants(graph, "a") == {"b", "c"}


def test_fingerprint_muda_quando_o_grafo_muda():
    def build(retries: int) -> str:
        wf = Workflow("x")
        wf.add_task("um", lambda: 1, retries=retries)
        return wf.fingerprint()

    assert build(1) != build(2)
    assert build(1) == build(1)


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda wf: wf.add_task("a", lambda: 1, depends_on=["fantasma"]), "não existe"),
        (
            lambda wf: (
                wf.add_task("a", lambda: 1, depends_on=["b"]),
                wf.add_task("b", lambda: 1, depends_on=["a"]),
            ),
            "Ciclo",
        ),
        (lambda wf: (wf.add_task("a", lambda: 1), wf.add_task("a", lambda: 1)), "duas vezes"),
        (lambda wf: wf.add_task("A-b", lambda: 1), "inválido"),
        (lambda wf: wf.add_task("a", lambda: 1, trigger_rule="sempre"), "trigger_rule"),
        (lambda wf: wf.add_task("a", lambda: 1, retries=-1), "retries"),
        (lambda wf: wf.add_task("a", lambda: 1, timeout=0), "timeout"),
        (lambda wf: wf.add_task("a", "nao e funcao"), "função"),
        (lambda wf: wf.add_task("a", lambda: 1, depends_on=[object()]), "não é uma tarefa"),
    ],
)
def test_definicoes_invalidas_sao_recusadas(build, message):
    wf = Workflow("teste")
    with pytest.raises(WorkflowError, match=message):
        build(wf)
        wf.validate()


def test_workflow_sem_tarefas_e_parametros_invalidos():
    with pytest.raises(WorkflowError, match="não tem tarefas"):
        Workflow("vazio").validate()
    with pytest.raises(WorkflowError):
        Workflow("Nome Ruim")
    with pytest.raises(WorkflowError, match="Agendamento"):
        Workflow("x", schedule="todo dia")
    with pytest.raises(WorkflowError, match="Fuso"):
        Workflow("x", timezone="Lua/Base")
    with pytest.raises(WorkflowError):
        Workflow("x", max_active_runs=0)


def test_cron_proximo_horario_respeita_fuso():
    after = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)  # 09:00 em Brasília
    nxt = next_fire("0 7 * * *", "America/Sao_Paulo", after)
    assert nxt.astimezone(UTC) == datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    assert validate_cron("@hourly") == "0 * * * *"


@pytest.mark.parametrize(
    ("expr", "text"),
    [
        (None, "Somente manual"),
        ("*/15 * * * *", "A cada 15 minutos"),
        ("0 * * * *", "De hora em hora"),
        ("30 * * * *", "De hora em hora, no minuto 30"),
        ("0 */6 * * *", "A cada 6 horas"),
        ("0 7 * * *", "Todo dia às 07:00"),
        ("30 2 * * 1-5", "Dias úteis às 02:30"),
        ("0 9 * * 1", "Toda segunda-feira às 09:00"),
        ("0 9 * * 0", "Todo domingo às 09:00"),
        ("0 6 1 * *", "Todo dia 1 do mês às 06:00"),
        ("5 4 * 2 *", "5 4 * 2 *"),
        ("@daily", "Todo dia às 00:00"),
    ],
)
def test_descricao_da_agenda(expr, text):
    assert describe(expr) == text


def test_espera_exponencial_com_teto():
    assert retry_delay(1, 5, 2, 600, jitter=0) == 5
    assert retry_delay(3, 5, 2, 600, jitter=0) == 20
    assert retry_delay(20, 5, 2, 600, jitter=0) == 600
    assert 4.5 <= retry_delay(1, 5, 2, 600) <= 5.5


@pytest.mark.parametrize(
    ("rule", "ups", "verdict"),
    [
        ("all_success", [], "run"),
        ("all_success", ["succeeded", "running"], "wait"),
        ("all_success", ["succeeded", "failed"], "skip"),
        ("all_success", ["succeeded", "skipped"], "skip"),
        ("all_done", ["failed", "skipped"], "run"),
        ("one_failed", ["failed", "succeeded"], "run"),
        ("one_failed", ["succeeded", "succeeded"], "skip"),
    ],
)
def test_regras_de_disparo(rule, ups, verdict):
    assert decide(rule, ups) == verdict


def test_resultado_da_execucao():
    assert run_outcome(["succeeded", "skipped"]) == "succeeded"
    assert run_outcome(["succeeded", "failed", "cancelled"]) == "failed"
    assert run_outcome(["succeeded", "cancelled"]) == "cancelled"


def test_senhas_e_tokens():
    stored = hash_password("Delco@2026")
    assert stored.startswith("scrypt$") and "Delco" not in stored
    assert verify_password("Delco@2026", stored)
    assert not verify_password("errada123", stored)
    assert not verify_password("x", "formato-invalido")
    assert len(token_hash("dlc_abc")) == 64
    assert password_problem("curta1") and password_problem("semnumeros") and password_problem("12345678")
    assert password_problem("boa senha 1") is None


def test_contexto_fora_do_worker_imprime(capsys):
    ctx = TaskContext("wf", 1, "t", 1, attempt=2, max_attempts=2)
    ctx.log("olá")
    ctx.progress(50)
    assert ctx.idempotency_key == "delco:wf:1:t" and ctx.is_retry and ctx.is_last_attempt
    assert "olá" in capsys.readouterr().out


def test_carregador_isola_arquivos_com_erro(tmp_path: Path):
    (tmp_path / "bom.py").write_text(
        "from delco import Workflow\nw = Workflow('bom')\nw.add_task('a', lambda: 1)\n"
    )
    (tmp_path / "sintaxe.py").write_text("isso não é python\n")
    (tmp_path / "ciclo.py").write_text(
        "from delco import Workflow\nw = Workflow('ciclo')\n"
        "w.add_task('a', lambda: 1, depends_on=['b'])\nw.add_task('b', lambda: 1, depends_on=['a'])\n"
    )
    (tmp_path / "repetido.py").write_text(
        "from delco import Workflow\nw = Workflow('bom')\nw.add_task('a', lambda: 1)\n"
    )
    (tmp_path / "_ignorado.py").write_text("raise SystemExit('não deveria importar')\n")
    result = load_folder(tmp_path)
    assert list(result.workflows) == ["bom"]
    assert set(result.errors) == {"sintaxe.py", "ciclo.py", "repetido.py"}
    assert "SyntaxError" in result.errors["sintaxe.py"]
    assert "Ciclo" in result.errors["ciclo.py"]
    assert result.workflows["bom"].source_file == "bom.py"


def test_utilitarios_da_cli():
    assert parse_params(["dias=2", "nome=Ana", "ativo=true", "lista=[1,2]"], '{"base": 1}') == {
        "base": 1,
        "dias": 2,
        "nome": "Ana",
        "ativo": True,
        "lista": [1, 2],
    }
    with pytest.raises(SystemExit):
        parse_params(["sem-igual"], None)
    with pytest.raises(SystemExit):
        parse_params([], "[1]")
    assert duration(None) == "—" and duration(1500) == "1.5 s" and duration(125000) == "2 min 05 s"
    assert duration(3_900_000) == "1 h 05 min"
    out = table([{"a": 1, "b": None}], [("a", "A"), ("b", "B")])
    assert out.splitlines()[0].startswith("A") and "1" in out.splitlines()[2]
    assert table([], [("a", "A")]) == "(nada encontrado)"


def test_helpers_shell_e_http(monkeypatch):
    import httpx

    from delco import helpers

    sent: list[tuple] = []
    ctx = TaskContext("wf", 7, "t", 1, attempt=1, max_attempts=1, _send=sent.append)
    assert helpers.shell(ctx, "echo linha-um") == 0
    assert ("log", "info", "linha-um") in sent
    with pytest.raises(helpers.CommandError, match="código 3"):
        helpers.shell(ctx, ["sh", "-c", "exit 3"])
    assert helpers.shell(ctx, ["sh", "-c", "exit 2"], check=False) == 2

    def fake_request(method, url, headers, timeout, **kwargs):
        assert headers["Idempotency-Key"] == "delco:wf:7:t"
        return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))

    monkeypatch.setattr(helpers.httpx, "request", fake_request)
    assert helpers.http_json(ctx, "post", "https://exemplo.local/api", json={"a": 1}) == {"ok": True}

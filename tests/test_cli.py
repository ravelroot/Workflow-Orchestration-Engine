"""Linha de comando: cliente HTTP (contra a API em memória) e comandos locais."""

from __future__ import annotations

import re
import threading
import time

import httpx
import pytest

from delco import cli
from delco.db import release
from delco.seed import DEMO_PASSWORD, seed_users

from .conftest import FIXTURES


@pytest.fixture
def cli_env(db, bus, settings, scheduler, tmp_path, monkeypatch):
    from delco.wsgi import application

    seed_users()
    real_client = httpx.Client
    transport = httpx.WSGITransport(app=application)
    with real_client(transport=transport, base_url="http://delco.test") as test_client:

        def fake_client(base_url: str, timeout: float, headers: dict) -> httpx.Client:
            return real_client(transport=transport, base_url=base_url, headers=headers, timeout=timeout)

        monkeypatch.setattr(cli.httpx, "Client", fake_client)
        monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "cli.json")
        monkeypatch.delenv("DELCO_TOKEN", raising=False)
        monkeypatch.setenv("DELCO_URL", "http://delco.test")
        stop = threading.Event()

        def ticking():
            while not stop.is_set():
                try:
                    scheduler.tick()
                except Exception:
                    pass
                time.sleep(0.05)
            release()

        t = threading.Thread(target=ticking, daemon=True)
        t.start()
        yield test_client
        stop.set()
        t.join(2)


def run_cli(capsys, *args: str) -> tuple[int, str]:
    code = cli.main(list(args))
    out = capsys.readouterr()
    return code, out.out + out.err


def created_run(out: str) -> str:
    match = re.search(r"Execução #(\d+) criada", out)
    assert match, out
    return match.group(1)


def test_fluxo_completo_pela_cli(cli_env, workers, capsys, monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match="Sem credenciais"):
        cli.main(["workflows"])
    monkeypatch.setenv("DELCO_PASSWORD", DEMO_PASSWORD)
    code, out = run_cli(capsys, "login", "--email", "operador@delco.dev")
    assert code == 0 and "Tiago Moura" in out and (tmp_path / "cli.json").exists()

    code, out = run_cli(capsys, "workflows")
    assert "linear" in out and "Somente manual" in out

    workers()
    code, out = run_cli(capsys, "trigger", "linear", "-p", "base=5", "--note", "via cli", "--wait")
    linear = created_run(out)
    assert code == 0 and "sucesso" in out

    code, out = run_cli(capsys, "runs", "--workflow", "linear")
    assert "1 de 1 execuções" in out and "CLI" not in out.splitlines()[0]
    code, out = run_cli(capsys, "run", linear)
    assert "carregar" in out and "1/3" in out
    code, out = run_cli(capsys, "logs", linear, "extrair")
    assert "lendo dados" in out
    with pytest.raises(SystemExit, match="Tentativa 9"):
        cli.main(["logs", linear, "extrair", "--attempt", "9"])

    code, out = run_cli(capsys, "trigger", "quebra", "--wait")
    quebra = created_run(out)
    assert code == 1 and "falhou" in out
    code, out = run_cli(capsys, "retry", quebra, "--from", "processar")
    assert "processar" in out
    code, out = run_cli(capsys, "trigger", "longo")
    longo = created_run(out)
    time.sleep(0.5)
    code, out = run_cli(capsys, "cancel", longo)
    assert "cancelada" in out
    with pytest.raises(SystemExit, match="409"):
        cli.main(["cancel", longo])

    code, out = run_cli(capsys, "pause", "agendado")
    assert "pausado" in out
    code, out = run_cli(capsys, "resume", "agendado")
    assert "retomado" in out
    code, out = run_cli(capsys, "workers")
    assert "online" in out

    code, out = run_cli(capsys, "logout")
    assert not (tmp_path / "cli.json").exists()


def test_login_com_senha_errada(cli_env, monkeypatch):
    monkeypatch.setenv("DELCO_PASSWORD", "errada")
    with pytest.raises(SystemExit, match="401"):
        cli.main(["login", "--email", "admin@delco.dev"])


def test_comandos_locais(db, capsys, monkeypatch, tmp_path):
    code, out = run_cli(capsys, "validate", str(FIXTURES))
    assert code == 0 and "cinquenta" in out
    (tmp_path / "quebrado.py").write_text("x = (\n")
    code, out = run_cli(capsys, "validate", str(tmp_path))
    assert code == 1 and "quebrado.py" in out
    code, out = run_cli(capsys, "migrate")
    assert "nenhuma" in out
    code, out = run_cli(
        capsys,
        "users",
        "create",
        "--email",
        "cli@delco.dev",
        "--name",
        "Pela CLI",
        "--role",
        "viewer",
        "--password",
        "Senha1234",
    )
    assert "criado" in out
    with pytest.raises(SystemExit, match="email"):
        cli.main(["users", "create", "--email", "cli@delco.dev", "--name", "x", "--password", "Senha1234"])
    with pytest.raises(SystemExit):
        cli.main(["--version"])

"""Linha de comando do Delco.

Comandos de servidor (rodam os processos):   api, scheduler, worker, migrate, seed, validate, users
                                             (atalhos para os comandos do Django em ``manage.py``)
Comandos de cliente (falam com a API):       login, logout, workflows, trigger, runs, run, logs,
                                             cancel, retry, pause, resume, workers
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from . import __version__

STATUS_PT = {
    "queued": "na fila",
    "running": "rodando",
    "succeeded": "sucesso",
    "failed": "falhou",
    "cancelled": "cancelada",
    "pending": "pendente",
    "retry_wait": "aguardando nova tentativa",
    "skipped": "pulada",
}
CONFIG_FILE = Path(os.environ.get("DELCO_CLI_CONFIG", Path.home() / ".config" / "delco" / "cli.json"))


# ------------------------------------------------------------------ utilidades
def table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "(nada encontrado)"
    data = [[("" if r.get(k) is None else str(r.get(k))) for k, _ in columns] for r in rows]
    widths = [max(len(h), *(len(row[i]) for row in data)) for i, (_, h) in enumerate(columns)]
    lines = ["  ".join(h.ljust(widths[i]) for i, (_, h) in enumerate(columns))]
    lines.append("  ".join("─" * w for w in widths))
    lines += ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in data]
    return "\n".join(lines)


def parse_params(pairs: list[str], raw_json: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if raw_json:
        loaded = json.loads(raw_json)
        if not isinstance(loaded, dict):
            raise SystemExit("--params-json precisa ser um objeto JSON, como '{\"dias\": 2}'.")
        params.update(loaded)
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"Parâmetro inválido: {pair!r}. Use chave=valor.")
        key, value = pair.split("=", 1)
        try:
            params[key.strip()] = json.loads(value)
        except ValueError:
            params[key.strip()] = value
    return params


def duration(ms: Any) -> str:
    if ms is None:
        return "—"
    s = int(ms) / 1000
    if s < 60:
        return f"{s:.1f} s"
    m, s = divmod(int(s), 60)
    return f"{m} min {s:02d} s" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def short_time(iso: str | None) -> str:
    return iso.replace("T", " ")[:19] if iso else "—"


class Client:
    def __init__(self, url: str, token: str | None) -> None:
        self.url = url.rstrip("/")
        self.http = httpx.Client(
            base_url=self.url, timeout=30, headers={"Authorization": f"Bearer {token}"} if token else {}
        )

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise SystemExit(f"Não foi possível falar com {self.url}: {exc}") from None
        if response.status_code >= 400:
            try:
                body = response.json()
                message = body.get("message", response.text)
                fields = body.get("fields") or {}
            except ValueError:
                message, fields = response.text, {}
            extra = "".join(f"\n  · {k}: {v}" for k, v in fields.items())
            raise SystemExit(f"Erro {response.status_code}: {message}{extra}")
        return response.json() if response.content else None


def load_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return {}


def client_from(args: argparse.Namespace) -> Client:
    cfg = load_config()
    url = args.url or os.environ.get("DELCO_URL") or cfg.get("url") or "http://localhost:8080"
    token = os.environ.get("DELCO_TOKEN") or cfg.get("token")
    if not token:
        raise SystemExit("Sem credenciais. Rode `delco login` ou defina DELCO_TOKEN com um token pessoal.")
    return Client(url, token)


# ------------------------------------------------------------- comandos cliente
def cmd_login(args: argparse.Namespace) -> int:
    url = args.url or os.environ.get("DELCO_URL") or "http://localhost:8080"
    email = args.email or input("E-mail: ").strip()
    password = os.environ.get("DELCO_PASSWORD") or getpass.getpass("Senha: ")
    anon = Client(url, None)
    session = anon.request("POST", "/api/auth/login", json={"email": email, "password": password})
    authed = Client(url, session["token"])
    personal = authed.request(
        "POST", "/api/tokens", json={"name": f"CLI em {socket.gethostname()}", "expires_in_days": args.days}
    )
    authed.request("POST", "/api/auth/logout")
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"url": url, "token": personal["token"], "token_id": personal["id"]}))
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:  # pragma: no cover - Windows
        pass
    print(f"Conectado a {url} como {session['user']['name']} ({session['user']['role']}).")
    print(f"Token pessoal salvo em {CONFIG_FILE} (válido por {args.days} dias).")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    cfg = load_config()
    if cfg.get("token") and cfg.get("token_id"):
        try:
            Client(cfg["url"], cfg["token"]).request("DELETE", f"/api/tokens/{cfg['token_id']}")
        except SystemExit:
            pass
    CONFIG_FILE.unlink(missing_ok=True)
    print("Credenciais removidas.")
    return 0


def cmd_workflows(args: argparse.Namespace) -> int:
    rows = client_from(args).request("GET", "/api/workflows")
    for r in rows:
        r["estado"] = "inativo" if not r["is_active"] else "pausado" if r["is_paused"] else "ativo"
        r["proxima"] = short_time(r["next_run_at"]) if r["next_run_at"] and not r["is_paused"] else "—"
        r["ultimas"] = " ".join(
            {"succeeded": "✓", "failed": "✗", "running": "▶", "queued": "·", "cancelled": "–"}.get(
                x["status"], "?"
            )
            for x in r["recent"][-8:]
        )
    print(
        table(
            rows,
            [
                ("name", "WORKFLOW"),
                ("estado", "ESTADO"),
                ("schedule_text", "AGENDA"),
                ("task_count", "TAREFAS"),
                ("proxima", "PRÓXIMA (UTC)"),
                ("ultimas", "ÚLTIMAS"),
            ],
        )
    )
    return 0


def _print_run(run: dict[str, Any]) -> None:
    print(
        f"Execução #{run['id']} · {run['workflow']} v{run['version']} · {STATUS_PT.get(run['status'], run['status'])}"
        f" · {duration(run['duration_ms'])}"
    )
    rows = [
        {
            **t,
            "st": STATUS_PT.get(t["status"], t["status"]),
            "tent": f"{t['attempts']}/{t['max_attempts']}",
            "dur": duration(t["duration_ms"]),
            "erro": (t["error"] or "")[:70],
        }
        for t in run["tasks"]
    ]
    print(
        table(
            rows,
            [("name", "TAREFA"), ("st", "ESTADO"), ("tent", "TENT."), ("dur", "DURAÇÃO"), ("erro", "ERRO")],
        )
    )


def _wait(client: Client, run_id: int) -> int:
    last = None
    while True:
        run = client.request("GET", f"/api/runs/{run_id}")
        snapshot = (run["status"], run["tasks_succeeded"], run["tasks_running"], run["tasks_failed"])
        if snapshot != last:
            last = snapshot
            print(
                f"  {STATUS_PT.get(run['status'], run['status']):<10} {run['tasks_succeeded']}/{run['tasks_total']}"
                f" concluídas · {run['tasks_running']} rodando · {run['tasks_failed']} com falha",
                flush=True,
            )
        if run["status"] in ("succeeded", "failed", "cancelled"):
            print()
            _print_run(run)
            return 0 if run["status"] == "succeeded" else 1
        time.sleep(1)


def cmd_trigger(args: argparse.Namespace) -> int:
    client = client_from(args)
    params = parse_params(args.param, args.params_json)
    run = client.request(
        "POST",
        f"/api/workflows/{args.workflow}/runs",
        json={"params": params, "note": args.note, "source": "cli"},
    )
    print(f"Execução #{run['id']} criada para {args.workflow}.")
    return _wait(client, run["id"]) if args.wait else 0


def cmd_runs(args: argparse.Namespace) -> int:
    query = {"size": args.limit}
    if args.workflow:
        query["workflow"] = args.workflow
    if args.status:
        query["status"] = args.status
    page = client_from(args).request("GET", "/api/runs", params=query)
    rows = [
        {
            **r,
            "st": STATUS_PT.get(r["status"], r["status"]),
            "quando": short_time(r["created_at"]),
            "dur": duration(r["duration_ms"]),
            "prog": f"{r['tasks_succeeded']}/{r['tasks_total']}",
        }
        for r in page["items"]
    ]
    print(
        table(
            rows,
            [
                ("id", "#"),
                ("workflow", "WORKFLOW"),
                ("st", "ESTADO"),
                ("trigger", "ORIGEM"),
                ("quando", "CRIADA (UTC)"),
                ("dur", "DURAÇÃO"),
                ("prog", "TAREFAS"),
            ],
        )
    )
    print(f"\n{len(rows)} de {page['total']} execuções.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    _print_run(client_from(args).request("GET", f"/api/runs/{args.run_id}"))
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    client = client_from(args)
    task = client.request("GET", f"/api/runs/{args.run_id}/tasks/{args.task}")
    attempts = task["attempts_list"]
    if not attempts:
        print("Esta tarefa ainda não rodou.")
        return 0
    chosen = next((a for a in attempts if a["number"] == args.attempt), None) if args.attempt else attempts[0]
    if chosen is None:
        raise SystemExit(f"Tentativa {args.attempt} não encontrada.")
    after = 0
    while True:
        page = client.request(
            "GET", f"/api/attempts/{chosen['id']}/logs", params={"after": after, "limit": 2000}
        )
        for line in page["lines"]:
            print(f"{short_time(line['at'])}  {line['level'].upper():<7} {line['message']}")
            after = line["id"]
        if not page["has_more"]:
            break
    if chosen.get("traceback"):
        print("\n" + chosen["traceback"])
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    client_from(args).request("POST", f"/api/runs/{args.run_id}/cancel")
    print(f"Execução #{args.run_id} cancelada.")
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    client = client_from(args)
    result = client.request("POST", f"/api/runs/{args.run_id}/retry", json={"from_task": args.from_task})
    print(f"Execução #{args.run_id} retomada: {', '.join(result['reset'])}")
    return _wait(client, args.run_id) if args.wait else 0


def cmd_pause(args: argparse.Namespace, paused: bool = True) -> int:
    client_from(args).request("POST", f"/api/workflows/{args.workflow}/{'pause' if paused else 'resume'}")
    print(f"{args.workflow} {'pausado' if paused else 'retomado'}.")
    return 0


def cmd_workers(args: argparse.Namespace) -> int:
    rows = client_from(args).request("GET", "/api/workers")
    for r in rows:
        r["filas"] = ",".join(r["queues"])
        r["ocupacao"] = f"{len(r['running'])}/{r['concurrency']}"
        r["sinal"] = short_time(r["last_heartbeat_at"])
    print(
        table(
            rows,
            [
                ("id", "WORKER"),
                ("status", "ESTADO"),
                ("filas", "FILAS"),
                ("ocupacao", "VAGAS"),
                ("executed_24h", "24 H"),
                ("sinal", "ÚLTIMO SINAL (UTC)"),
            ],
        )
    )
    return 0


# ------------------------------------------------------------- comandos servidor
# Os processos do servidor são comandos do Django (``python manage.py worker`` etc.);
# aqui eles ganham um atalho: ``delco worker``.
def _django():
    from .db import setup

    setup()


def cmd_api(args: argparse.Namespace) -> int:  # pragma: no cover - processo de longa duração
    _django()
    from daphne.cli import CommandLineInterface

    from .config import get_settings
    from .db import migrate

    migrate()
    if get_settings().seed:
        from .seed import seed_users

        seed_users()
    logging.getLogger("delco.api").info("API pronta · versão %s · porta %s", __version__, args.port)
    CommandLineInterface().run(
        [
            "-b",
            args.host,
            "-p",
            str(args.port),
            "--proxy-headers",
            "--ping-interval",
            "20",
            "-v",
            "0",
            "delco.asgi:application",
        ]
    )
    return 0


def _call(command: str, *args: str, **options: Any) -> int:
    _django()
    from django.core.management import call_command

    call_command(command, *args, **{k: v for k, v in options.items() if v is not None})
    return 0


def cmd_scheduler(args: argparse.Namespace) -> int:  # pragma: no cover
    return _call("scheduler")


def cmd_worker(args: argparse.Namespace) -> int:  # pragma: no cover
    return _call("worker", queues=args.queues, concurrency=args.concurrency, name=args.name)


def cmd_migrate(args: argparse.Namespace) -> int:
    _django()
    from .db import migrate

    applied = migrate()
    print("Migrações aplicadas: " + (", ".join(applied) if applied else "nenhuma (banco já atualizado)"))
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    return _call("seed")


def cmd_validate(args: argparse.Namespace) -> int:
    from .config import get_settings
    from .loader import load_folder
    from .schedule import describe

    folder = Path(args.folder) if args.folder else get_settings().workflows_dir
    result = load_folder(folder)
    rows = [
        {
            "name": w.name,
            "arquivo": w.source_file,
            "tarefas": len(w.tasks),
            "agenda": describe(w.schedule),
            "fp": w.fingerprint()[:12],
        }
        for w in result.workflows.values()
    ]
    print(
        table(
            rows,
            [
                ("name", "WORKFLOW"),
                ("arquivo", "ARQUIVO"),
                ("tarefas", "TAREFAS"),
                ("agenda", "AGENDA"),
                ("fp", "VERSÃO"),
            ],
        )
    )
    for path, err in result.errors.items():
        print(f"\n✗ {path}\n{err.rstrip()}", file=sys.stderr)
    return 1 if result.errors else 0


def cmd_users_create(args: argparse.Namespace) -> int:
    _django()
    from django.core.management import CommandError

    try:
        return _call("createuser", email=args.email, name=args.name, role=args.role, password=args.password)
    except CommandError as exc:
        raise SystemExit(str(exc)) from None


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="delco", description="Delco: motor de orquestração de workflows.")
    p.add_argument("--version", action="version", version=f"delco {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="comando")

    def client_cmd(name: str, help_: str, fn) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--url", help="Endereço do Delco (padrão: DELCO_URL ou http://localhost:8080)")
        sp.set_defaults(func=fn)
        return sp

    s = sub.add_parser("api", help="sobe a API e o painel web")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=int(os.environ.get("DELCO_API_PORT", "8000")))
    s.set_defaults(func=cmd_api)
    sub.add_parser("scheduler", help="sobe o agendador").set_defaults(func=cmd_scheduler)
    s = sub.add_parser("worker", help="sobe um worker")
    s.add_argument("--queues", help="filas separadas por vírgula (padrão: DELCO_WORKER_QUEUES)")
    s.add_argument("--concurrency", type=int, help="tarefas simultâneas (padrão: DELCO_WORKER_CONCURRENCY)")
    s.add_argument("--name", help="nome do worker (padrão: host + sufixo aleatório)")
    s.set_defaults(func=cmd_worker)
    sub.add_parser("migrate", help="cria ou atualiza as tabelas").set_defaults(func=cmd_migrate)
    sub.add_parser("seed", help="cria contas e histórico de demonstração").set_defaults(func=cmd_seed)
    s = sub.add_parser("validate", help="confere os arquivos de workflow sem rodar nada")
    s.add_argument("folder", nargs="?", help="pasta (padrão: DELCO_WORKFLOWS_DIR)")
    s.set_defaults(func=cmd_validate)
    s = sub.add_parser("users", help="gerencia usuários pelo terminal")
    us = s.add_subparsers(dest="users_command", required=True, metavar="ação")
    s = us.add_parser("create", help="cria um usuário")
    s.add_argument("--email", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--role", choices=["admin", "operator", "viewer"], default="operator")
    s.add_argument("--password", help="senha (se omitida, é pedida no terminal)")
    s.set_defaults(func=cmd_users_create)

    s = client_cmd("login", "entra e salva um token pessoal para a CLI", cmd_login)
    s.add_argument("--email")
    s.add_argument("--days", type=int, default=90, help="validade do token (dias)")
    client_cmd("logout", "remove o token salvo", cmd_logout)
    client_cmd("workflows", "lista os workflows", cmd_workflows)
    s = client_cmd("trigger", "dispara uma execução", cmd_trigger)
    s.add_argument("workflow")
    s.add_argument("-p", "--param", action="append", default=[], metavar="CHAVE=VALOR")
    s.add_argument("--params-json", help="parâmetros como objeto JSON")
    s.add_argument("--note", help="observação gravada na execução")
    s.add_argument("--wait", action="store_true", help="acompanha até terminar (código de saída 1 se falhar)")
    s = client_cmd("runs", "lista execuções", cmd_runs)
    s.add_argument("--workflow")
    s.add_argument("--status", choices=["queued", "running", "succeeded", "failed", "cancelled"])
    s.add_argument("--limit", type=int, default=20)
    s = client_cmd("run", "mostra uma execução e suas tarefas", cmd_run)
    s.add_argument("run_id", type=int)
    s = client_cmd("logs", "mostra o log de uma tarefa", cmd_logs)
    s.add_argument("run_id", type=int)
    s.add_argument("task")
    s.add_argument("--attempt", type=int, help="número da tentativa (padrão: a última)")
    s = client_cmd("cancel", "cancela uma execução", cmd_cancel)
    s.add_argument("run_id", type=int)
    s = client_cmd("retry", "retoma uma execução a partir da falha", cmd_retry)
    s.add_argument("run_id", type=int)
    s.add_argument("--from", dest="from_task", help="refaz a partir desta tarefa (e as seguintes)")
    s.add_argument("--wait", action="store_true")
    s = client_cmd("pause", "pausa o agendamento de um workflow", lambda a: cmd_pause(a, True))
    s.add_argument("workflow")
    s = client_cmd("resume", "retoma o agendamento de um workflow", lambda a: cmd_pause(a, False))
    s.add_argument("workflow")
    client_cmd("workers", "lista os workers", cmd_workers)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

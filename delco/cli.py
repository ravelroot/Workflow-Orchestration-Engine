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
    if int(ms) < 1000:
        return f"{int(ms)} ms"
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


def emit_json(data: Any) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    return 0


def wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def client_from(args: argparse.Namespace) -> Client:
    cfg = load_config()
    url = args.url or os.environ.get("DELCO_URL") or cfg.get("url") or "http://localhost:8080"
    token = os.environ.get("DELCO_TOKEN") or cfg.get("token")
    if not token:
        raise SystemExit("Sem credenciais. Rode `delco login` ou defina DELCO_TOKEN com um token pessoal.")
    return Client(url, token)


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
    except OSError:
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
    if wants_json(args):
        return emit_json(rows)
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
    if args.dry_run:
        checked = client.request(
            "POST", f"/api/workflows/{args.workflow}/runs", json={"params": params, "dry_run": True}
        )
        print(f"Parâmetros válidos para {args.workflow}:")
        print(json.dumps(checked["params"], ensure_ascii=False, indent=2))
        return 0
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
    if getattr(args, "trigger", None):
        query["trigger"] = args.trigger
    if getattr(args, "since", None):
        query["since"] = args.since
    if getattr(args, "q", None):
        query["q"] = args.q
    page = client_from(args).request("GET", "/api/runs", params=query)
    if wants_json(args):
        return emit_json(page)
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


def cmd_export(args: argparse.Namespace) -> int:
    pairs = (("workflow", args.workflow), ("status", args.status), ("trigger", args.trigger), ("since", args.since))
    query = {k: v for k, v in pairs if v}
    client = client_from(args)
    try:
        response = client.http.get("/api/runs/export", params=query)
    except httpx.HTTPError as exc:
        raise SystemExit(f"Não foi possível falar com {client.url}: {exc}") from None
    if response.status_code >= 400:
        raise SystemExit(f"Erro {response.status_code}: {response.text[:200]}")
    with open(args.file, "wb") as fh:
        fh.write(response.content)
    lines = max(0, response.content.count(b"\n") - 1)
    print(f"{lines} execução(ões) exportada(s) para {args.file}")
    return 0


def cmd_upcoming(args: argparse.Namespace) -> int:
    rows = client_from(args).request("GET", "/api/upcoming", params={"hours": args.hours})
    if wants_json(args):
        return emit_json(rows)
    if not rows:
        print(f"Nenhuma execução agendada nas próximas {args.hours} h.")
        return 0
    for r in rows:
        r["quando"] = short_time(r["at"])
    print(table(rows, [("quando", "QUANDO (UTC)"), ("workflow", "WORKFLOW"), ("schedule_text", "AGENDA")]))
    return 0


KINDS_PT = {
    "slack": "Slack", "discord": "Discord", "teams": "Microsoft Teams", "google_chat": "Google Chat",
    "webhook": "Webhook", "trigger": "Gatilho por URL",
}


def cmd_integrations(args: argparse.Namespace) -> int:
    rows = client_from(args).request("GET", "/api/integrations")
    if wants_json(args):
        return emit_json(rows)
    if not rows:
        print("Nenhuma integração cadastrada. Crie em Integrações, no painel.")
        return 0
    for r in rows:
        r["tipo"] = KINDS_PT.get(r["kind"], r["kind"])
        r["ativa"] = "sim" if r["is_active"] else "não"
        r["destino"] = r["workflow"] or r["url"] or ""
        r["eventos"] = ", ".join(e.removeprefix("run.") for e in r["events"]) or "—"
        r["entregas"] = f"{r['stats']['sent']} ok · {r['stats']['failed']} falha"
    print(table(rows, [("id", "#"), ("name", "NOME"), ("tipo", "TIPO"), ("ativa", "ATIVA"),
                       ("destino", "DESTINO"), ("eventos", "EVENTOS"), ("entregas", "ENTREGAS")]))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    run = client_from(args).request("GET", f"/api/runs/{args.run_id}")
    if wants_json(args):
        return emit_json(run)
    _print_run(run)
    if run.get("error"):
        print(f"\nMotivo: {run['error']}")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    client = client_from(args)
    task = client.request("GET", f"/api/runs/{args.run_id}/tasks/{args.task}")
    attempts = task["attempts_list"]
    if not attempts and not args.follow:
        print("Esta tarefa ainda não rodou.")
        return 0
    while not attempts:
        time.sleep(1)
        task = client.request("GET", f"/api/runs/{args.run_id}/tasks/{args.task}")
        attempts = task["attempts_list"]
    chosen = next((a for a in attempts if a["number"] == args.attempt), None) if args.attempt else attempts[0]
    if chosen is None:
        raise SystemExit(f"Tentativa {args.attempt} não encontrada.")
    after = 0
    while True:
        page = client.request(
            "GET", f"/api/attempts/{chosen['id']}/logs", params={"after": after, "limit": 2000}
        )
        for line in page["lines"]:
            if not args.level or line["level"] in args.level:
                print(f"{short_time(line['at'])}  {line['level'].upper():<7} {line['message']}", flush=True)
            after = line["id"]
        if page["has_more"]:
            continue
        if not args.follow:
            break
        current = client.request("GET", f"/api/runs/{args.run_id}/tasks/{args.task}")
        latest = current["attempts_list"][0]
        if latest["id"] != chosen["id"]:
            print(f"--- tentativa {latest['number']} ---", flush=True)
            chosen, after = latest, 0
            continue
        if latest["status"] != "running":
            final = client.request("GET", f"/api/attempts/{chosen['id']}/logs", params={"after": after, "limit": 2000})
            for line in final["lines"]:
                print(f"{short_time(line['at'])}  {line['level'].upper():<7} {line['message']}", flush=True)
            chosen = latest
            break
        time.sleep(1)
    if chosen.get("traceback"):
        print("\n" + chosen["traceback"])
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    client = client_from(args)
    body = {
        "start": args.start,
        "end": args.end,
        "params": parse_params(args.param, args.params_json),
        "note": args.note,
        "dry_run": args.dry_run,
        "skip_existing": not args.all,
    }
    result = client.request("POST", f"/api/workflows/{args.workflow}/backfill", json=body)
    if wants_json(args):
        return emit_json(result)
    if args.dry_run:
        print(f"{len(result['slots'])} execução(ões) seriam criadas para {args.workflow}:")
        for at in result["slots"]:
            print(f"  {short_time(at)}")
        if result["skipped"]:
            print(f"{len(result['skipped'])} horário(s) já têm execução e seriam pulados.")
        return 0
    print(f"{len(result['created'])} execução(ões) criadas para {args.workflow}.")
    if result["skipped"]:
        print(f"{len(result['skipped'])} horário(s) já tinham execução e foram pulados (use --all para refazer).")
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    result = client_from(args).request(
        "POST", f"/api/runs/{args.run_id}/tasks/{args.task}/mark", json={"status": args.status}
    )
    print(f"{args.task} marcada como {STATUS_PT.get(args.status, args.status)}.")
    if result["reset"]:
        print(f"Execução #{args.run_id} retomada a partir de: {', '.join(result['reset'])}")
    else:
        print(f"Execução #{args.run_id} agora está como {STATUS_PT.get(result['run_status'], result['run_status'])}.")
    return 0


def cmd_versions(args: argparse.Namespace) -> int:
    rows = client_from(args).request("GET", f"/api/workflows/{args.workflow}/versions")
    if wants_json(args):
        return emit_json(rows)
    for r in rows:
        r["quando"] = short_time(r["created_at"])
        r["fp"] = r["fingerprint"][:12]
        r["atual"] = "●" if r["current"] else ""
        c = r["changes"]
        if c is None:
            r["mudou"] = "primeira versão"
        else:
            parts = []
            if c["added"]:
                parts.append("+" + ", +".join(c["added"]))
            if c["removed"]:
                parts.append("-" + ", -".join(c["removed"]))
            if c["changed"]:
                parts.append("~" + ", ~".join(x["task"] for x in c["changed"]))
            r["mudou"] = " ".join(parts)[:80] or "opções do workflow"
    print(table(rows, [("atual", ""), ("version", "VERSÃO"), ("quando", "CRIADA (UTC)"), ("tasks", "TAREFAS"),
                       ("runs", "EXECUÇÕES"), ("fp", "IMPRESSÃO"), ("mudou", "O QUE MUDOU")]))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    wf = client_from(args).request("GET", f"/api/workflows/{args.workflow}")
    if wants_json(args):
        return emit_json(wf)
    state = "inativo" if not wf["is_active"] else "pausado" if wf["is_paused"] else "ativo"
    print(f"{wf['name']} · v{wf['version']} · {state}")
    if wf["description"]:
        print(wf["description"])
    print(f"Agenda: {wf['schedule_text']}" + (f" ({wf['schedule']}, {wf['timezone']})" if wf["schedule"] else ""))
    print(f"Arquivo: {wf['source_file']}")
    if wf.get("run_timeout"):
        print(f"Tempo limite da execução: {duration(wf['run_timeout'] * 1000)}")
    if wf.get("params_schema"):
        print("\nParâmetros:")
        for name, spec in wf["params_schema"].items():
            extra = []
            if "default" in spec:
                extra.append(f"padrão {json.dumps(spec['default'], ensure_ascii=False)}")
            if spec.get("required"):
                extra.append("obrigatório")
            if spec.get("choices"):
                extra.append("opções " + ", ".join(str(c) for c in spec["choices"]))
            print(f"  {name} ({spec['type']}): {', '.join(extra)}" + (f" · {spec['description']}" if spec.get("description") else ""))
    print("\nTarefas:")
    for t in wf["graph"]["tasks"]:
        deps = f" ← {', '.join(t['upstream'])}" if t["upstream"] else ""
        opts = [f"{t['retries']} retentativa(s)", f"fila {t['queue']}"]
        if t["trigger_rule"] != "all_success":
            opts.append(t["trigger_rule"])
        if t.get("retry_on"):
            opts.append("repete só " + ", ".join(t["retry_on"]))
        print(f"  {t['name']}{deps}  [{' · '.join(opts)}]")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    client_from(args).request("PATCH", f"/api/runs/{args.run_id}", json={"note": args.text})
    print(f"Observação da execução #{args.run_id} {'atualizada' if args.text else 'removida'}.")
    return 0


TEMPLATE = """from delco import Param, Workflow

{var} = Workflow(
    "{name}",
    description="Descreva aqui o que este workflow faz.",
{schedule}    params={{
        "dias": Param(1, description="Quantos dias processar", min=1, max=30),
    }},
    tags=["novo"],
)


@{var}.task(retries=2, timeout=300, description="Busca os dados de origem.")
def extrair(ctx):
    ctx.log(f"Processando {{ctx.params['dias']}} dia(s)")
    return {{"linhas": 0}}


@{var}.task(depends_on=[extrair], description="Grava o resultado no destino.")
def carregar(ctx):
    linhas = ctx.upstream["extrair"]["linhas"]
    ctx.log(f"{{linhas}} linha(s) gravada(s)")
    return {{"gravadas": linhas}}
"""


def cmd_new(args: argparse.Namespace) -> int:
    from .schedule import validate_cron
    from .sdk import NAME_RE

    name = args.name.strip().lower()
    if not NAME_RE.match(name):
        raise SystemExit("Use um nome com letras minúsculas, números e _ (ex.: relatorio_semanal).")
    if args.schedule:
        try:
            validate_cron(args.schedule)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
    folder = _folder(args)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.py"
    if path.exists() and not args.force:
        raise SystemExit(f"{path} já existe. Use --force para sobrescrever.")
    schedule = f'    schedule="{args.schedule}",\n    timezone="{args.timezone}",\n' if args.schedule else ""
    path.write_text(TEMPLATE.format(var="wf", name=name, schedule=schedule), encoding="utf-8")
    print(f"Workflow criado em {path}")
    print(f"Teste agora, sem servidor: delco dev run {name}")
    return 0


def _folder(args: argparse.Namespace) -> Path:
    if getattr(args, "folder", None):
        return Path(args.folder)
    from .config import get_settings

    return get_settings().workflows_dir


def cmd_dev_run(args: argparse.Namespace) -> int:
    from datetime import datetime

    from .loader import load_folder
    from .sdk import WorkflowError

    folder = _folder(args)
    result = load_folder(folder)
    wf = result.workflows.get(args.workflow)
    if wf is None:
        for path, err in result.errors.items():
            print(f"✗ {path}\n{err.rstrip()}", file=sys.stderr)
        raise SystemExit(f"Workflow {args.workflow!r} não encontrado em {folder}.")
    params = parse_params(args.param, args.params_json)
    logical = datetime.fromisoformat(args.date) if args.date else None
    symbols = {"succeeded": "✓", "failed": "✗", "skipped": "–"}
    quiet = wants_json(args)

    def on_event(kind: str, data: dict[str, Any]) -> None:
        if quiet:
            return
        if kind == "start":
            print(f"▶ {data['task']}", flush=True)
        elif kind == "log" and not args.quiet:
            print(f"    [{data['level']}] {data['message']}", flush=True)
        elif kind == "error":
            print(f"    tentativa {data['attempt']}: {data['error']}", flush=True)
        elif kind == "skipped":
            print(f"– {data['task']} · pulada (dependência sem sucesso)", flush=True)
        elif kind == "end":
            word = STATUS_PT.get(data["status"], data["status"])
            reason = f" · {data['error']}" if data.get("error") and data["status"] != "succeeded" else ""
            print(f"{symbols.get(data['status'], '?')} {data['task']} · {word} · {duration(data['duration_ms'])}{reason}", flush=True)

    try:
        run = wf.run_local(params, logical_date=logical, retries=not args.no_retries, on_event=on_event)
    except WorkflowError as exc:
        raise SystemExit(f"Parâmetros inválidos: {exc}") from None
    if quiet:
        return emit_json(
            {
                "workflow": run.workflow,
                "status": run.status,
                "params": run.params,
                "duration_ms": run.duration_ms,
                "tasks": {
                    n: {"status": t.status, "attempts": t.attempts, "output": t.output, "error": t.error}
                    for n, t in run.tasks.items()
                },
            }
        )
    print(f"\nResultado: {STATUS_PT.get(run.status, run.status)} em {duration(run.duration_ms)}")
    if args.output:
        print(json.dumps(run.outputs, ensure_ascii=False, indent=2, default=str))
    return 0 if run.succeeded else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    _django()
    from . import doctor
    from .bus import Bus
    from .config import get_settings

    settings = get_settings()
    bus = Bus(settings.redis_url)
    checks = doctor.run_checks(settings, bus)
    if wants_json(args):
        emit_json({"status": doctor.summary(checks), "checks": [c.as_dict() for c in checks]})
    else:
        icons = {"ok": "✓", "warn": "!", "error": "✗"}
        group = None
        for c in checks:
            if c.group != group:
                group = c.group
                print(f"\n{group.upper()}")
            print(f"  {icons[c.status]} {c.label}: {c.message}")
            if c.hint and c.status != "ok":
                print(f"      → {c.hint}")
        word = {"ok": "Tudo certo.", "warn": "Funciona, com avisos.", "error": "Há problemas a corrigir."}
        print(f"\n{word[doctor.summary(checks)]}")
    status = doctor.summary(checks)
    return 1 if status == "error" or (args.strict and status == "warn") else 0


def cmd_config(args: argparse.Namespace) -> int:
    from .config import get_settings

    data = get_settings().public()
    if wants_json(args):
        return emit_json(data)
    width = max(len(k) for k in data)
    for key, value in data.items():
        shown = ", ".join(value) if isinstance(value, list) else value
        print(f"{key.ljust(width)}  {shown}")
    return 0


def cmd_healthcheck(args: argparse.Namespace) -> int:
    from .config import get_settings

    settings = get_settings()
    if args.role == "api":
        port = os.environ.get("DELCO_API_PORT", "8000")
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=3)
        except httpx.HTTPError as exc:
            print(f"API sem resposta: {exc}")
            return 1
        print(r.text)
        return 0 if r.status_code == 200 else 1
    path = Path(settings.health_dir) / f"delco-{args.role}.health"
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        print(f"{args.role}: sem sinal em {path}")
        return 1
    limit = args.max_age or max(30.0, settings.heartbeat_seconds * 6)
    ok = age <= limit
    print(f"{args.role}: último sinal há {age:.0f} s (limite {limit:.0f} s)")
    return 0 if ok else 1


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
    client = client_from(args)
    if args.action in ("pause", "resume"):
        if not args.worker_id:
            raise SystemExit(f"Informe o worker: delco workers {args.action} <id>")
        client.request("POST", f"/api/workers/{args.worker_id}/{args.action}")
        word = "pausado: termina o que está rodando e não pega tarefas novas" if args.action == "pause" else "retomado"
        print(f"Worker {args.worker_id} {word}.")
        return 0
    rows = client.request("GET", "/api/workers")
    if wants_json(args):
        return emit_json(rows)
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


def _django():
    from .db import setup

    setup()


def cmd_api(args: argparse.Namespace) -> int:
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


def cmd_scheduler(args: argparse.Namespace) -> int:
    return _call("scheduler")


def cmd_worker(args: argparse.Namespace) -> int:
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="delco", description="Delco: motor de orquestração de workflows.")
    p.add_argument("--version", action="version", version=f"delco {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="comando")
    statuses = ["queued", "running", "succeeded", "failed", "cancelled"]
    triggers = ["schedule", "manual", "api", "cli", "webhook", "backfill"]

    def client_cmd(name: str, help_: str, fn, *, json_flag: bool = False) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--url", help="Endereço do Delco (padrão: DELCO_URL ou http://localhost:8080)")
        if json_flag:
            sp.add_argument("--json", action="store_true", help="saída em JSON (para scripts)")
        sp.set_defaults(func=fn)
        return sp

    def add_params(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("-p", "--param", action="append", default=[], metavar="CHAVE=VALOR")
        sp.add_argument("--params-json", help="parâmetros como objeto JSON")

    s = sub.add_parser("api", help="sobe a API e o painel web")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=int(os.environ.get("DELCO_API_PORT", "8000")))
    s.set_defaults(func=cmd_api)
    sub.add_parser("scheduler", help="sobe o agendador").set_defaults(func=cmd_scheduler)
    s = sub.add_parser("worker", help="sobe um worker")
    s.add_argument("--queues", help="filas separadas por vírgula (padrão: DELCO_WORKER_QUEUES)")
    s.add_argument("--concurrency", type=int, help="tarefas simultâneas (padrão: DELCO_WORKER_CONCURRENCY)")
    s.add_argument("--name", help="nome do worker (padrão: host + pid)")
    s.set_defaults(func=cmd_worker)
    sub.add_parser("migrate", help="cria ou atualiza as tabelas").set_defaults(func=cmd_migrate)
    sub.add_parser("seed", help="cria contas e histórico de demonstração").set_defaults(func=cmd_seed)
    s = sub.add_parser("validate", help="confere os arquivos de workflow sem rodar nada")
    s.add_argument("folder", nargs="?", help="pasta (padrão: DELCO_WORKFLOWS_DIR)")
    s.set_defaults(func=cmd_validate)
    s = sub.add_parser("doctor", help="confere configuração, banco, Redis, processos e segurança")
    s.add_argument("--json", action="store_true")
    s.add_argument("--strict", action="store_true", help="código de saída 1 também para avisos")
    s.set_defaults(func=cmd_doctor)
    s = sub.add_parser("config", help="mostra a configuração em uso (segredos mascarados)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_config)
    s = sub.add_parser("healthcheck", help="confere se um processo local está vivo (para o Docker)")
    s.add_argument("role", choices=["api", "scheduler", "worker"])
    s.add_argument("--max-age", type=float, help="segundos sem sinal até considerar parado")
    s.set_defaults(func=cmd_healthcheck)
    s = sub.add_parser("new", help="cria um arquivo de workflow a partir de um modelo")
    s.add_argument("name", help="nome do workflow, ex.: relatorio_semanal")
    s.add_argument("--schedule", help="cron de 5 campos, ex.: '0 7 * * 1-5'")
    s.add_argument("--timezone", default="America/Sao_Paulo")
    s.add_argument("--folder", help="pasta (padrão: DELCO_WORKFLOWS_DIR)")
    s.add_argument("--force", action="store_true", help="sobrescreve se o arquivo já existir")
    s.set_defaults(func=cmd_new)
    s = sub.add_parser("dev", help="ferramentas para escrever workflows")
    ds = s.add_subparsers(dest="dev_command", required=True, metavar="ação")
    s = ds.add_parser("run", help="roda um workflow aqui mesmo, sem banco, Redis nem workers")
    s.add_argument("workflow")
    add_params(s)
    s.add_argument("--folder", help="pasta (padrão: DELCO_WORKFLOWS_DIR)")
    s.add_argument("--date", help="data lógica (ISO), ex.: 2026-11-01T07:00")
    s.add_argument("--no-retries", action="store_true", help="não repete tarefas que falharem")
    s.add_argument("--quiet", action="store_true", help="esconde as linhas de log das tarefas")
    s.add_argument("--output", action="store_true", help="mostra o resultado de cada tarefa no fim")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_dev_run)
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
    client_cmd("workflows", "lista os workflows", cmd_workflows, json_flag=True)
    s = client_cmd("show", "mostra um workflow: agenda, parâmetros e tarefas", cmd_show, json_flag=True)
    s.add_argument("workflow")
    s = client_cmd("versions", "histórico de versões de um workflow", cmd_versions, json_flag=True)
    s.add_argument("workflow")
    s = client_cmd("trigger", "dispara uma execução", cmd_trigger)
    s.add_argument("workflow")
    add_params(s)
    s.add_argument("--note", help="observação gravada na execução")
    s.add_argument("--wait", action="store_true", help="acompanha até terminar (código de saída 1 se falhar)")
    s.add_argument("--dry-run", action="store_true", help="só confere os parâmetros, sem criar a execução")
    s = client_cmd("backfill", "reprocessa um período (uma execução por horário da agenda)", cmd_backfill, json_flag=True)
    s.add_argument("workflow")
    s.add_argument("--from", dest="start", required=True, help="data inicial, ex.: 2026-10-01")
    s.add_argument("--to", dest="end", required=True, help="data final, ex.: 2026-10-07")
    add_params(s)
    s.add_argument("--note")
    s.add_argument("--dry-run", action="store_true", help="só lista os horários, sem criar nada")
    s.add_argument("--all", action="store_true", help="refaz também horários que já têm execução")
    s = client_cmd("runs", "lista execuções", cmd_runs, json_flag=True)
    s.add_argument("--workflow")
    s.add_argument("--status", choices=statuses)
    s.add_argument("--trigger", choices=triggers, help="origem")
    s.add_argument("--since", type=int, help="só as criadas nos últimos N dias")
    s.add_argument("-q", "--search", dest="q", help="número, workflow ou observação")
    s.add_argument("--limit", type=int, default=20)
    s = client_cmd("run", "mostra uma execução e suas tarefas", cmd_run, json_flag=True)
    s.add_argument("run_id", type=int)
    s = client_cmd("logs", "mostra o log de uma tarefa", cmd_logs)
    s.add_argument("run_id", type=int)
    s.add_argument("task")
    s.add_argument("--attempt", type=int, help="número da tentativa (padrão: a última)")
    s.add_argument("-f", "--follow", action="store_true", help="acompanha o log ao vivo até a tarefa terminar")
    s.add_argument("--level", action="append", choices=["debug", "info", "warning", "error"], help="só estes níveis")
    s = client_cmd("cancel", "cancela uma execução", cmd_cancel)
    s.add_argument("run_id", type=int)
    s = client_cmd("retry", "retoma uma execução a partir da falha", cmd_retry)
    s.add_argument("run_id", type=int)
    s.add_argument("--from", dest="from_task", help="refaz a partir desta tarefa (e as seguintes)")
    s.add_argument("--wait", action="store_true")
    s = client_cmd("mark", "marca uma tarefa como sucesso, falha ou pulada", cmd_mark)
    s.add_argument("run_id", type=int)
    s.add_argument("task")
    s.add_argument("--status", choices=["succeeded", "failed", "skipped"], default="succeeded")
    s = client_cmd("note", "grava ou apaga a observação de uma execução", cmd_note)
    s.add_argument("run_id", type=int)
    s.add_argument("text", nargs="?", default="")
    s = client_cmd("pause", "pausa o agendamento de um workflow", lambda a: cmd_pause(a, True))
    s.add_argument("workflow")
    s = client_cmd("resume", "retoma o agendamento de um workflow", lambda a: cmd_pause(a, False))
    s.add_argument("workflow")
    s = client_cmd("workers", "lista, pausa ou retoma workers", cmd_workers, json_flag=True)
    s.add_argument("action", nargs="?", choices=["list", "pause", "resume"], default="list")
    s.add_argument("worker_id", nargs="?")
    s = client_cmd("export", "exporta execuções para CSV", cmd_export)
    s.add_argument("file", help="arquivo de saída, ex.: execucoes.csv")
    s.add_argument("--workflow")
    s.add_argument("--status", choices=statuses)
    s.add_argument("--trigger", choices=triggers)
    s.add_argument("--since", type=int, help="só as criadas nos últimos N dias")
    s = client_cmd("upcoming", "lista as próximas execuções agendadas", cmd_upcoming, json_flag=True)
    s.add_argument("--hours", type=int, default=24)
    client_cmd("integrations", "lista as integrações", cmd_integrations, json_flag=True)
    return p


def main(argv: list[str] | None = None) -> int:
    from .config import ConfigError

    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stdout = open(os.devnull, "w")
        return 0

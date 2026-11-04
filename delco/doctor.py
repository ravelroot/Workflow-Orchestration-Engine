from __future__ import annotations

import platform
import sys
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from . import __version__
from .config import Settings

OK, WARN, ERROR = "ok", "warn", "error"


@dataclass
class Check:
    id: str
    group: str
    label: str
    status: str
    message: str
    hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _config_checks(s: Settings) -> list[Check]:
    out: list[Check] = []
    prod = s.is_production
    if s.secret_key_from_env:
        out.append(Check("secret_key", "configuração", "Chave secreta", OK, "Definida em DELCO_SECRET_KEY."))
    elif prod:
        out.append(Check("secret_key", "configuração", "Chave secreta", WARN,
                         "DELCO_SECRET_KEY não foi definida; cada processo sorteia uma.",
                         "Defina DELCO_SECRET_KEY no .env com um valor longo e aleatório."))
    else:
        out.append(Check("secret_key", "configuração", "Chave secreta", OK,
                         "Sorteada a cada processo (normal em desenvolvimento)."))
    if prod and "*" in s.allowed_hosts:
        out.append(Check("allowed_hosts", "configuração", "Hosts permitidos", WARN,
                         "DELCO_ALLOWED_HOSTS aceita qualquer domínio.",
                         "Use o domínio do painel, como delco.suaempresa.com."))
    else:
        out.append(Check("allowed_hosts", "configuração", "Hosts permitidos", OK, ", ".join(s.allowed_hosts)))
    host = urlparse(s.public_url).hostname or ""
    if prod and host in ("localhost", "127.0.0.1"):
        out.append(Check("public_url", "configuração", "Endereço público", WARN,
                         f"DELCO_PUBLIC_URL aponta para {s.public_url}; os links das notificações não vão abrir.",
                         "Use o endereço que o time acessa, como https://delco.suaempresa.com."))
    else:
        out.append(Check("public_url", "configuração", "Endereço público", OK, s.public_url))
    if prod and s.seed:
        out.append(Check("seed", "configuração", "Dados de demonstração", WARN,
                         "DELCO_SEED está ligado em produção.",
                         "Desligue com DELCO_SEED=false; ele só age num banco vazio, mas evita surpresas."))
    if s.smtp_enabled:
        out.append(Check("smtp", "configuração", "E-mail (SMTP)", OK, f"{s.smtp_host}:{s.smtp_port} ({s.smtp_security})"))
    else:
        out.append(Check("smtp", "configuração", "E-mail (SMTP)", OK, "Não configurado: avisos por e-mail ficam indisponíveis."))
    return out


def _database_checks() -> list[Check]:
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    out: list[Check] = []
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
    except Exception as exc:
        return [Check("database", "componentes", "MySQL", ERROR, f"Sem conexão: {exc}",
                      "Confira DELCO_DATABASE_URL e se o MySQL está de pé.")]
    major = tuple(int(x) for x in version.split("-")[0].split(".")[:2] if x.isdigit())
    status = OK if major >= (8, 4) else ERROR
    out.append(Check("database", "componentes", "MySQL", status, f"Versão {version}",
                     "" if status == OK else "O Delco precisa do MySQL 8.4 ou mais novo."))
    try:
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        pending = [m.name for m, backwards in plan if not backwards]
    except Exception as exc:
        pending = [f"? ({exc})"]
    if pending:
        out.append(Check("migrations", "componentes", "Migrações", ERROR, f"{len(pending)} pendente(s): {', '.join(pending)}",
                         "Rode delco migrate (a API também aplica ao subir)."))
    else:
        out.append(Check("migrations", "componentes", "Migrações", OK, "Banco atualizado."))
    return out


def _redis_checks(bus) -> list[Check]:
    if bus is None or not bus.ping():
        return [Check("redis", "componentes", "Redis", ERROR, "Sem conexão.", "Confira DELCO_REDIS_URL e se o Redis está de pé.")]
    info = bus.server_info()
    out = [Check("redis", "componentes", "Redis", OK, f"Versão {info.get('version', '?')} · memória {info.get('used_memory', '?')}")]
    if not info.get("aof_enabled"):
        out.append(Check("redis_aof", "componentes", "Persistência do Redis", WARN,
                         "AOF desligado: se o Redis reiniciar, as mensagens são republicadas pelo agendador.",
                         "Não é obrigatório; ligue com --appendonly yes para reiniciar mais rápido."))
    return out


def _runtime_checks(bus) -> list[Check]:
    from .models import IntegrationDelivery, TaskInstance, Worker, Workflow
    from .schedule import utcnow

    out: list[Check] = []
    schedulers = bus.schedulers() if bus is not None else []
    if schedulers:
        out.append(Check("scheduler", "processos", "Agendador", OK, f"{len(schedulers)} ativo(s): " + ", ".join(x["id"] for x in schedulers)))
        broken = [x for x in schedulers if x.get("last_error")]
        if broken:
            out.append(Check("scheduler_errors", "processos", "Erros do agendador", WARN, broken[0]["last_error"],
                             "Veja os logs com docker compose logs scheduler."))
    else:
        out.append(Check("scheduler", "processos", "Agendador", ERROR, "Nenhum agendador ativo: nada novo começa a rodar.",
                         "Suba com delco scheduler ou docker compose up -d scheduler."))
    workers = list(Worker.objects.exclude(status="offline").values("id", "queues", "status"))
    active = [w for w in workers if w["status"] == "online"]
    if active:
        out.append(Check("workers", "processos", "Workers", OK, f"{len(active)} online" + (f", {len(workers) - len(active)} pausado(s) ou parando" if len(workers) > len(active) else "")))
    else:
        out.append(Check("workers", "processos", "Workers", ERROR, "Nenhum worker consumindo as filas.",
                         "Suba com delco worker ou docker compose up -d worker."))
    consumed = {q for w in active for q in (w["queues"] or [])}
    used: set[str] = set()
    for wf in Workflow.objects.filter(is_active=True).select_related("current_version"):
        if wf.current_version:
            used |= {t["queue"] for t in wf.current_version.graph["tasks"]}
    waiting = set(TaskInstance.objects.filter(status="queued").values_list("queue", flat=True).distinct())
    orphan = sorted((used | waiting) - consumed) if active else []
    if orphan:
        out.append(Check("queues", "processos", "Filas sem worker", WARN, "Nenhum worker online consome: " + ", ".join(orphan),
                         "Inclua a fila em DELCO_WORKER_QUEUES de algum worker."))
    elif active:
        out.append(Check("queues", "processos", "Filas", OK, "Todas as filas usadas têm worker: " + ", ".join(sorted(used)) if used else "Nenhuma fila em uso."))
    errors = bus.load_errors() if bus is not None else {}
    total = Workflow.objects.filter(is_active=True).count()
    if errors:
        out.append(Check("workflows", "workflows", "Arquivos de workflow", WARN, f"{len(errors)} arquivo(s) com erro: " + ", ".join(sorted(errors)),
                         "Rode delco validate para ver o erro completo."))
    else:
        out.append(Check("workflows", "workflows", "Arquivos de workflow", OK, f"{total} workflow(s) carregado(s)."))
    day = utcnow() - timedelta(hours=24)
    failed = IntegrationDelivery.objects.filter(status="failed", created_at__gt=day).count()
    if failed:
        out.append(Check("deliveries", "integrações", "Entregas de notificação", WARN, f"{failed} entrega(s) falharam nas últimas 24 h.",
                         "Abra Integrações e confira o histórico de entregas."))
    else:
        out.append(Check("deliveries", "integrações", "Entregas de notificação", OK, "Nenhuma falha nas últimas 24 h."))
    return out


def _demo_accounts(s: Settings) -> list[Check]:
    from .models import User
    from .security import verify_password
    from .seed import DEMO_PASSWORD, DEMO_USERS

    emails = [e for e, _, _ in DEMO_USERS]
    exposed = [u.email for u in User.objects.filter(email__in=emails, is_active=True) if verify_password(DEMO_PASSWORD, u.password_hash)]
    if not exposed:
        return [Check("demo_accounts", "segurança", "Contas de demonstração", OK, "Nenhuma conta com a senha de demonstração.")]
    status = ERROR if s.is_production else WARN
    return [Check("demo_accounts", "segurança", "Contas de demonstração", status,
                  f"{len(exposed)} conta(s) ativas com a senha pública Delco@2026: " + ", ".join(exposed),
                  "Troque a senha ou desative essas contas em Usuários antes de expor o painel.")]


def run_checks(settings: Settings, bus=None, *, deep: bool = True) -> list[Check]:
    checks = _config_checks(settings)
    db = _database_checks()
    checks += db
    checks += _redis_checks(bus)
    if deep and all(c.status != ERROR for c in db):
        try:
            checks += _runtime_checks(bus)
            checks += _demo_accounts(settings)
        except Exception as exc:
            checks.append(Check("runtime", "processos", "Estado do motor", ERROR, f"Não foi possível consultar: {exc}"))
    return checks


def summary(checks: list[Check]) -> str:
    if any(c.status == ERROR for c in checks):
        return ERROR
    if any(c.status == WARN for c in checks):
        return WARN
    return OK


def versions() -> dict[str, str]:
    import django

    return {
        "delco": __version__,
        "python": sys.version.split()[0],
        "django": django.get_version(),
        "platform": platform.platform(terse=True),
    }

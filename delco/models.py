"""Modelos do Django: a fonte da verdade de todo o estado do Delco.

As tabelas têm nomes fixos (``db_table``) e as restrições ficam no próprio
MySQL: CHECK, índices, chave única por data lógica (índice funcional) e ON DELETE
no banco (``DB_CASCADE``/``DB_SET_NULL``). Assim a garantia vale mesmo para
quem escreve fora do ORM, e o agendador e os workers podem confiar no banco
para resolver disputas.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Case, F, Q, When
from django.db.models.functions import Lower, Now
from django.utils import timezone as tz


class Role(models.TextChoices):
    ADMIN = "admin", "Administrador"
    OPERATOR = "operator", "Operador"
    VIEWER = "viewer", "Leitura"


class TokenKind(models.TextChoices):
    SESSION = "session", "Sessão"
    PERSONAL = "personal", "Pessoal"


class RunStatus(models.TextChoices):
    QUEUED = "queued", "Na fila"
    RUNNING = "running", "Rodando"
    SUCCEEDED = "succeeded", "Sucesso"
    FAILED = "failed", "Falhou"
    CANCELLED = "cancelled", "Cancelada"


class Trigger(models.TextChoices):
    SCHEDULE = "schedule", "Agenda"
    MANUAL = "manual", "Painel"
    API = "api", "API"
    CLI = "cli", "CLI"


class WorkerStatus(models.TextChoices):
    ONLINE = "online", "Online"
    STOPPING = "stopping", "Parando"
    OFFLINE = "offline", "Offline"


class TaskStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    QUEUED = "queued", "Na fila"
    RUNNING = "running", "Rodando"
    RETRY_WAIT = "retry_wait", "Aguardando nova tentativa"
    SUCCEEDED = "succeeded", "Sucesso"
    FAILED = "failed", "Falhou"
    SKIPPED = "skipped", "Pulada"
    CANCELLED = "cancelled", "Cancelada"


class TriggerRule(models.TextChoices):
    ALL_SUCCESS = "all_success", "Todas com sucesso"
    ALL_DONE = "all_done", "Todas terminadas"
    ONE_FAILED = "one_failed", "Alguma falhou"


class AttemptStatus(models.TextChoices):
    RUNNING = "running", "Rodando"
    SUCCEEDED = "succeeded", "Sucesso"
    FAILED = "failed", "Falhou"
    TIMED_OUT = "timed_out", "Tempo esgotado"
    LOST = "lost", "Perdida"
    CANCELLED = "cancelled", "Cancelada"


class LogLevel(models.TextChoices):
    DEBUG = "debug", "Debug"
    INFO = "info", "Info"
    WARNING = "warning", "Aviso"
    ERROR = "error", "Erro"


def _in(field: str, choices: type[models.TextChoices]) -> Q:
    return Q(**{f"{field}__in": choices.values})


# =========================================================================== acesso
class User(models.Model):
    """Conta do painel. Senha guardada com o hasher scrypt do Django."""

    email = models.CharField(max_length=254)
    name = models.CharField(max_length=120)
    password_hash = models.CharField(max_length=255)
    role = models.CharField(max_length=16, choices=Role.choices)
    is_active = models.BooleanField(default=True, db_default=True)
    failed_logins = models.IntegerField(default=0, db_default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=tz.now, db_default=Now())
    updated_at = models.DateTimeField(default=tz.now, db_default=Now())

    class Meta:
        db_table = "users"
        constraints = [
            models.UniqueConstraint(Lower("email"), name="users_email_key"),
            models.CheckConstraint(condition=_in("role", Role), name="users_role_check"),
            models.CheckConstraint(condition=Q(failed_logins__gte=0), name="users_failed_logins_check"),
        ]

    def __str__(self) -> str:
        return self.email


class ApiToken(models.Model):
    """Token opaco: só o SHA-256 fica no banco."""

    user = models.ForeignKey(User, on_delete=models.DB_CASCADE, related_name="tokens")
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=TokenKind.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=32)
    created_at = models.DateTimeField(default=tz.now, db_default=Now())
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "api_tokens"
        indexes = [models.Index(fields=["user"], name="api_tokens_user_idx")]
        constraints = [models.CheckConstraint(condition=_in("kind", TokenKind), name="api_tokens_kind_check")]


class AuditLog(models.Model):
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.DB_SET_NULL, related_name="audit_entries"
    )
    action = models.CharField(max_length=64)
    target = models.CharField(max_length=255, null=True, blank=True)
    details = models.JSONField(default=dict, db_default={})
    created_at = models.DateTimeField(default=tz.now, db_default=Now())

    class Meta:
        db_table = "audit_log"
        indexes = [models.Index(fields=["-created_at"], name="audit_log_created_idx")]


# ====================================================================== definições
class Workflow(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(default="", db_default="")
    schedule = models.CharField(max_length=120, null=True, blank=True)
    timezone = models.CharField(max_length=64, default="America/Sao_Paulo", db_default="America/Sao_Paulo")
    max_active_runs = models.IntegerField(default=1, db_default=1)
    default_params = models.JSONField(default=dict, db_default={})
    tags = models.JSONField(default=list, db_default=[])
    source_file = models.CharField(max_length=500, null=True, blank=True)
    current_version = models.ForeignKey(
        "WorkflowVersion", null=True, blank=True, on_delete=models.DB_SET_NULL, related_name="+"
    )
    is_paused = models.BooleanField(default=False, db_default=False)
    is_active = models.BooleanField(default=True, db_default=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=tz.now, db_default=Now())
    updated_at = models.DateTimeField(default=tz.now, db_default=Now())

    class Meta:
        db_table = "workflows"
        indexes = [
            models.Index(fields=["next_run_at"], name="workflows_due_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(max_active_runs__gt=0), name="workflows_max_active_runs_check")
        ]

    def __str__(self) -> str:
        return self.name


class WorkflowVersion(models.Model):
    """Cada mudança no grafo vira uma versão nova; execuções antigas guardam a sua."""

    workflow = models.ForeignKey(
        Workflow, on_delete=models.DB_CASCADE, related_name="versions", db_index=False
    )
    version = models.IntegerField()
    fingerprint = models.CharField(max_length=64)
    graph = models.JSONField()
    created_at = models.DateTimeField(default=tz.now, db_default=Now())

    class Meta:
        db_table = "workflow_versions"
        constraints = [
            models.UniqueConstraint(fields=["workflow", "version"], name="workflow_versions_version_key"),
            models.UniqueConstraint(
                fields=["workflow", "fingerprint"], name="workflow_versions_fingerprint_key"
            ),
            models.CheckConstraint(condition=Q(version__gt=0), name="workflow_versions_version_check"),
        ]


# ======================================================================= execuções
class WorkflowRun(models.Model):
    workflow = models.ForeignKey(Workflow, on_delete=models.DB_CASCADE, related_name="runs", db_index=False)
    version = models.ForeignKey(WorkflowVersion, on_delete=models.DO_NOTHING, related_name="runs")
    status = models.CharField(
        max_length=16, choices=RunStatus.choices, default=RunStatus.QUEUED, db_default=RunStatus.QUEUED
    )
    trigger = models.CharField(max_length=16, choices=Trigger.choices)
    triggered_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.DB_SET_NULL, related_name="runs"
    )
    logical_date = models.DateTimeField(null=True, blank=True)
    params = models.JSONField(default=dict, db_default={})
    note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=tz.now, db_default=Now())
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=tz.now, db_default=Now())

    class Meta:
        db_table = "workflow_runs"
        indexes = [
            models.Index(fields=["workflow", "-id"], name="workflow_runs_workflow_idx"),
            models.Index(fields=["status", "id"], name="workflow_runs_open_idx"),
            models.Index(fields=["finished_at"], name="workflow_runs_finished_idx"),
        ]
        constraints = [
            # Uma única execução agendada por data lógica, mesmo com vários agendadores. O MySQL
            # não tem índice parcial: o índice funcional usa a data só quando trigger = 'schedule'
            # (nas outras fica NULL, e NULL não colide).
            models.UniqueConstraint(
                F("workflow"),
                Case(When(trigger="schedule", then=F("logical_date"))),
                name="workflow_runs_schedule_key",
            ),
            models.CheckConstraint(condition=_in("status", RunStatus), name="workflow_runs_status_check"),
            models.CheckConstraint(condition=_in("trigger", Trigger), name="workflow_runs_trigger_check"),
        ]


class Worker(models.Model):
    id = models.CharField(max_length=200, primary_key=True)
    hostname = models.CharField(max_length=255)
    pid = models.IntegerField()
    queues = models.JSONField()
    concurrency = models.IntegerField()
    version = models.CharField(max_length=32)
    status = models.CharField(
        max_length=16,
        choices=WorkerStatus.choices,
        default=WorkerStatus.ONLINE,
        db_default=WorkerStatus.ONLINE,
    )
    started_at = models.DateTimeField(default=tz.now, db_default=Now())
    last_heartbeat_at = models.DateTimeField(default=tz.now, db_default=Now())
    stopped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "workers"
        constraints = [
            models.CheckConstraint(condition=Q(concurrency__gt=0), name="workers_concurrency_check"),
            models.CheckConstraint(condition=_in("status", WorkerStatus), name="workers_status_check"),
        ]


class TaskInstance(models.Model):
    run = models.ForeignKey(WorkflowRun, on_delete=models.DB_CASCADE, related_name="tasks", db_index=False)
    task_name = models.CharField(max_length=100)
    status = models.CharField(
        max_length=16, choices=TaskStatus.choices, default=TaskStatus.PENDING, db_default=TaskStatus.PENDING
    )
    upstream = models.JSONField(default=list, db_default=[])
    trigger_rule = models.CharField(
        max_length=16,
        choices=TriggerRule.choices,
        default=TriggerRule.ALL_SUCCESS,
        db_default=TriggerRule.ALL_SUCCESS,
    )
    queue = models.CharField(max_length=100, default="default", db_default="default")
    retries = models.IntegerField(default=2, db_default=2)
    max_attempts = models.IntegerField(default=3, db_default=3)
    retry_delay = models.FloatField(default=5, db_default=5)
    retry_backoff = models.FloatField(default=2, db_default=2)
    max_retry_delay = models.FloatField(default=600, db_default=600)
    timeout_seconds = models.FloatField(null=True, blank=True)
    attempts = models.IntegerField(default=0, db_default=0)
    worker = models.ForeignKey(
        Worker, null=True, blank=True, on_delete=models.DB_SET_NULL, related_name="tasks"
    )
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    queue_message_id = models.CharField(max_length=64, null=True, blank=True)
    cancel_requested = models.BooleanField(default=False, db_default=False)
    progress = models.SmallIntegerField(null=True, blank=True)
    output = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=tz.now, db_default=Now())

    class Meta:
        db_table = "task_instances"
        indexes = [
            models.Index(fields=["status", "run"], name="task_instances_open_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="task_instances_lease_idx"),
            models.Index(fields=["finished_at"], name="task_instances_finished_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["run", "task_name"], name="task_instances_run_task_key"),
            models.CheckConstraint(condition=_in("status", TaskStatus), name="task_instances_status_check"),
            models.CheckConstraint(
                condition=_in("trigger_rule", TriggerRule), name="task_instances_trigger_rule_check"
            ),
            models.CheckConstraint(condition=Q(retries__gte=0), name="task_instances_retries_check"),
            models.CheckConstraint(
                condition=Q(max_attempts__gte=1), name="task_instances_max_attempts_check"
            ),
            models.CheckConstraint(condition=Q(attempts__gte=0), name="task_instances_attempts_check"),
            models.CheckConstraint(
                condition=Q(progress__isnull=True) | Q(progress__gte=0, progress__lte=100),
                name="task_instances_progress_check",
            ),
        ]


class TaskAttempt(models.Model):
    task_instance = models.ForeignKey(
        TaskInstance, on_delete=models.DB_CASCADE, related_name="attempt_set", db_index=False
    )
    number = models.IntegerField()
    worker = models.ForeignKey(
        Worker, null=True, blank=True, on_delete=models.DB_SET_NULL, related_name="attempts"
    )
    status = models.CharField(
        max_length=16,
        choices=AttemptStatus.choices,
        default=AttemptStatus.RUNNING,
        db_default=AttemptStatus.RUNNING,
    )
    started_at = models.DateTimeField(default=tz.now, db_default=Now())
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    traceback = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "task_attempts"
        constraints = [
            models.UniqueConstraint(fields=["task_instance", "number"], name="task_attempts_number_key"),
            models.CheckConstraint(condition=Q(number__gt=0), name="task_attempts_number_check"),
            models.CheckConstraint(condition=_in("status", AttemptStatus), name="task_attempts_status_check"),
        ]


class TaskLog(models.Model):
    attempt = models.ForeignKey(TaskAttempt, on_delete=models.DB_CASCADE, related_name="logs", db_index=False)
    logged_at = models.DateTimeField(default=tz.now, db_default=Now())
    level = models.CharField(max_length=16, choices=LogLevel.choices)
    message = models.TextField()

    class Meta:
        db_table = "task_logs"
        indexes = [models.Index(fields=["attempt", "id"], name="task_logs_attempt_idx")]
        constraints = [models.CheckConstraint(condition=_in("level", LogLevel), name="task_logs_level_check")]

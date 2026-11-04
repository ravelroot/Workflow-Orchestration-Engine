from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("delco", "0002_integracoes"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="integration",
            name="integrations_kind_check",
        ),
        migrations.RemoveConstraint(
            model_name="taskattempt",
            name="task_attempts_status_check",
        ),
        migrations.RemoveConstraint(
            model_name="worker",
            name="workers_status_check",
        ),
        migrations.RemoveConstraint(
            model_name="workflowrun",
            name="workflow_runs_trigger_check",
        ),
        migrations.AddField(
            model_name="integration",
            name="config",
            field=models.JSONField(db_default={}, default=dict),
        ),
        migrations.AddField(
            model_name="user",
            name="preferences",
            field=models.JSONField(db_default={}, default=dict),
        ),
        migrations.AddField(
            model_name="workflow",
            name="params_schema",
            field=models.JSONField(db_default={}, default=dict),
        ),
        migrations.AddField(
            model_name="workflow",
            name="source_code",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflow",
            name="source_hash",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="workflowrun",
            name="error",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowversion",
            name="source_code",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="integration",
            name="kind",
            field=models.CharField(
                choices=[
                    ("slack", "Slack"),
                    ("discord", "Discord"),
                    ("teams", "Microsoft Teams"),
                    ("google_chat", "Google Chat"),
                    ("telegram", "Telegram"),
                    ("email", "E-mail"),
                    ("webhook", "Webhook genérico"),
                    ("trigger", "Gatilho por URL"),
                ],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="taskattempt",
            name="status",
            field=models.CharField(
                choices=[
                    ("running", "Rodando"),
                    ("succeeded", "Sucesso"),
                    ("failed", "Falhou"),
                    ("timed_out", "Tempo esgotado"),
                    ("lost", "Perdida"),
                    ("cancelled", "Cancelada"),
                    ("skipped", "Pulada"),
                ],
                db_default="running",
                default="running",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="worker",
            name="status",
            field=models.CharField(
                choices=[
                    ("online", "Online"),
                    ("paused", "Pausado"),
                    ("stopping", "Parando"),
                    ("offline", "Offline"),
                ],
                db_default="online",
                default="online",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="workflowrun",
            name="trigger",
            field=models.CharField(
                choices=[
                    ("schedule", "Agenda"),
                    ("manual", "Painel"),
                    ("api", "API"),
                    ("cli", "CLI"),
                    ("webhook", "Webhook"),
                    ("backfill", "Reprocessamento"),
                ],
                max_length=16,
            ),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["action", "-id"], name="audit_log_action_idx"),
        ),
        migrations.AddIndex(
            model_name="workflowrun",
            index=models.Index(
                fields=["workflow", "logical_date"], name="workflow_runs_logical_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="integration",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "kind__in",
                        [
                            "slack",
                            "discord",
                            "teams",
                            "google_chat",
                            "telegram",
                            "email",
                            "webhook",
                            "trigger",
                        ],
                    )
                ),
                name="integrations_kind_check",
            ),
        ),
        migrations.AddConstraint(
            model_name="taskattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "status__in",
                        [
                            "running",
                            "succeeded",
                            "failed",
                            "timed_out",
                            "lost",
                            "cancelled",
                            "skipped",
                        ],
                    )
                ),
                name="task_attempts_status_check",
            ),
        ),
        migrations.AddConstraint(
            model_name="worker",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("status__in", ["online", "paused", "stopping", "offline"])
                ),
                name="workers_status_check",
            ),
        ),
        migrations.AddConstraint(
            model_name="workflowrun",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "trigger__in",
                        ["schedule", "manual", "api", "cli", "webhook", "backfill"],
                    )
                ),
                name="workflow_runs_trigger_check",
            ),
        ),
    ]

from __future__ import annotations

from rest_framework import serializers

from ..models import RunStatus, Trigger


class LoginSerializer(serializers.Serializer):
    email = serializers.CharField(help_text="admin@delco.dev")
    password = serializers.CharField(trim_whitespace=False, help_text="Delco@2026")


class PasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False, allow_blank=True)
    new_password = serializers.CharField(trim_whitespace=False, allow_blank=True)


class TokenSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True, help_text="CLI do notebook")
    expires_in_days = serializers.IntegerField(required=False, allow_null=True, default=90)


class UserCreateSerializer(serializers.Serializer):
    email = serializers.CharField(allow_blank=True)
    name = serializers.CharField(allow_blank=True)
    role = serializers.CharField(help_text="admin | operator | viewer")
    password = serializers.CharField(trim_whitespace=False, allow_blank=True)


class UserPatchSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    role = serializers.CharField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False, allow_null=True)
    password = serializers.CharField(required=False, allow_null=True, allow_blank=True, trim_whitespace=False)
    unlock = serializers.BooleanField(required=False, allow_null=True)


class TriggerSerializer(serializers.Serializer):
    params = serializers.DictField(required=False, default=dict, help_text='{"dias": 1}')
    note = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=500)
    source = serializers.ChoiceField(choices=["manual", "api", "cli"], required=False, default="manual")
    dry_run = serializers.BooleanField(required=False, default=False)


class RetrySerializer(serializers.Serializer):
    from_task = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, help_text="transformar"
    )


class PageSerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1, default=1)
    size = serializers.IntegerField(min_value=1, max_value=200, default=50)


class RunsQuerySerializer(serializers.Serializer):
    workflow = serializers.CharField(required=False)
    status = serializers.ChoiceField(choices=RunStatus.values, required=False)
    trigger = serializers.ChoiceField(choices=Trigger.values, required=False)
    since = serializers.IntegerField(min_value=1, max_value=366, required=False, help_text="dias")
    q = serializers.CharField(required=False, allow_blank=True, max_length=100)
    page = serializers.IntegerField(min_value=1, default=1)
    size = serializers.IntegerField(min_value=1, max_value=100, default=25)


class InsightsQuerySerializer(serializers.Serializer):
    days = serializers.ChoiceField(choices=[7, 30], default=7)


class UpcomingQuerySerializer(serializers.Serializer):
    hours = serializers.IntegerField(min_value=1, max_value=168, default=24)


class IntegrationSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, help_text="Alertas do time de dados")
    kind = serializers.CharField(
        required=False, help_text="slack | discord | teams | google_chat | telegram | email | webhook | trigger"
    )
    url = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    token = serializers.CharField(required=False, allow_blank=True, help_text="Token do bot do Telegram")
    chat_id = serializers.CharField(required=False, allow_blank=True, help_text="-1001234567890")
    recipients = serializers.ListField(
        child=serializers.CharField(), required=False, help_text='["dados@empresa.com"]'
    )
    require_signature = serializers.BooleanField(required=False, allow_null=True)
    rotate_secret = serializers.BooleanField(required=False, default=False)
    events = serializers.ListField(child=serializers.CharField(), required=False, help_text='["run.failed"]')
    workflows = serializers.ListField(child=serializers.CharField(), required=False)
    workflow = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    is_active = serializers.BooleanField(required=False, allow_null=True)


class HookSerializer(serializers.Serializer):
    params = serializers.DictField(required=False, default=dict)
    note = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=500)


class GridQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(min_value=1, max_value=60, default=20)


class LogsQuerySerializer(serializers.Serializer):
    after = serializers.IntegerField(min_value=0, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=5000, default=1000)


def valid(serializer_class: type[serializers.Serializer], data) -> dict:
    s = serializer_class(data=data)
    s.is_valid(raise_exception=True)
    return dict(s.validated_data)


class PreferencesSerializer(serializers.Serializer):
    theme = serializers.CharField(required=False)
    palette = serializers.CharField(required=False)
    time_zone = serializers.CharField(required=False)
    density = serializers.CharField(required=False)
    page_size = serializers.IntegerField(required=False)
    notify_failures = serializers.BooleanField(required=False)
    notify_mine = serializers.BooleanField(required=False)
    log_wrap = serializers.BooleanField(required=False)


class BackfillSerializer(serializers.Serializer):
    start = serializers.DateField(help_text="2026-10-01")
    end = serializers.DateField(help_text="2026-10-07")
    params = serializers.DictField(required=False, default=dict)
    note = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=500)
    dry_run = serializers.BooleanField(required=False, default=False)
    skip_existing = serializers.BooleanField(required=False, default=True)


class MarkSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["succeeded", "failed", "skipped"])


class BulkRunsSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["cancel", "retry", "delete"])
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=200)


class BulkWorkflowsSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["pause", "resume"])
    names = serializers.ListField(child=serializers.CharField(), required=False, allow_null=True, max_length=500)


class RunPatchSerializer(serializers.Serializer):
    note = serializers.CharField(allow_null=True, allow_blank=True, max_length=500)


class AuditQuerySerializer(serializers.Serializer):
    action = serializers.CharField(required=False, allow_blank=True, max_length=64)
    user = serializers.IntegerField(required=False, min_value=1)
    q = serializers.CharField(required=False, allow_blank=True, max_length=100)
    since = serializers.IntegerField(required=False, min_value=1, max_value=366)
    page = serializers.IntegerField(min_value=1, default=1)
    size = serializers.IntegerField(min_value=1, max_value=200, default=50)


class DeliveriesQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["pending", "sent", "failed"], required=False)
    limit = serializers.IntegerField(min_value=1, max_value=200, default=50)

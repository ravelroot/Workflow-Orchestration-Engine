"""Validação das entradas da API (corpo e parâmetros de consulta) com serializers do DRF."""

from __future__ import annotations

from rest_framework import serializers

from ..models import RunStatus


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
    page = serializers.IntegerField(min_value=1, default=1)
    size = serializers.IntegerField(min_value=1, max_value=100, default=25)


class GridQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(min_value=1, max_value=60, default=20)


class LogsQuerySerializer(serializers.Serializer):
    after = serializers.IntegerField(min_value=0, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=5000, default=1000)


def valid(serializer_class: type[serializers.Serializer], data) -> dict:
    """Valida e devolve os dados limpos; erro vira 422 no formato padrão."""
    s = serializer_class(data=data)
    s.is_valid(raise_exception=True)
    return dict(s.validated_data)

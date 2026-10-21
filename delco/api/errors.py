"""Formato único de erro da API: ``{"code", "message", "fields"}``."""

from __future__ import annotations

from typing import Any

from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response

from .. import accounts, store


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.fields = status, code, message, fields or {}


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} não encontrado(a).")


def error(status: int, code: str, message: str, fields: dict[str, Any] | None = None) -> Response:
    return Response({"code": code, "message": message, "fields": fields or {}}, status=status)


def _flatten(detail: Any, prefix: str = "") -> dict[str, str]:
    """Transforma os erros aninhados do DRF em ``{"campo": "mensagem"}``."""
    if isinstance(detail, dict):
        out: dict[str, str] = {}
        for key, value in detail.items():
            name = (
                "body"
                if key == "non_field_errors" and not prefix
                else (f"{prefix}.{key}" if prefix else str(key))
            )
            out.update(_flatten(value, name))
        return out
    if isinstance(detail, list):
        if detail and all(not isinstance(x, dict | list) for x in detail):
            return {prefix or "body": str(detail[0])}
        out = {}
        for i, value in enumerate(detail):
            out.update(_flatten(value, f"{prefix}.{i}" if prefix else str(i)))
        return out
    return {prefix or "body": str(detail)}


def exception_handler(exc: Exception, context: dict) -> Response | None:
    response: Response | None = None
    if isinstance(exc, ApiError):
        response = error(exc.status, exc.code, exc.message, exc.fields)
    elif isinstance(exc, accounts.AuthError):
        response = error(exc.status, exc.code, exc.message)
    elif isinstance(exc, accounts.ValidationError):
        response = error(422, "validation_error", "Revise os campos destacados.", exc.fields)
    elif isinstance(exc, store.StoreError):
        response = error(409, "conflict", str(exc))
    elif isinstance(exc, exceptions.ValidationError):
        response = error(422, "validation_error", "Requisição inválida.", _flatten(exc.detail))
    elif isinstance(exc, exceptions.NotAuthenticated):
        response = error(401, "unauthenticated", "Faça login para continuar.")
    elif isinstance(exc, exceptions.AuthenticationFailed):
        codes = exc.get_codes()
        response = error(401, codes if isinstance(codes, str) else "invalid_token", str(exc.detail))
    elif isinstance(exc, exceptions.PermissionDenied):
        response = error(403, "forbidden", "Seu papel não permite esta ação.")
    elif isinstance(exc, Http404 | exceptions.NotFound):
        response = error(404, "not_found", "Recurso não encontrado.")
    elif isinstance(exc, exceptions.APIException):
        response = error(exc.status_code, "http_error", str(exc.detail))
    if response is not None and getattr(exc, "auth_header", None):
        response["WWW-Authenticate"] = exc.auth_header
    return response

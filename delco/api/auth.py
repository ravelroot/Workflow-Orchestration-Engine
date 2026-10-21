"""Autenticação por token (``Authorization: Bearer``) e permissões por papel no Django REST Framework."""

from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.permissions import BasePermission

from .. import accounts


class BearerTokenAuthentication(BaseAuthentication):
    """Aceita o token de sessão do painel ou um token pessoal (``dlc_…``)."""

    keyword = "bearer"

    def authenticate(self, request):
        header = get_authorization_header(request).decode("latin-1").strip()
        if not header:
            return None
        scheme, _, token = header.partition(" ")
        if scheme.lower() != self.keyword:
            return None
        try:
            principal = accounts.authenticate(token.strip())
        except accounts.AuthError as exc:
            raise exceptions.AuthenticationFailed(exc.message, code=exc.code) from None
        return principal, token.strip()

    def authenticate_header(self, request) -> str:
        return 'Bearer realm="delco"'


class _Role(BasePermission):
    role = "viewer"
    message = "Seu papel não permite esta ação."
    code = "forbidden"

    def has_permission(self, request, view) -> bool:
        user = request.user
        if user is None or not getattr(user, "is_authenticated", False):
            raise exceptions.NotAuthenticated("Faça login para continuar.", code="unauthenticated")
        return user.can(self.role)


class IsViewer(_Role):
    role = "viewer"


class IsOperator(_Role):
    role = "operator"


class IsAdmin(_Role):
    role = "admin"


class BearerTokenScheme(OpenApiAuthenticationExtension):
    """Descreve a autenticação por token no OpenAPI (botão Authorize do Swagger)."""

    target_class = "delco.api.auth.BearerTokenAuthentication"
    name = "bearer"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "dlc_…"}

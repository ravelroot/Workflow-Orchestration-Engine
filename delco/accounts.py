"""Usuários, sessões e tokens pessoais (ORM do Django)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import BooleanField, Case, Q, QuerySet, Value, When
from django.db.models.functions import Lower, Now

from .config import Settings
from .models import ApiToken, User
from .schedule import utcnow
from .security import dummy_verify, hash_password, new_token, password_problem, token_hash, verify_password
from .store import audit

ROLES = ("admin", "operator", "viewer")
ROLE_LEVEL = {"viewer": 0, "operator": 1, "admin": 2}
USER_FIELDS = ("id", "email", "name", "role", "is_active", "created_at")


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ValidationError(Exception):
    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("Dados inválidos")
        self.fields = fields


@dataclass
class Principal:
    """Quem fez a requisição (``request.user`` no Django REST Framework)."""

    id: int
    email: str
    name: str
    role: str
    token_id: int
    token_kind: str

    is_authenticated = True
    is_anonymous = False

    def can(self, role: str) -> bool:
        return ROLE_LEVEL[self.role] >= ROLE_LEVEL[role]

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "email": self.email, "name": self.name, "role": self.role}


def _by_email(qs: QuerySet[User], email: str) -> QuerySet[User]:
    """Busca pelo índice único ``lower(email)``."""
    return qs.annotate(email_lower=Lower("email")).filter(email_lower=email.strip().lower())


def _issue(user_id: int, name: str, kind: str, expires_at) -> tuple[str, ApiToken]:
    token = new_token()
    row = ApiToken.objects.create(
        user_id=user_id,
        name=name,
        kind=kind,
        token_hash=token_hash(token),
        prefix=token[:10],
        expires_at=expires_at,
    )
    return token, row


def login(settings: Settings, email: str, password: str) -> dict[str, Any]:
    email = (email or "").strip().lower()
    failure: AuthError | None = None
    with transaction.atomic():
        user = _by_email(User.objects.select_for_update(), email).first()
        if user is None or not user.is_active:
            dummy_verify()
            raise AuthError("invalid_credentials", "E-mail ou senha incorretos.")
        now = utcnow()
        if user.locked_until and user.locked_until > now:
            minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
            raise AuthError(
                "locked", f"Conta bloqueada por tentativas erradas. Tente de novo em {minutes} min.", 423
            )
        if not verify_password(password or "", user.password_hash):
            failures = user.failed_logins + 1
            locked = None
            if failures >= settings.login_max_failures:
                locked, failures = now + timedelta(minutes=settings.login_lock_minutes), 0
            User.objects.filter(pk=user.pk).update(failed_logins=failures, locked_until=locked)
            audit(user.id, "auth.failed", f"user:{user.id}", {"locked": locked is not None})
            # a falha precisa ser gravada (COMMIT) antes de responder; por isso não levanta aqui dentro
            failure = AuthError("invalid_credentials", "E-mail ou senha incorretos.")
    if failure:
        raise failure
    with transaction.atomic():
        User.objects.filter(pk=user.pk).update(failed_logins=0, locked_until=None, last_login_at=Now())
        token, row = _issue(
            user.id, "Sessão no painel", "session", now + timedelta(hours=settings.session_hours)
        )
        audit(user.id, "auth.login", f"user:{user.id}", {})
    return {
        "token": token,
        "expires_at": row.expires_at,
        "user": {"id": user.id, "email": user.email, "name": user.name, "role": user.role},
    }


def authenticate(token: str) -> Principal:
    if not token:
        raise AuthError("unauthenticated", "Faça login para continuar.")
    row = (
        ApiToken.objects.select_related("user")
        .filter(token_hash=token_hash(token), revoked_at__isnull=True, user__is_active=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=Now()))
        .first()
    )
    if row is None:
        raise AuthError("invalid_token", "Sessão expirada ou token inválido. Faça login de novo.")
    if row.last_used_at is None or row.last_used_at < utcnow() - timedelta(minutes=1):
        ApiToken.objects.filter(pk=row.pk).update(last_used_at=Now())
    u = row.user
    return Principal(u.id, u.email, u.name, u.role, row.id, row.kind)


def logout(principal: Principal) -> None:
    ApiToken.objects.filter(pk=principal.token_id, kind="session").update(revoked_at=Now())


def change_password(principal: Principal, current: str, new: str) -> None:
    problem = password_problem(new or "")
    if problem:
        raise ValidationError({"new_password": problem})
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=principal.id)
        if not verify_password(current or "", user.password_hash):
            raise ValidationError({"current_password": "Senha atual incorreta."})
        User.objects.filter(pk=user.pk).update(password_hash=hash_password(new), updated_at=Now())
        # encerra as outras sessões; tokens pessoais continuam valendo
        ApiToken.objects.filter(user_id=principal.id, kind="session", revoked_at__isnull=True).exclude(
            pk=principal.token_id
        ).update(revoked_at=Now())
        audit(principal.id, "auth.password", f"user:{principal.id}", {})


# ------------------------------------------------------------------ tokens
def list_tokens(user_id: int) -> list[dict[str, Any]]:
    return list(
        ApiToken.objects.filter(user_id=user_id, kind="personal", revoked_at__isnull=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=Now()))
        .order_by("-id")
        .values("id", "name", "prefix", "created_at", "last_used_at", "expires_at")
    )


def create_token(principal: Principal, name: str, days: int | None) -> dict[str, Any]:
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise ValidationError({"name": "Dê um nome de até 80 caracteres para o token."})
    if days is not None and not 1 <= days <= 3650:
        raise ValidationError({"expires_in_days": "Use de 1 a 3650 dias, ou deixe sem validade."})
    with transaction.atomic():
        expires = utcnow() + timedelta(days=days) if days else None
        token, row = _issue(principal.id, name, "personal", expires)
        audit(principal.id, "token.create", f"token:{row.id}", {"name": name})
    return {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "prefix": row.prefix,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "token": token,
    }


def revoke_token(principal: Principal, token_id: int) -> bool:
    with transaction.atomic():
        n = ApiToken.objects.filter(
            pk=token_id, user_id=principal.id, kind="personal", revoked_at__isnull=True
        ).update(revoked_at=Now())
        if n:
            audit(principal.id, "token.revoke", f"token:{token_id}", {})
    return bool(n)


# ------------------------------------------------------------------- usuários
def _validate_user(data: dict[str, Any], *, creating: bool) -> dict[str, str]:
    errors: dict[str, str] = {}
    if creating or "email" in data:
        email = (data.get("email") or "").strip()
        if "@" not in email or len(email) > 200 or " " in email:
            errors["email"] = "Informe um e-mail válido."
    if creating or "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            errors["name"] = "Informe o nome."
        elif len(name) > 120:
            errors["name"] = "Use um nome de até 120 caracteres."
    if creating or "role" in data:
        if data.get("role") not in ROLES:
            errors["role"] = "Papel inválido."
    if creating or data.get("password"):
        problem = password_problem(data.get("password") or "")
        if problem:
            errors["password"] = problem
    return errors


def list_users() -> list[dict[str, Any]]:
    return list(
        User.objects.annotate(
            locked=Case(
                When(locked_until__gt=Now(), then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            )
        )
        .order_by("-is_active", "name")
        .values("id", "email", "name", "role", "is_active", "last_login_at", "created_at", "locked")
    )


def _public(user: User) -> dict[str, Any]:
    return {f: getattr(user, f) for f in USER_FIELDS}


def create_user(data: dict[str, Any], actor_id: int | None = None) -> dict[str, Any]:
    errors = _validate_user(data, creating=True)
    if errors:
        raise ValidationError(errors)
    with transaction.atomic():
        if _by_email(User.objects.all(), data["email"]).exists():
            raise ValidationError({"email": "Já existe um usuário com este e-mail."})
        user = User.objects.create(
            email=data["email"].strip().lower(),
            name=data["name"].strip(),
            role=data["role"],
            password_hash=hash_password(data["password"]),
        )
        audit(actor_id, "user.create", f"user:{user.id}", {"role": user.role})
    return _public(user)


def update_user(user_id: int, data: dict[str, Any], actor: Principal) -> dict[str, Any]:
    errors = _validate_user(data, creating=False)
    if user_id == actor.id and (data.get("role") not in (None, actor.role) or data.get("is_active") is False):
        errors["role"] = "Você não pode mudar o próprio papel nem desativar a própria conta."
    if errors:
        raise ValidationError(errors)
    changes: dict[str, Any] = {}
    for field in ("name", "role", "is_active"):
        if field in data and data[field] is not None:
            changes[field] = data[field].strip() if field == "name" else data[field]
    if data.get("password"):
        changes["password_hash"] = hash_password(data["password"])
    if data.get("unlock"):
        changes.update(locked_until=None, failed_logins=0)
    if not changes:
        raise ValidationError({"_": "Nada para alterar."})
    with transaction.atomic():
        if not User.objects.filter(pk=user_id).update(**changes, updated_at=Now()):
            raise LookupError(user_id)
        if data.get("is_active") is False or data.get("password"):
            ApiToken.objects.filter(user_id=user_id, revoked_at__isnull=True).update(revoked_at=Now())
        changed = sorted(k for k in data if k != "password") + (["password"] if data.get("password") else [])
        audit(actor.id, "user.update", f"user:{user_id}", {"fields": changed})
        user = User.objects.get(pk=user_id)
    return _public(user)

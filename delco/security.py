"""Senhas (hasher scrypt do Django) e tokens de acesso (opacos, guardados só como SHA-256)."""

from __future__ import annotations

import hashlib
import secrets
from functools import lru_cache

from django.contrib.auth.hashers import check_password, make_password

TOKEN_PREFIX = "dlc_"


def hash_password(password: str) -> str:
    return make_password(password)


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    try:
        return check_password(password, stored)
    except ValueError:
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 8:
        return "A senha precisa ter pelo menos 8 caracteres."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "A senha precisa ter letras e números."
    return None


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@lru_cache(maxsize=1)
def _dummy() -> str:
    return make_password(secrets.token_hex(8))


def dummy_verify() -> None:
    """Gasta o mesmo tempo de uma verificação real (evita descobrir e-mails pelo tempo de resposta)."""
    verify_password("x", _dummy())

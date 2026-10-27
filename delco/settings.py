"""Configurações do Django.

Tudo vem das variáveis DELCO_* (veja ``delco/config.py`` e o ``.env.example``):
o mesmo pacote roda a API (Daphne/ASGI), o agendador e os workers.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, unquote, urlparse

from django.utils.csp import CSP

from .config import get_settings

DELCO = get_settings()
BASE_DIR = DELCO.web_dir.parent


def _database(url: str) -> dict:
    """Aceita mysql://usuario:senha@servidor:3306/banco (parâmetros extras na query vão para o driver)."""
    u = urlparse(url)
    return {
        "ENGINE": "django.db.backends.mysql",
        "NAME": unquote(u.path.lstrip("/")) or "delco",
        "USER": unquote(u.username or ""),
        "PASSWORD": unquote(u.password or ""),
        "HOST": u.hostname or "localhost",
        "PORT": str(u.port or 3306),
        "OPTIONS": {
            "charset": "utf8mb4",
            # Sessão em UTC: NOW() do banco e as datas gravadas pelo Django falam o mesmo fuso.
            "init_command": "SET time_zone = '+00:00'",
            **dict(parse_qsl(u.query)),  # ex.: ?ssl_mode=REQUIRED
        },
        # Cada processo (API, agendador e cada vaga do worker) abre e devolve a própria conexão.
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": True,
        "TEST": {"NAME": unquote(u.path.lstrip("/")) or "delco_test", "CHARSET": "utf8mb4"},
    }


SECRET_KEY = DELCO.secret_key
DEBUG = DELCO.environment == "development" and os.environ.get("DELCO_DEBUG", "").lower() in {"1", "true"}
ALLOWED_HOSTS = DELCO.allowed_hosts

INSTALLED_APPS = [
    "daphne",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "channels",
    "delco.apps.DelcoConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "delco.api.middleware.ApiCacheMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "delco.urls"
ASGI_APPLICATION = "delco.asgi.application"
WSGI_APPLICATION = "delco.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

DATABASES = {"default": _database(DELCO.database_url)}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Senhas com scrypt (hasher nativo do Django). Não usamos django.contrib.auth:
# o Delco tem papéis fixos e tokens opacos próprios (veja delco/accounts.py).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.ScryptPasswordHasher"]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Painel web servido pelo WhiteNoise na raiz do site; arquivos do Swagger em /static/.
STATIC_URL = "/static/"
STATIC_ROOT = None  # os arquivos do Swagger saem direto dos pacotes (WHITENOISE_USE_FINDERS)
WHITENOISE_ROOT = DELCO.web_dir if DELCO.web_dir.is_dir() else None
WHITENOISE_INDEX_FILE = True
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG
APPEND_SLASH = False

# Cabeçalhos de segurança (CSP nativa do Django 6).
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "connect-src": [CSP.SELF, "ws:", "wss:"],
    "frame-ancestors": [CSP.NONE],
    "base-uri": [CSP.SELF],
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["delco.api.auth.BearerTokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["delco.api.auth.IsViewer"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "delco.api.errors.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": None,
    "UNAUTHENTICATED_TOKEN": None,
    "COERCE_DECIMAL_TO_STRING": False,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Delco",
    "DESCRIPTION": (
        "API do Delco, motor de orquestração de workflows. Autentique com `Authorization: Bearer <token>`: "
        "use o token de sessão devolvido por `POST /api/auth/login` ou um token pessoal criado no painel."
    ),
    "VERSION": "1.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": [],
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "/img/favicon.svg",
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api",
    "TAGS": [
        {"name": "sistema"},
        {"name": "acesso"},
        {"name": "usuários"},
        {"name": "workflows"},
        {"name": "execuções"},
        {"name": "monitoramento"},
    ],
}

# WebSocket: os eventos chegam do Redis (pub/sub) e cada processo da API repassa
# aos navegadores conectados nele pelos grupos do Channels.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "delco": {
            "format": "%(asctime)s %(levelname)-7s %(name)s · %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "delco"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DELCO_LOG_LEVEL", "INFO").upper()},
    "loggers": {
        "django.request": {"level": "ERROR"},
        "daphne": {"level": "WARNING"},
        "django.channels.server": {"level": "WARNING"},
    },
}

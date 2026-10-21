"""API REST do Delco em Django REST Framework.

As views são finas: validam a entrada com serializers, chamam ``store``,
``accounts`` ou ``queries`` e publicam o evento no Redis para o painel ao vivo.
"""

from __future__ import annotations

import logging

from django.http import JsonResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from .. import __version__, accounts, store
from ..bus import get_bus
from ..config import get_settings
from ..db import ping
from . import queries
from .auth import IsAdmin, IsOperator, IsViewer
from .errors import not_found
from .serializers import (
    GridQuerySerializer,
    LoginSerializer,
    LogsQuerySerializer,
    PageSerializer,
    PasswordSerializer,
    RetrySerializer,
    RunsQuerySerializer,
    TokenSerializer,
    TriggerSerializer,
    UserCreateSerializer,
    UserPatchSerializer,
    valid,
)

log = logging.getLogger("delco.api")

OBJ = OpenApiTypes.OBJECT


def _q(name: str, kind=OpenApiTypes.INT, description: str = "") -> OpenApiParameter:
    return OpenApiParameter(name, kind, OpenApiParameter.QUERY, required=False, description=description)


def api_not_found(request, *args, **kwargs) -> JsonResponse:
    return JsonResponse(
        {"code": "not_found", "message": "Rota da API não encontrada.", "fields": {}}, status=404
    )


# ------------------------------------------------------------------- sistema
@extend_schema(tags=["sistema"], responses=OBJ, auth=[], summary="Saúde do banco e do Redis")
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request: Request) -> Response:
    ok_db, ok_redis = ping(), get_bus().ping()
    body = {
        "status": "ok" if ok_db and ok_redis else "degraded",
        "database": ok_db,
        "redis": ok_redis,
        "version": __version__,
    }
    return Response(body, status=200 if ok_db else 503)


# -------------------------------------------------------------------- acesso
@extend_schema(tags=["acesso"], request=LoginSerializer, responses=OBJ, auth=[], summary="Entrar")
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def login(request: Request) -> Response:
    data = valid(LoginSerializer, request.data)
    return Response(accounts.login(get_settings(), data["email"], data["password"]))


@extend_schema(tags=["acesso"], request=None, responses={204: None}, summary="Sair (encerra a sessão)")
@api_view(["POST"])
@permission_classes([IsViewer])
def logout(request: Request) -> Response:
    accounts.logout(request.user)
    return Response(status=204)


@extend_schema(tags=["acesso"], responses=OBJ, summary="Quem sou eu")
@api_view(["GET"])
@permission_classes([IsViewer])
def me(request: Request) -> Response:
    return Response(request.user.public())


@extend_schema(tags=["acesso"], request=PasswordSerializer, responses={204: None}, summary="Trocar a senha")
@api_view(["PUT"])
@permission_classes([IsViewer])
def password(request: Request) -> Response:
    data = valid(PasswordSerializer, request.data)
    accounts.change_password(request.user, data["current_password"], data["new_password"])
    return Response(status=204)


@extend_schema(
    tags=["acesso"],
    methods=["GET"],
    responses=OBJ,
    summary="Listar meus tokens pessoais",
)
@extend_schema(
    tags=["acesso"],
    methods=["POST"],
    request=TokenSerializer,
    responses={201: OBJ},
    summary="Criar token pessoal (o valor aparece uma única vez)",
)
@api_view(["GET", "POST"])
@permission_classes([IsViewer])
def tokens(request: Request) -> Response:
    if request.method == "GET":
        return Response(accounts.list_tokens(request.user.id))
    data = valid(TokenSerializer, request.data)
    return Response(accounts.create_token(request.user, data["name"], data["expires_in_days"]), status=201)


@extend_schema(tags=["acesso"], responses={204: None}, summary="Revogar token pessoal")
@api_view(["DELETE"])
@permission_classes([IsViewer])
def revoke_token(request: Request, token_id: int) -> Response:
    if not accounts.revoke_token(request.user, token_id):
        raise not_found("Token")
    return Response(status=204)


# ------------------------------------------------------------------ usuários
@extend_schema(tags=["usuários"], methods=["GET"], responses=OBJ, summary="Listar usuários")
@extend_schema(
    tags=["usuários"],
    methods=["POST"],
    request=UserCreateSerializer,
    responses={201: OBJ},
    summary="Criar usuário",
)
@api_view(["GET", "POST"])
@permission_classes([IsAdmin])
def users(request: Request) -> Response:
    if request.method == "GET":
        return Response(accounts.list_users())
    data = valid(UserCreateSerializer, request.data)
    return Response(accounts.create_user(data, request.user.id), status=201)


@extend_schema(tags=["usuários"], request=UserPatchSerializer, responses=OBJ, summary="Alterar usuário")
@api_view(["PATCH"])
@permission_classes([IsAdmin])
def update_user(request: Request, user_id: int) -> Response:
    s = UserPatchSerializer(data=request.data, partial=True)
    s.is_valid(raise_exception=True)
    try:
        return Response(accounts.update_user(user_id, dict(s.validated_data), request.user))
    except LookupError:
        raise not_found("Usuário") from None


@extend_schema(tags=["usuários"], parameters=[_q("page"), _q("size")], responses=OBJ, summary="Auditoria")
@api_view(["GET"])
@permission_classes([IsAdmin])
def audit(request: Request) -> Response:
    q = valid(PageSerializer, request.query_params)
    return Response(queries.audit_page(q["page"], q["size"]))


# ----------------------------------------------------------------- workflows
@extend_schema(tags=["workflows"], responses=OBJ, summary="Listar workflows")
@api_view(["GET"])
@permission_classes([IsViewer])
def workflows(request: Request) -> Response:
    return Response(queries.list_workflows())


@extend_schema(tags=["workflows"], responses=OBJ, summary="Detalhe do workflow (grafo, agenda, estatísticas)")
@api_view(["GET"])
@permission_classes([IsViewer])
def workflow(request: Request, name: str) -> Response:
    return Response(queries.get_workflow(name) or _raise(not_found("Workflow")))


@extend_schema(
    tags=["workflows"], parameters=[_q("limit")], responses=OBJ, summary="Grade de execuções × tarefas"
)
@api_view(["GET"])
@permission_classes([IsViewer])
def grid(request: Request, name: str) -> Response:
    q = valid(GridQuerySerializer, request.query_params)
    return Response(queries.workflow_grid(name, q["limit"]) or _raise(not_found("Workflow")))


@extend_schema(
    tags=["workflows"], request=TriggerSerializer, responses={201: OBJ}, summary="Disparar execução"
)
@api_view(["POST"])
@permission_classes([IsOperator])
def trigger(request: Request, name: str) -> Response:
    data = valid(TriggerSerializer, request.data)
    try:
        run_id = store.trigger_run(
            name,
            params=data["params"],
            trigger=data["source"],
            user_id=request.user.id,
            note=data.get("note"),
        )
    except LookupError:
        raise not_found("Workflow") from None
    bus = get_bus()
    bus.publish("run", id=run_id, status="queued", workflow=name)
    bus.wake()
    return Response({"id": run_id, "status": "queued", "workflow": name}, status=201)


def _pause(request: Request, name: str, paused: bool) -> Response:
    try:
        result = store.set_paused(name, paused, request.user.id)
    except LookupError:
        raise not_found("Workflow") from None
    get_bus().publish("workflows", changed=[name])
    return Response(result)


@extend_schema(tags=["workflows"], request=None, responses=OBJ, summary="Pausar agenda")
@api_view(["POST"])
@permission_classes([IsOperator])
def pause(request: Request, name: str) -> Response:
    return _pause(request, name, True)


@extend_schema(tags=["workflows"], request=None, responses=OBJ, summary="Retomar agenda")
@api_view(["POST"])
@permission_classes([IsOperator])
def resume(request: Request, name: str) -> Response:
    return _pause(request, name, False)


@extend_schema(tags=["workflows"], responses=OBJ, summary="Arquivos de workflow com erro")
@api_view(["GET"])
@permission_classes([IsViewer])
def load_errors(request: Request) -> Response:
    return Response(get_bus().load_errors())


# ----------------------------------------------------------------- execuções
@extend_schema(
    tags=["execuções"],
    parameters=[
        _q("workflow", OpenApiTypes.STR),
        _q("status", OpenApiTypes.STR, "queued | running | succeeded | failed | cancelled"),
        _q("page"),
        _q("size"),
    ],
    responses=OBJ,
    summary="Listar execuções",
)
@api_view(["GET"])
@permission_classes([IsViewer])
def runs(request: Request) -> Response:
    q = valid(RunsQuerySerializer, request.query_params)
    return Response(
        queries.list_runs(workflow=q.get("workflow"), status=q.get("status"), page=q["page"], size=q["size"])
    )


@extend_schema(tags=["execuções"], responses=OBJ, summary="Execução com suas tarefas")
@api_view(["GET"])
@permission_classes([IsViewer])
def run(request: Request, run_id: int) -> Response:
    return Response(queries.get_run(run_id) or _raise(not_found("Execução")))


@extend_schema(tags=["execuções"], request=None, responses=OBJ, summary="Cancelar execução")
@api_view(["POST"])
@permission_classes([IsOperator])
def cancel(request: Request, run_id: int) -> Response:
    try:
        result = store.cancel_run(run_id, request.user.id)
    except LookupError:
        raise not_found("Execução") from None
    get_bus().publish("run", id=run_id, status="cancelled")
    return Response(result)


@extend_schema(
    tags=["execuções"], request=RetrySerializer, responses=OBJ, summary="Retomar a partir da falha"
)
@api_view(["POST"])
@permission_classes([IsOperator])
def retry(request: Request, run_id: int) -> Response:
    data = valid(RetrySerializer, request.data)
    try:
        result = store.retry_run(run_id, from_task=data.get("from_task") or None, user_id=request.user.id)
    except LookupError:
        raise not_found("Execução ou tarefa") from None
    bus = get_bus()
    bus.publish("run", id=run_id, status="queued")
    bus.wake()
    return Response(result)


@extend_schema(tags=["execuções"], responses=OBJ, summary="Tarefa com tentativas, saída e erro")
@api_view(["GET"])
@permission_classes([IsViewer])
def task(request: Request, run_id: int, name: str) -> Response:
    return Response(queries.get_task(run_id, name) or _raise(not_found("Tarefa")))


@extend_schema(
    tags=["execuções"], parameters=[_q("after"), _q("limit")], responses=OBJ, summary="Log da tentativa"
)
@api_view(["GET"])
@permission_classes([IsViewer])
def logs(request: Request, attempt_id: int) -> Response:
    q = valid(LogsQuerySerializer, request.query_params)
    return Response(
        queries.attempt_logs(attempt_id, q["after"], q["limit"]) or _raise(not_found("Tentativa"))
    )


# ------------------------------------------------------------- monitoramento
@extend_schema(
    tags=["monitoramento"], responses=OBJ, summary="Indicadores, vazão por hora e execuções recentes"
)
@api_view(["GET"])
@permission_classes([IsViewer])
def overview(request: Request) -> Response:
    return Response(queries.overview())


@extend_schema(tags=["monitoramento"], responses=OBJ, summary="Workers e o que cada um está rodando")
@api_view(["GET"])
@permission_classes([IsViewer])
def workers(request: Request) -> Response:
    return Response(queries.list_workers())


@extend_schema(tags=["monitoramento"], responses=OBJ, summary="Filas: banco e Redis Streams")
@api_view(["GET"])
@permission_classes([IsViewer])
def queues(request: Request) -> Response:
    counts = {q["queue"]: q for q in queries.queue_counts()}
    try:
        streams = {s["queue"]: s for s in get_bus().queue_stats()}
    except Exception:
        streams = {}
    names = sorted(set(counts) | set(streams))
    return Response(
        [
            {
                "queue": n,
                "waiting": counts.get(n, {}).get("waiting", 0),
                "running": counts.get(n, {}).get("running", 0),
                "retry_wait": counts.get(n, {}).get("retry_wait", 0),
                "stream_length": streams.get(n, {}).get("length", 0),
                "stream_pending": streams.get(n, {}).get("pending", 0),
            }
            for n in names
        ]
    )


def _raise(exc: Exception):
    raise exc

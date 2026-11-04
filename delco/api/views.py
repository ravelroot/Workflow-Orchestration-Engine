from __future__ import annotations

import csv
import hmac
import io
import logging

from django.http import HttpResponse, JsonResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from .. import __version__, accounts, integrations, store
from ..bus import get_bus
from ..config import get_settings
from ..db import ping
from . import queries
from .auth import IsAdmin, IsOperator, IsViewer
from .errors import ApiError, not_found
from .serializers import (
    AuditQuerySerializer,
    BackfillSerializer,
    BulkRunsSerializer,
    BulkWorkflowsSerializer,
    DeliveriesQuerySerializer,
    GridQuerySerializer,
    HookSerializer,
    InsightsQuerySerializer,
    IntegrationSerializer,
    LoginSerializer,
    LogsQuerySerializer,
    MarkSerializer,
    PasswordSerializer,
    PreferencesSerializer,
    RetrySerializer,
    RunPatchSerializer,
    RunsQuerySerializer,
    TokenSerializer,
    TriggerSerializer,
    UpcomingQuerySerializer,
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


@extend_schema(tags=["sistema"], responses=OBJ, auth=[], summary="Saúde do banco e do Redis")
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request: Request) -> Response:
    bus = get_bus()
    ok_db, ok_redis = ping(), bus.ping()
    scheduler = bool(bus.schedulers()) if ok_redis else False
    body = {
        "status": "ok" if ok_db and ok_redis and scheduler else "degraded",
        "database": ok_db,
        "redis": ok_redis,
        "scheduler": scheduler,
        "version": __version__,
    }
    return Response(body, status=200 if ok_db else 503)


@extend_schema(tags=["acesso"], request=LoginSerializer, responses=OBJ, auth=[], summary="Entrar")
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def login(request: Request) -> Response:
    settings = get_settings()
    if not get_bus().hit(f"login:{client_ip(request)}", settings.login_rate_limit):
        raise ApiError(429, "rate_limited", "Muitas tentativas de entrada. Espere um minuto e tente de novo.")
    data = valid(LoginSerializer, request.data)
    return Response(accounts.login(settings, data["email"], data["password"]))


def client_ip(request: Request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")) or "?"


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
    return Response({**request.user.public(), "preferences": accounts.get_preferences(request.user.id)})


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
    q = valid(AuditQuerySerializer, request.query_params)
    return Response(
        queries.audit_page(
            q["page"],
            q["size"],
            action=q.get("action") or None,
            user=q.get("user"),
            q=q.get("q") or None,
            since=q.get("since"),
        )
    )


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
    if data.get("dry_run"):
        from ..models import Workflow

        wf = Workflow.objects.filter(name=name).first()
        if wf is None:
            raise not_found("Workflow")
        return Response({"workflow": name, "params": store.validate_params(wf, data["params"])})
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


@extend_schema(
    tags=["execuções"],
    parameters=[
        _q("workflow", OpenApiTypes.STR),
        _q("status", OpenApiTypes.STR, "queued | running | succeeded | failed | cancelled"),
        _q("trigger", OpenApiTypes.STR, "schedule | manual | api | cli | webhook"),
        _q("since", OpenApiTypes.INT, "Só as criadas nos últimos N dias"),
        _q("q", OpenApiTypes.STR, "Número (#118), observação ou parte do nome"),
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
    return Response(queries.list_runs(**_run_filters(q), page=q["page"], size=q["size"]))


def _run_filters(q: dict) -> dict:
    return {k: q.get(k) or None for k in ("workflow", "status", "trigger", "since", "q")}


@extend_schema(
    tags=["execuções"],
    parameters=[
        _q("workflow", OpenApiTypes.STR),
        _q("status", OpenApiTypes.STR),
        _q("trigger", OpenApiTypes.STR),
        _q("since", OpenApiTypes.INT),
        _q("q", OpenApiTypes.STR),
    ],
    responses={(200, "text/csv"): OpenApiTypes.STR},
    summary="Exportar execuções em CSV",
)
@api_view(["GET"])
@permission_classes([IsViewer])
def export_runs(request: Request) -> HttpResponse:
    q = valid(RunsQuerySerializer, request.query_params)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(queries.EXPORT_COLUMNS)
    writer.writerows(queries.export_runs(**_run_filters(q)))
    resp = HttpResponse("\ufeff" + buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="execucoes-delco.csv"'
    return resp


@extend_schema(tags=["execuções"], methods=["GET"], responses=OBJ, summary="Execução com suas tarefas")
@extend_schema(
    tags=["execuções"], methods=["PATCH"], request=RunPatchSerializer, responses=OBJ, summary="Editar a observação"
)
@extend_schema(tags=["execuções"], methods=["DELETE"], responses={204: None}, summary="Excluir execução terminada")
@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsViewer])
def run(request: Request, run_id: int) -> Response:
    if request.method == "PATCH":
        _require(request, "operator")
        data = valid(RunPatchSerializer, request.data)
        if not store.set_note(run_id, data.get("note"), user_id=request.user.id):
            raise not_found("Execução")
    elif request.method == "DELETE":
        _require(request, "admin")
        try:
            store.delete_run(run_id, user_id=request.user.id)
        except LookupError:
            raise not_found("Execução") from None
        get_bus().publish("run", id=run_id, status="deleted")
        return Response(status=204)
    return Response(queries.get_run(run_id) or _raise(not_found("Execução")))


def _require(request: Request, role: str) -> None:
    if not request.user.can(role):
        raise ApiError(403, "forbidden", "Seu papel não permite esta ação.")


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
    rows = queries.list_workers()
    paused = get_bus().paused_workers()
    for w in rows:
        w["pause_requested"] = w["id"] in paused
    return Response(rows)


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


@extend_schema(tags=["monitoramento"], parameters=[_q("days", OpenApiTypes.INT, "7 ou 30")], responses=OBJ,
               summary="Desempenho por workflow e tarefas problemáticas")
@api_view(["GET"])
@permission_classes([IsViewer])
def insights(request: Request) -> Response:
    q = valid(InsightsQuerySerializer, request.query_params)
    return Response(queries.insights(int(q["days"])))


@extend_schema(tags=["monitoramento"], parameters=[_q("hours", OpenApiTypes.INT, "1 a 168")], responses=OBJ,
               summary="Próximas execuções agendadas")
@api_view(["GET"])
@permission_classes([IsViewer])
def upcoming(request: Request) -> Response:
    q = valid(UpcomingQuerySerializer, request.query_params)
    return Response(queries.upcoming(q["hours"]))


def _integration_or_404(integration_id: int):
    from ..models import Integration

    integ = Integration.objects.select_related("workflow", "created_by").filter(id=integration_id).first()
    if integ is None:
        raise not_found("Integração")
    return integ


def _integration_error(exc: integrations.IntegrationError) -> ApiError:
    return ApiError(422, "validation_error", "Confira os campos destacados.", exc.fields)


@extend_schema(tags=["integrações"], methods=["GET"], responses=OBJ, summary="Listar integrações")
@extend_schema(tags=["integrações"], methods=["POST"], request=IntegrationSerializer, responses={201: OBJ},
               summary="Criar integração (Slack, Discord, Teams, Google Chat, webhook ou gatilho por URL)")
@api_view(["GET", "POST"])
@permission_classes([IsOperator])
def integrations_view(request: Request) -> Response:
    if request.method == "GET":
        return Response(integrations.list_all())
    if not IsAdmin().has_permission(request, None):
        raise ApiError(403, "forbidden", "Só administradores criam integrações.")
    data = valid(IntegrationSerializer, request.data)
    try:
        integ, extras = integrations.create(data, user_id=request.user.id)
    except integrations.IntegrationError as exc:
        raise _integration_error(exc) from None
    body = integrations.as_dict(_integration_or_404(integ.id))
    body.update(extras)
    if extras.get("token"):
        body["hook_url"] = f"{get_settings().public_url.rstrip('/')}/api/hooks/{extras['token']}"
    return Response(body, status=201)


@extend_schema(tags=["integrações"], methods=["GET"], responses=OBJ, summary="Detalhe da integração")
@extend_schema(tags=["integrações"], methods=["PATCH"], request=IntegrationSerializer, responses=OBJ,
               summary="Alterar integração")
@extend_schema(tags=["integrações"], methods=["DELETE"], responses={204: None}, summary="Excluir integração")
@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsOperator])
def integration(request: Request, integration_id: int) -> Response:
    integ = _integration_or_404(integration_id)
    if request.method == "GET":
        return Response(integrations.as_dict(integ))
    if not IsAdmin().has_permission(request, None):
        raise ApiError(403, "forbidden", "Só administradores alteram integrações.")
    if request.method == "DELETE":
        integrations.delete(integ, user_id=request.user.id)
        return Response(status=204)
    data = valid(IntegrationSerializer, request.data)
    try:
        _, extras = integrations.update(
            integ, {k: v for k, v in data.items() if k in request.data}, user_id=request.user.id
        )
    except integrations.IntegrationError as exc:
        raise _integration_error(exc) from None
    return Response({**integrations.as_dict(_integration_or_404(integration_id)), **extras})


@extend_schema(tags=["integrações"], request=None, responses=OBJ, summary="Enviar mensagem de teste")
@api_view(["POST"])
@permission_classes([IsAdmin])
def integration_test(request: Request, integration_id: int) -> Response:
    integ = _integration_or_404(integration_id)
    try:
        d = integrations.send_test(integ, user_id=request.user.id)
    except integrations.IntegrationError as exc:
        raise _integration_error(exc) from None
    return Response({"id": d.id, "status": d.status, "response_code": d.response_code, "error": d.error})


@extend_schema(tags=["integrações"], responses=OBJ, summary="Histórico de entregas")
@api_view(["GET"])
@permission_classes([IsOperator])
def integration_deliveries(request: Request, integration_id: int) -> Response:
    _integration_or_404(integration_id)
    q = valid(DeliveriesQuerySerializer, request.query_params)
    return Response(integrations.deliveries(integration_id, q["limit"], q.get("status")))


@extend_schema(tags=["integrações"], request=HookSerializer, responses={201: OBJ}, auth=[],
               summary="Gatilho por URL: dispara o workflow ligado ao token")
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def hook(request: Request, token: str) -> Response:
    raw = request.body
    settings = get_settings()
    if not get_bus().hit(f"hook:{token[:16]}", settings.hook_rate_limit):
        raise ApiError(429, "rate_limited", "Limite de chamadas por minuto atingido para este gatilho.")
    integ = integrations.resolve_token(token)
    if integ is None or integ.workflow is None:
        raise ApiError(404, "not_found", "Gatilho não encontrado ou desativado.")
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not integrations.signature_ok(integ, raw, headers):
        raise ApiError(401, "invalid_signature", "Assinatura ausente ou inválida (X-Delco-Signature).")
    data = valid(HookSerializer, request.data if isinstance(request.data, dict) else {})
    try:
        run_id = store.trigger_run(
            integ.workflow.name, params=data["params"], trigger="webhook", note=data.get("note") or f"Gatilho: {integ.name}"
        )
    except LookupError:
        raise not_found("Workflow") from None
    from ..models import Integration
    from ..schedule import utcnow

    Integration.objects.filter(id=integ.id).update(last_delivery_at=utcnow(), last_status="sent")
    bus = get_bus()
    bus.publish("run", id=run_id, status="queued", workflow=integ.workflow.name)
    bus.wake()
    return Response({"id": run_id, "status": "queued", "workflow": integ.workflow.name}, status=201)


@extend_schema(tags=["acesso"], methods=["GET"], responses=OBJ, summary="Minhas preferências")
@extend_schema(
    tags=["acesso"], methods=["PATCH"], request=PreferencesSerializer, responses=OBJ, summary="Alterar preferências"
)
@api_view(["GET", "PATCH"])
@permission_classes([IsViewer])
def preferences(request: Request) -> Response:
    if request.method == "GET":
        return Response(accounts.get_preferences(request.user.id))
    if not isinstance(request.data, dict):
        raise ApiError(422, "validation_error", "Envie um objeto JSON.")
    return Response(accounts.update_preferences(request.user, dict(request.data)))


@extend_schema(tags=["workflows"], responses=OBJ, summary="Histórico de versões do grafo, com o que mudou")
@api_view(["GET"])
@permission_classes([IsViewer])
def versions(request: Request, name: str) -> Response:
    rows = store.workflow_versions(name)
    if rows is None:
        raise not_found("Workflow")
    return Response(rows)


@extend_schema(tags=["workflows"], responses=OBJ, summary="Código-fonte atual do workflow")
@api_view(["GET"])
@permission_classes([IsViewer])
def source(request: Request, name: str) -> Response:
    return Response(queries.workflow_source(name) or _raise(not_found("Workflow")))


@extend_schema(
    tags=["workflows"],
    request=BackfillSerializer,
    responses={201: OBJ, 200: OBJ},
    summary="Reprocessar um período (uma execução por horário da agenda)",
)
@api_view(["POST"])
@permission_classes([IsOperator])
def backfill(request: Request, name: str) -> Response:
    data = valid(BackfillSerializer, request.data)
    try:
        result = store.backfill(
            name,
            start=data["start"],
            end=data["end"],
            params=data["params"],
            note=data.get("note") or None,
            user_id=request.user.id,
            dry_run=data["dry_run"],
            skip_existing=data["skip_existing"],
            limit=get_settings().max_backfill_runs,
        )
    except LookupError:
        raise not_found("Workflow") from None
    if result["created"]:
        bus = get_bus()
        for run_id in result["created"]:
            bus.publish("run", id=run_id, status="queued", workflow=name)
        bus.wake()
    return Response(result, status=201 if result["created"] else 200)


@extend_schema(
    tags=["workflows"], request=BulkWorkflowsSerializer, responses=OBJ, summary="Pausar ou retomar várias agendas"
)
@api_view(["POST"])
@permission_classes([IsOperator])
def bulk_workflows(request: Request) -> Response:
    data = valid(BulkWorkflowsSerializer, request.data)
    result = store.bulk_workflows(data["action"], data.get("names"), user_id=request.user.id)
    if result["changed"]:
        get_bus().publish("workflows", changed=result["changed"])
    return Response(result)


@extend_schema(
    tags=["execuções"], request=BulkRunsSerializer, responses=OBJ, summary="Cancelar, retomar ou excluir em lote"
)
@api_view(["POST"])
@permission_classes([IsOperator])
def bulk_runs(request: Request) -> Response:
    data = valid(BulkRunsSerializer, request.data)
    if data["action"] == "delete":
        _require(request, "admin")
    result = store.bulk_runs(data["action"], data["ids"], user_id=request.user.id)
    bus = get_bus()
    status = {"cancel": "cancelled", "retry": "queued", "delete": "deleted"}[data["action"]]
    for run_id in result["done"]:
        bus.publish("run", id=run_id, status=status)
    if result["done"]:
        bus.wake()
    return Response(result)


@extend_schema(
    tags=["execuções"],
    request=MarkSerializer,
    responses=OBJ,
    summary="Marcar tarefa como sucesso, falha ou pulada (e seguir a execução)",
)
@api_view(["POST"])
@permission_classes([IsOperator])
def mark(request: Request, run_id: int, name: str) -> Response:
    data = valid(MarkSerializer, request.data)
    try:
        result = store.mark_task(run_id, name, data["status"], user_id=request.user.id)
    except LookupError:
        raise not_found("Execução ou tarefa") from None
    bus = get_bus()
    bus.publish("run", id=run_id, status=result["run_status"])
    bus.wake()
    return Response(result)


def _worker_action(request: Request, worker_id: str, paused: bool) -> Response:
    from ..models import Worker

    if not Worker.objects.filter(id=worker_id).exclude(status="offline").exists():
        raise not_found("Worker")
    bus = get_bus()
    if paused:
        bus.pause_worker(worker_id)
    else:
        bus.resume_worker(worker_id)
    store.audit(request.user.id, "worker.pause" if paused else "worker.resume", f"worker:{worker_id}", {})
    bus.publish("worker", id=worker_id, status="pausing" if paused else "resuming")
    return Response({"id": worker_id, "pause_requested": paused})


@extend_schema(tags=["monitoramento"], request=None, responses=OBJ, summary="Pausar worker (não pega tarefas novas)")
@api_view(["POST"])
@permission_classes([IsOperator])
def pause_worker(request: Request, worker_id: str) -> Response:
    return _worker_action(request, worker_id, True)


@extend_schema(tags=["monitoramento"], request=None, responses=OBJ, summary="Retomar worker pausado")
@api_view(["POST"])
@permission_classes([IsOperator])
def resume_worker(request: Request, worker_id: str) -> Response:
    return _worker_action(request, worker_id, False)


@extend_schema(
    tags=["monitoramento"],
    responses={(200, "text/plain"): OpenApiTypes.STR},
    summary="Métricas no formato do Prometheus",
    auth=[],
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def metrics(request: Request) -> HttpResponse:
    from .metrics import render

    settings = get_settings()
    header = request.headers.get("Authorization", "")
    token = header.partition(" ")[2].strip() if header.lower().startswith("bearer ") else ""
    allowed = bool(settings.metrics_token) and hmac.compare_digest(token, settings.metrics_token)
    if not allowed and token:
        try:
            accounts.authenticate(token)
            allowed = True
        except accounts.AuthError:
            allowed = False
    if not allowed:
        raise ApiError(401, "unauthenticated", "Envie Authorization: Bearer com DELCO_METRICS_TOKEN ou um token pessoal.")
    return HttpResponse(render(get_bus()), content_type="text/plain; version=0.0.4; charset=utf-8")


@extend_schema(tags=["sistema e manutenção"], responses=OBJ, summary="Componentes, configuração e verificações")
@api_view(["GET"])
@permission_classes([IsAdmin])
def system(request: Request) -> Response:
    from .. import doctor

    settings = get_settings()
    bus = get_bus()
    checks = doctor.run_checks(settings, bus)
    return Response(
        {
            "versions": doctor.versions(),
            "status": doctor.summary(checks),
            "checks": [c.as_dict() for c in checks],
            "schedulers": bus.schedulers(),
            "redis": bus.server_info(),
            "config": settings.public(),
            "load_errors": bus.load_errors(),
        }
    )


@extend_schema(
    tags=["sistema e manutenção"], request=None, responses=OBJ, summary="Rodar a limpeza do histórico agora"
)
@api_view(["POST"])
@permission_classes([IsAdmin])
def housekeeping_now(request: Request) -> Response:
    settings = get_settings()
    removed = store.housekeeping(settings.retention_days)
    store.audit(request.user.id, "system.housekeeping", None, removed)
    return Response({"removed": removed, "retention_days": settings.retention_days})


@extend_schema(
    tags=["usuários"],
    parameters=[_q("action", OpenApiTypes.STR), _q("user"), _q("q", OpenApiTypes.STR), _q("since")],
    responses={(200, "text/csv"): OpenApiTypes.STR},
    summary="Exportar auditoria em CSV",
)
@api_view(["GET"])
@permission_classes([IsAdmin])
def export_audit(request: Request) -> HttpResponse:
    q = valid(AuditQuerySerializer, request.query_params)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(queries.AUDIT_COLUMNS)
    writer.writerows(
        queries.export_audit(
            action=q.get("action") or None, user=q.get("user"), q=q.get("q") or None, since=q.get("since")
        )
    )
    resp = HttpResponse("\ufeff" + buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="auditoria-delco.csv"'
    return resp


@extend_schema(tags=["integrações"], request=None, responses=OBJ, summary="Reenviar uma entrega")
@api_view(["POST"])
@permission_classes([IsAdmin])
def redeliver(request: Request, integration_id: int, delivery_id: int) -> Response:
    _integration_or_404(integration_id)
    from ..models import IntegrationDelivery

    if not IntegrationDelivery.objects.filter(id=delivery_id, integration_id=integration_id).exists():
        raise not_found("Entrega")
    try:
        d = integrations.redeliver(delivery_id, user_id=request.user.id)
    except integrations.IntegrationError as exc:
        raise _integration_error(exc) from None
    get_bus().wake()
    return Response({"id": d.id, "status": d.status})

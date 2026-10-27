"""Rotas da API REST (montadas em /api/)."""

from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health),
    path("auth/login", views.login),
    path("auth/logout", views.logout),
    path("auth/me", views.me),
    path("auth/me/password", views.password),
    path("tokens", views.tokens),
    path("tokens/<int:token_id>", views.revoke_token),
    path("users", views.users),
    path("users/<int:user_id>", views.update_user),
    path("audit", views.audit),
    path("workflows", views.workflows),
    path("workflows/<str:name>", views.workflow),
    path("workflows/<str:name>/grid", views.grid),
    path("workflows/<str:name>/runs", views.trigger),
    path("workflows/<str:name>/pause", views.pause),
    path("workflows/<str:name>/resume", views.resume),
    path("load-errors", views.load_errors),
    path("runs", views.runs),
    path("runs/export", views.export_runs),
    path("runs/<int:run_id>", views.run),
    path("runs/<int:run_id>/cancel", views.cancel),
    path("runs/<int:run_id>/retry", views.retry),
    path("runs/<int:run_id>/tasks/<str:name>", views.task),
    path("attempts/<int:attempt_id>/logs", views.logs),
    path("overview", views.overview),
    path("workers", views.workers),
    path("queues", views.queues),
    path("insights", views.insights),
    path("upcoming", views.upcoming),
    path("integrations", views.integrations_view),
    path("integrations/<int:integration_id>", views.integration),
    path("integrations/<int:integration_id>/test", views.integration_test),
    path("integrations/<int:integration_id>/deliveries", views.integration_deliveries),
    path("hooks/<str:token>", views.hook),
]

"""Rotas: API REST em /api/, documentação em /api/docs e o painel (WhiteNoise) na raiz."""

from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularJSONAPIView, SpectacularSwaggerSplitView

from .api.views import api_not_found

urlpatterns = [
    path("api/openapi.json", SpectacularJSONAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerSplitView.as_view(url_name="schema"), name="docs"),
    path("api/", include("delco.api.urls")),
    re_path(r"^api(?:/.*)?$", api_not_found),
]

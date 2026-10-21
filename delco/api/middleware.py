"""Respostas da API nunca vão para cache (dados mudam o tempo todo e podem ser sensíveis)."""


class ApiCacheMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/") and not request.path.startswith("/api/docs"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

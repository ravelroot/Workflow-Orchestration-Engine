# Imagem única do Delco (Django): o mesmo contêiner vira API, agendador ou worker conforme o comando.
# O mysqlclient (driver do MySQL recomendado pelo Django) é compilado num estágio à parte.
FROM python:3.13-slim AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential pkg-config default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DELCO_WEB_DIR=/app/web \
    DELCO_WORKFLOWS_DIR=/app/workflows \
    DJANGO_SETTINGS_MODULE=delco.settings

RUN apt-get update \
    && apt-get install -y --no-install-recommends libmariadb3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN groupadd --system delco && useradd --system --gid delco --home-dir /app delco

COPY --from=build /wheels /wheels
RUN pip install --no-index /wheels/*.whl && rm -rf /wheels

COPY pyproject.toml README.md manage.py ./
COPY delco ./delco
RUN pip install --no-deps .

COPY web ./web
COPY workflows ./workflows

USER delco
EXPOSE 8000
CMD ["delco", "api", "--port", "8000"]

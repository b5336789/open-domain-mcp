FROM node:20-bookworm-slim AS web-builder

WORKDIR /app

COPY web/package*.json web/
RUN cd web && npm ci

COPY web web
COPY src src
RUN cd web && npm run build

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ODM_WEB_HOST=0.0.0.0 \
    ODM_WEB_PORT=8000 \
    ODM_DATA_DIR=/data/opendomain

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src src
COPY --from=web-builder /app/src/opendomainmcp/api/static src/opendomainmcp/api/static

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && pip install --no-cache-dir . \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/opendomain \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8000

CMD ["opendomainmcp-web"]

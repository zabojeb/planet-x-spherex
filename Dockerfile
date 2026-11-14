# syntax=docker/dockerfile:1.7
# One image, two roles: SPHEREX_ROLE=api (default) serves the SPA + API, SPHEREX_ROLE=worker builds sequences.

FROM node:22-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    SPHEREX_CACHE_DIR=/cache SPHEREX_WEB_DIST=/app/web/dist SPHEREX_LOG_JSON=1 PORT=8080
RUN pip install --no-cache-dir uv==0.5.14 && useradd -m -u 1000 app && mkdir -p /cache && chown app /cache
WORKDIR /app
COPY backend/pyproject.toml backend/
COPY backend/spherex_core backend/spherex_core
COPY backend/spherex_api backend/spherex_api
RUN uv pip install --system --no-cache "./backend"
COPY --from=web /src/web/dist /app/web/dist
COPY deploy/start.sh /app/deploy/start.sh
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/api/health',timeout=4)" || exit 1
CMD ["sh", "/app/deploy/start.sh"]

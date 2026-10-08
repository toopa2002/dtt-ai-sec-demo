# Onboarding app image: the session API also serves the web app's build (one container). Node is only used to build
# the web app. Build context: onboarding/ (so the catalog and web sources are included).
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
RUN npx ng build --configuration production

FROM python:3.12-alpine AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-install-project
COPY api/src ./src
RUN uv sync --frozen

FROM python:3.12-alpine
RUN adduser -D -u 10001 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
COPY --from=web /web/dist/onboarding-web/browser /app/web
COPY catalog /app/catalog
ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app/src PYTHONUNBUFFERED=1 CATALOG_DIR=/app/catalog WEB_DIR=/app/web
USER 10001
EXPOSE 8080
CMD ["uvicorn", "onboarding_api.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--no-access-log"]

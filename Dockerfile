FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY yourfeed ./yourfeed
COPY --from=web /web/dist ./web/dist
RUN useradd --system --uid 10001 --home-dir /app yourfeed
USER yourfeed
EXPOSE 8000
CMD ["uvicorn", "yourfeed.main:app", "--host", "0.0.0.0", "--port", "8000"]

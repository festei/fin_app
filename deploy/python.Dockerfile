# syntax=docker/dockerfile:1
# One Dockerfile for the Python runtimes. Build with:
#   docker build -f deploy/python.Dockerfile --build-arg PACKAGE=fin-api -t fin-api .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
ARG PACKAGE
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY flows ./flows
# --no-editable: install workspace packages into the venv. The default editable install only
# leaves .pth links to /app/packages, which the final stage does not copy (ImportError at start).
RUN uv sync --frozen --no-dev --no-editable --package ${PACKAGE}

FROM python:3.12-slim
RUN useradd --system --uid 10001 --home /app app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY config ./config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app

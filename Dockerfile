# syntax=docker/dockerfile:1

# ---- Build stage: install locked dependencies into a virtualenv ----
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Copy only what the build needs (see .dockerignore); never the whole checkout
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# ---- Runtime stage: no build tools, no source tree, non-root ----
FROM python:3.12-slim-bookworm

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /app/.venv /app/.venv

# Native, authenticated HTTP transport. GAM_MCP_AUTH_TOKEN must be provided at
# runtime or the server refuses to start. Override GAM_MCP_TRANSPORT=stdio for
# `docker run -i` use from a local MCP client.
# HOME is the writable /tmp: zeep caches WSDLs in ~/.cache, and the app user
# has no home directory (and the root filesystem may be --read-only).
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    GAM_MCP_TRANSPORT=http \
    GAM_MCP_HOST=0.0.0.0 \
    GAM_MCP_PORT=8000 \
    GAM_CREDENTIALS_PATH=/run/secrets/gam-credentials.json \
    GAM_UPLOAD_DIR=/uploads

USER app
WORKDIR /tmp

EXPOSE 8000

CMD ["gam-mcp"]

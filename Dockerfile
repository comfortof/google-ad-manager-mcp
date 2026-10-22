# syntax=docker/dockerfile:1

# ---- Build stage: install locked dependencies into a virtualenv ----
FROM python:3.12-slim-bookworm AS builder

# 1 adds Playwright and Chromium for capture_campaign_screenshots (about +1 GB)
ARG WITH_SCREENSHOTS=0

COPY --from=ghcr.io/astral-sh/uv:0.5.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Copy only what the build needs (see .dockerignore); never the whole checkout
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project \
    $([ "$WITH_SCREENSHOTS" = "1" ] && echo "--extra screenshots")

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    $([ "$WITH_SCREENSHOTS" = "1" ] && echo "--extra screenshots")

# ---- Runtime stage: no build tools, no source tree, non-root ----
FROM python:3.12-slim-bookworm

ARG WITH_SCREENSHOTS=0

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /app/.venv /app/.venv

# Native, authenticated HTTP transport. GAM_MCP_AUTH_TOKEN must be provided at
# runtime or the server refuses to start. Override GAM_MCP_TRANSPORT=stdio for
# `docker run -i` use from a local MCP client.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    GAM_MCP_TRANSPORT=http \
    GAM_MCP_HOST=0.0.0.0 \
    GAM_MCP_PORT=8000 \
    GAM_CREDENTIALS_PATH=/run/secrets/gam-credentials.json \
    GAM_UPLOAD_DIR=/uploads \
    GAM_EVIDENCE_DIR=/evidence \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Chromium and its system libraries, outside any home directory so the
# unprivileged user can run it. Installed as root, before USER app.
RUN if [ "$WITH_SCREENSHOTS" = "1" ]; then \
        playwright install --with-deps --only-shell chromium \
        && rm -rf /var/lib/apt/lists/* \
        && chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH"; \
    fi \
    && mkdir -p /evidence && chown app:app /evidence

USER app
WORKDIR /tmp

EXPOSE 8000

CMD ["gam-mcp"]

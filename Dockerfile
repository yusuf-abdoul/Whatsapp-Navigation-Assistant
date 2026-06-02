FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

COPY app ./app
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app
# Alembic config + migrations are needed at runtime for the Render
# `preDeployCommand: alembic upgrade head` hook.
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

# $PORT is set by Render automatically; defaults to 8000 locally. The
# proxy-headers / forwarded-allow-ips flags let signature verification see
# the real public URL behind Render's load balancer.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=*"]

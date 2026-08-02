# Multi-stage build: dependencies are resolved once against the committed uv.lock, so
# every image build is byte-for-byte reproducible, and the runtime stage carries no
# build toolchain.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first: this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY sample_documents ./sample_documents
RUN uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

RUN groupadd --system app && useradd --system --gid app --create-home app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STORAGE_DIR=/data/uploads

WORKDIR /app

COPY --from=builder --chown=app:app /app /app

# Uploaded originals live on a volume so re-processing survives container replacement.
RUN mkdir -p /data/uploads && chown -R app:app /data

USER app

EXPOSE 8000 8080

# Overridden by docker-compose to run either the REST API or the MCP server.
CMD ["uvicorn", "kb.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

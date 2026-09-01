# Cloud Run Job: one ingestion run, then exit.
#
# A job, not a service. It has no port and no request loop: Cloud Run starts it, it
# downloads the feed, transforms in DuckDB, loads Parquet into BigQuery and exits. A
# container that never exits is a job that never finishes and a bill that never stops.

# uv is copied in from its own pinned image rather than installed with a script, so the
# build depends on an exact uv version and an exact Python version independently.
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim-bookworm AS runtime

RUN useradd --create-home --uid 1001 pulso

WORKDIR /app
COPY --from=builder --chown=pulso:pulso /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER pulso

# A run downloads a 16.5 MB ZIP, extracts it, and writes Parquet — all under /tmp, which
# on Cloud Run is a tmpfs backed by the instance's memory. Size the job's memory for the
# working set, not just for Python.
ENTRYPOINT ["pulso-gtfs"]

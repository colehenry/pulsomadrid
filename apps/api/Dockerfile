# Cloud Run service: the read API behind the map.
#
# Two stages so the runtime image carries the virtualenv and the source, but none of
# uv, the lockfile resolution, or a build toolchain.

# uv is copied in from its own pinned image rather than installed with a script, so the
# build depends on an exact uv version and an exact Python version independently.
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, in their own layer. They change far less often than our code, so
# an edit to app/*.py reuses this layer instead of re-resolving every package.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
# --no-editable so the package is copied into the venv rather than linked back at /app/src.
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim-bookworm AS runtime

# Non-root. Cloud Run does not require it, but a container that never needs to write
# anywhere has no reason to run as root.
RUN useradd --create-home --uid 1001 pulso

WORKDIR /app
COPY --from=builder --chown=pulso:pulso /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER pulso
EXPOSE 8080

# One uvicorn process on purpose, not a worker pool. The endpoints are async and
# IO-bound, so a single event loop is the right shape — and the vehicle feed's cache and
# its single-flight lock live in process memory, so N workers would mean N independent
# caches and N times the upstream requests. Cloud Run scales by adding instances.
#
# `exec` so uvicorn becomes PID 1 and receives Cloud Run's SIGTERM directly; without it
# the shell holds PID 1 and shutdown waits for the grace period to expire.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

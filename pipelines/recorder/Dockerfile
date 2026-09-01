# Railway service: a long-lived poller.
#
# Not a job and not a cron entry. Railway cron has a five-minute minimum interval and a
# train stands in a station for 30-60 seconds, so a cron recorder would miss most
# STOPPED_AT events -- the arrival evidence every reliability metric rests on. This
# container is meant to run forever; if it exits, something is wrong.
#
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

# Memory is small and flat: the buffer holds about two minutes of observations, roughly
# a thousand rows. What it must not do is restart quietly -- every restart loses the
# unflushed buffer, and SIGTERM is handled so a redeploy flushes instead of dropping it.
ENTRYPOINT ["pulso-recorder"]

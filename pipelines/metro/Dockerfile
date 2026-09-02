# Railway service: a long-lived poller, separate from the Cercanias recorder on purpose.
#
# CRTM's endpoint is undocumented and can hang, throttle or start erroring without
# notice. The Cercanias recorder captures data nobody archives and that cannot be
# re-collected, so it must not share a process with this one.
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

# Memory is flat: the buffer holds ~2 minutes of observations. SIGTERM is handled so a
# redeploy flushes rather than dropping what it has.
ENTRYPOINT ["pulso-metro"]

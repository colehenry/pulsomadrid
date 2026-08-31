#!/usr/bin/env bash
# Lint, typecheck, test — both stacks. Same commands CI runs.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$REPO_ROOT"

fail=0
step() { local label="$1"; shift; section "$label"; if "$@"; then ok "$label"; else bad "$label"; fail=1; fi; }

if [ -f apps/api/pyproject.toml ]; then
  step "ruff"   bash -c 'cd apps/api && uv run ruff check .'
  step "mypy"   bash -c 'cd apps/api && uv run mypy .'
  step "pytest" bash -c 'cd apps/api && uv run pytest -q'
else warn "apps/api not scaffolded yet"; fi

if [ -f pipelines/gtfs/pyproject.toml ]; then
  step "gtfs ruff"   bash -c 'cd pipelines/gtfs && uv run ruff check src tests'
  step "gtfs pytest" bash -c 'cd pipelines/gtfs && uv run pytest -q'
else warn "pipelines/gtfs not scaffolded yet"; fi

if [ -f apps/web/package.json ]; then
  step "eslint" bash -c 'cd apps/web && npm run lint'
  step "tsc"    bash -c 'cd apps/web && npx tsc --noEmit'
  step "vitest" bash -c 'cd apps/web && npm test --if-present'
else warn "apps/web not scaffolded yet"; fi

if [ "$fail" -eq 0 ]; then printf '\n%sall checks passed%s\n' "$G" "$X"
else printf '\n%schecks failed%s\n' "$R" "$X"; exit 1; fi

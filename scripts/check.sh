#!/usr/bin/env bash
# Lint + typecheck + test. Same commands CI runs.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; shift; "$@" || fail=1; }

if [ -f apps/api/pyproject.toml ]; then
  step "ruff"   bash -c 'cd apps/api && uv run ruff check .'
  step "mypy"   bash -c 'cd apps/api && uv run mypy .'
  step "pytest" bash -c 'cd apps/api && uv run pytest -q'
fi
if [ -f apps/web/package.json ]; then
  step "eslint"    bash -c 'cd apps/web && npm run lint'
  step "tsc"       bash -c 'cd apps/web && npx tsc --noEmit'
  step "vitest"    bash -c 'cd apps/web && npm test --if-present'
fi
[ "$fail" -eq 0 ] && echo -e "\n\033[32mall checks passed\033[0m" || { echo -e "\n\033[31mchecks failed\033[0m"; exit 1; }

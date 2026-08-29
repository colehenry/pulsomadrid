#!/usr/bin/env bash
# Run the stack locally.  scripts/dev.sh [web|api|all]
set -euo pipefail
cd "$(dirname "$0")/.."
target="${1:-all}"

run_api() { (cd apps/api && uv run uvicorn app.main:app --reload --port 8000); }
run_web() { (cd apps/web && npm run dev); }

case "$target" in
  api) run_api ;;
  web) run_web ;;
  all)
    trap 'kill 0' EXIT INT TERM
    run_api & run_web &
    echo "web → http://localhost:3000    api → http://localhost:8000/docs"
    wait
    ;;
  *) echo "usage: scripts/dev.sh [web|api|all]"; exit 1 ;;
esac

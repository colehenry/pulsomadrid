#!/usr/bin/env bash
# Run the stack locally.
#
#   scripts/dev.sh          web + api together
#   scripts/dev.sh web      Next.js only     → http://localhost:3000
#   scripts/dev.sh api      FastAPI only     → http://localhost:8000/docs
#   scripts/dev.sh --kill   free ports 3000 and 8000
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$REPO_ROOT"

port_busy() { lsof -ti:"$1" >/dev/null 2>&1; }
free_port() {
  if port_busy "$1"; then
    warn "port $1 in use — killing $(lsof -ti:"$1" | tr '\n' ' ')"
    lsof -ti:"$1" | xargs kill 2>/dev/null
    sleep 1
  fi
}

if [ "${1:-all}" = "--kill" ]; then free_port 3000; free_port 8000; ok "ports free"; exit 0; fi

run_api() {
  [ -f apps/api/pyproject.toml ] || { bad "apps/api not scaffolded yet"; return 1; }
  free_port 8000
  (cd apps/api && uv run uvicorn app.main:app --reload --port 8000)
}
run_web() {
  [ -f apps/web/package.json ] || { bad "apps/web not scaffolded yet"; return 1; }
  free_port 3000
  (cd apps/web && npm run dev)
}

case "${1:-all}" in
  api) run_api ;;
  web) run_web ;;
  all)
    trap 'kill 0' EXIT INT TERM
    run_api & run_web &
    printf '\n  web → http://localhost:3000\n  api → http://localhost:8000/docs\n\n  ctrl-c stops both\n\n'
    wait
    ;;
  *) echo "usage: scripts/dev.sh [web|api|all|--kill]"; exit 1 ;;
esac

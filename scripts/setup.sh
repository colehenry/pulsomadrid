#!/usr/bin/env bash
# One-time local setup. Idempotent — safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

bold "Toolchain"
missing=0
check() { # name  command  install-hint
  if command -v "$2" >/dev/null 2>&1; then ok "$1 — $($2 --version 2>&1 | head -1)"
  else bad "$1 missing — $3"; missing=1; fi
}
check "node"      node      "brew install node  (or nvm use 20)"
check "uv"        uv        "brew install uv"
check "gcloud"    gcloud    "brew install --cask google-cloud-sdk"
check "terraform" terraform "brew install terraform"
check "docker"    docker    "https://docker.com"
check "gh"        gh        "brew install gh"

bold "Auth"
if gcloud auth application-default print-access-token >/dev/null 2>&1; then
  ok "GCP ADC present"
else
  bad "no ADC — run: gcloud auth application-default login"
fi

if [ "$missing" -ne 0 ]; then
  echo; echo "Install the missing tools above, then re-run. See docs/setup/local-environment.md"
fi

bold "Dependencies"
if [ -f apps/api/pyproject.toml ]; then (cd apps/api && uv sync) && ok "api deps"; else echo "  – apps/api not scaffolded yet"; fi
if [ -f apps/web/package.json ];  then (cd apps/web && npm install) && ok "web deps"; else echo "  – apps/web not scaffolded yet"; fi

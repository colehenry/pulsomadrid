#!/usr/bin/env bash
# Shared helpers. Sourced by the other scripts, not run directly.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load .env if present, so every script reads config from one place.
if [ -f "$REPO_ROOT/.env" ]; then
  set -a; . "$REPO_ROOT/.env"; set +a
fi

# Defaults, used when .env is absent or a key is unset.
: "${GCP_PROJECT_ID:=pulso-madrid}"
: "${GCP_REGION:=europe-southwest1}"
: "${BQ_LOCATION:=EU}"
: "${BQ_DATASET_RAW:=raw}"
: "${BQ_DATASET_FACTS:=facts}"
: "${BQ_DATASET_MARTS:=marts}"
: "${BQ_DATASET_DIMENSIONS:=dimensions}"
: "${BQ_DATASET_OPS:=ops}"

if [ -t 1 ]; then
  R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[1m'; X=$'\033[0m'
else
  R=""; G=""; Y=""; B=""; X=""
fi

section() { printf '\n%s%s%s\n' "$B" "$1" "$X"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$X" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$X" "$1"; }
bad()  { printf '  %s✗%s %s\n' "$R" "$X" "$1"; }
hint() { printf '      %s→ %s%s\n' "$Y" "$1" "$X"; }

have() { command -v "$1" >/dev/null 2>&1; }

#!/usr/bin/env bash
# Environment doctor. Read-only: it checks and reports, it never changes your cloud.
# Anything red prints the exact command that fixes it.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$REPO_ROOT"

problems=0
fail() { bad "$1"; [ -n "${2:-}" ] && hint "$2"; problems=$((problems+1)); }

section "Toolchain"
check_tool() { # label  command  install-hint
  if have "$2"; then ok "$1 — $("$2" --version 2>&1 | head -1 | cut -c1-60)"
  else fail "$1 missing" "$3"; fi
}
check_tool "node"      node      "brew install node   (or: nvm use 20)"
check_tool "uv"        uv        "brew install uv"
check_tool "gcloud"    gcloud    "brew install --cask google-cloud-sdk"
check_tool "terraform" terraform "brew tap hashicorp/tap && brew install hashicorp/tap/terraform"
check_tool "docker"    docker    "https://docker.com"
check_tool "gh"        gh        "brew install gh"

if have uv; then
  pyver="$(uv run python -V 2>/dev/null | awk '{print $2}')"
  case "$pyver" in
    3.12*|3.13*) ok "python $pyver" ;;
    "")          fail "python not resolved by uv" "uv python install 3.12 && uv python pin 3.12" ;;
    *)           fail "python $pyver — want 3.12+" "uv python install 3.12 && uv python pin 3.12" ;;
  esac
fi

section "Authentication"
if have gcloud; then
  acct="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null)"
  [ -n "$acct" ] && ok "CLI as $acct" || fail "gcloud not logged in" "scripts/auth.sh"
  gcloud auth application-default print-access-token >/dev/null 2>&1 \
    && ok "ADC present" || fail "no ADC" "scripts/auth.sh"
fi

section "Google Cloud — $GCP_PROJECT_ID"
if have gcloud && [ -n "${acct:-}" ]; then
  proj="$(gcloud config get-value project 2>/dev/null)"
  [ "$proj" = "$GCP_PROJECT_ID" ] && ok "active project $proj" \
    || fail "active project is '$proj', expected '$GCP_PROJECT_ID'" "gcloud config set project $GCP_PROJECT_ID"

  if gcloud billing projects describe "$GCP_PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null | grep -qi true; then
    ok "billing enabled"
  else
    fail "billing not enabled" "gcloud billing projects link $GCP_PROJECT_ID --billing-account=<ID>"
  fi

  # Datasets, and their locations — location is permanent, so verify it.
  if have bq; then
    existing="$(bq ls --format=json 2>/dev/null | python3 -c 'import sys,json;print(" ".join(d["datasetReference"]["datasetId"] for d in json.load(sys.stdin)))' 2>/dev/null)"
    for ds in "$BQ_DATASET_RAW" "$BQ_DATASET_CLEAN" "$BQ_DATASET_ANALYTICS"; do
      if printf '%s' "$existing" | grep -qw "$ds"; then
        loc="$(bq show --format=json "$GCP_PROJECT_ID:$ds" 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["location"])' 2>/dev/null)"
        if [ "$loc" = "$BQ_LOCATION" ]; then ok "dataset $ds ($loc)"
        else fail "dataset $ds is in $loc, expected $BQ_LOCATION" "location is permanent — recreate and copy"; fi
      else
        fail "dataset $ds missing" "bq --location=$BQ_LOCATION mk --dataset $GCP_PROJECT_ID:$ds"
      fi
    done
  fi

  # Cost guardrail. The console can't set this — see docs/setup/getting-started.md step 4.
  q="$(gcloud quotas info describe QueryUsagePerDay --service=bigquery.googleapis.com \
        --project="$GCP_PROJECT_ID" --format='value(dimensionsInfos[0].details.value)' 2>/dev/null)"
  if [ -n "$q" ] && [ "$q" -lt 1048576 ] 2>/dev/null; then
    ok "BigQuery daily query quota: $((q/1024)) GiB"
  else
    warn "BigQuery daily query quota is ${q:-unknown} MiB — no meaningful cap"
    hint "see docs/setup/getting-started.md step 4"
  fi
fi

section "Dependencies"
if [ -f apps/api/pyproject.toml ]; then
  (cd apps/api && uv sync --quiet) && ok "api deps" || fail "uv sync failed in apps/api"
else warn "apps/api not scaffolded yet"; fi
if [ -f apps/web/package.json ]; then
  (cd apps/web && npm install --silent) && ok "web deps" || fail "npm install failed in apps/web"
else warn "apps/web not scaffolded yet"; fi

if [ "$problems" -eq 0 ]; then
  printf '\n%sEnvironment ready.%s\n' "$G" "$X"
else
  printf '\n%s%d problem(s).%s Fix the arrows above and re-run.\n' "$R" "$problems" "$X"; exit 1
fi

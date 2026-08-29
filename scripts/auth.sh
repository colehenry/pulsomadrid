#!/usr/bin/env bash
# Authenticate to Google Cloud. Idempotent — skips whatever is already valid.
#
#   scripts/auth.sh          check, and log in only what's missing
#   scripts/auth.sh --force  re-run both logins regardless
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

force=false
[ "${1:-}" = "--force" ] && force=true

have gcloud || { bad "gcloud not installed"; hint "brew install --cask google-cloud-sdk"; exit 1; }

section "Google Cloud authentication"

# 1. CLI identity — lets the `gcloud` and `bq` commands act as you.
account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
if [ -z "$account" ] || $force; then
  warn "logging in the CLI (a browser will open)"
  gcloud auth login
  account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)')"
fi
ok "CLI authenticated as $account"

# 2. Application Default Credentials — lets local *code* act as you, with no key file.
if ! gcloud auth application-default print-access-token >/dev/null 2>&1 || $force; then
  warn "creating Application Default Credentials (a browser will open)"
  gcloud auth application-default login
fi
ok "ADC present"

# 3. Active project.
current="$(gcloud config get-value project 2>/dev/null || true)"
if [ "$current" != "$GCP_PROJECT_ID" ]; then
  gcloud config set project "$GCP_PROJECT_ID" >/dev/null
fi
ok "project set to $GCP_PROJECT_ID"

# 4. Quota project — which project gets billed for API calls made by local code.
gcloud auth application-default set-quota-project "$GCP_PROJECT_ID" >/dev/null 2>&1 || \
  warn "could not set ADC quota project (harmless, but you'll see a warning on API calls)"
ok "ADC quota project set"

printf '\n%sAuthenticated.%s Run scripts/setup.sh to verify the whole environment.\n' "$G" "$X"

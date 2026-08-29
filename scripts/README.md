# scripts/

Every repeated command lives here, so nothing important survives only in shell history.

| Script | What it does | When |
|---|---|---|
| `auth.sh` | Logs in to Google Cloud — CLI identity *and* ADC — sets the project and quota project. Idempotent: skips whatever is already valid. `--force` re-runs both. | First setup, and whenever credentials expire |
| `setup.sh` | **Environment doctor.** Checks toolchain, auth, GCP project, billing, datasets and their locations, the BigQuery cost quota, and installs deps. Read-only against your cloud — it reports, it never changes anything. Every failure prints the exact fix. | Any time something feels wrong |
| `dev.sh` | Runs web + api locally. `web` / `api` for one; `--kill` frees ports 3000 and 8000. | Daily |
| `check.sh` | Lint, typecheck, test — both stacks. Same commands CI runs. | Before every commit |
| `lib.sh` | Shared helpers. Loads `.env`, defines the output colours. Sourced, never run directly. | — |

## Configuration

All scripts read `.env` (copy `.env.example`). One place for the project id, region,
BigQuery location, and dataset names — so renaming a dataset doesn't mean grepping
through scripts.

`.env` is gitignored. `.env.example` is committed and should stay in sync.

## Conventions

- Scripts fix *local* things and only report on *cloud* things. Anything that spends
  money or is permanent gets printed for you to run, never executed automatically.
- Every failure prints the command that fixes it. A red line you can't act on is a bug
  in the script.
- Adding a repeated command? Put it here rather than in a doc, and add a row above.

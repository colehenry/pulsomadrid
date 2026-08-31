# Infrastructure

Terraform for every Google Cloud resource Pulso uses.

Everything here already existed before this configuration did — it was created by hand
while building the first pipeline, then imported. That is why the useful check is
`terraform plan` reporting **0 to destroy**: it proves the code describes reality.

## What is managed here

| Resource | Notes |
|---|---|
| 5 BigQuery datasets | `raw`, `dimensions`, `facts`, `marts`, `ops`. Location is **permanent** — see `bq_location` |
| GCS archive bucket | `pulso-madrid-raw`, with Nearline at 30 days and Coldline at 90 |
| Enabled APIs | BigQuery, Storage, Cloud Run, Scheduler, Artifact Registry, Cloud Build, Logging, Monitoring, IAM |

## What is deliberately not managed here

**BigQuery tables.** Their definitions live in `pipelines/gtfs/ddls.sql`, where each
column carries an inline description. Terraform would mean maintaining schemas as JSON
and losing that. They move to dbt in Stage 3.

*The known cost of this:* `CREATE TABLE IF NOT EXISTS` cannot alter anything, so every
column change during M1 meant dropping and recreating a table by hand. One of those went
wrong and left a table stale until an audit caught it. Terraform or dbt would compute
that diff. This is a real trade-off, not an oversight.

**The BigQuery daily query quota.** Set once, outside Terraform, because the Cloud Quotas
resource is still beta and the console silently refuses a decrease this large:

```bash
gcloud quotas preferences create \
  --service=bigquery.googleapis.com --project=pulso-madrid \
  --quota-id=QueryUsagePerDay --preferred-value=51200 \
  --allow-high-percentage-quota-decrease \
  --preference-id=bigquery-query-usage-per-day
```

51200 MiB is 50 GiB per day, and it is a **hard stop**: queries beyond it fail rather
than bill.

**The state bucket**, `pulso-madrid-tfstate`. Terraform cannot create the bucket that
holds its own state. Bootstrapped once:

```bash
gcloud storage buckets create gs://pulso-madrid-tfstate --location=EU --uniform-bucket-level-access
gcloud storage buckets update  gs://pulso-madrid-tfstate --versioning
```

Versioning matters: a truncated state file is otherwise unrecoverable.

## Using it

```bash
cd infrastructure
terraform init          # once, and after any provider change
terraform plan          # always read this before applying
terraform apply         # you run this, never an agent
```

`terraform apply` is the one command here that changes real infrastructure. Per
`AGENTS.md` agents print it and never run it.

## Current plan output

As of 2026-08-30, from a clean import:

```
Plan: 5 to add, 5 to change, 0 to destroy.
```

- **5 to add** — Cloud Run, Scheduler, Artifact Registry, Cloud Build and IAM APIs,
  which M2 needs and which were never enabled.
- **5 to change** — descriptions and a `managed_by = terraform` label on each dataset,
  updated in place.
- **0 to destroy** — the point. Nothing here would be recreated.

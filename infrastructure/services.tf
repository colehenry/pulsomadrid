# Identities for the workloads that run on Railway.
#
# The accounts and their permissions are code; only the key itself is created by hand,
# because a key is a credential and Terraform state is not where one should live. See
# the runbook in README.md.
#
# One account per workload, never one shared: the API must not be able to write to the
# warehouse, and the pipeline has no reason to serve HTTP.

resource "google_service_account" "api" {
  account_id   = "pulso-api"
  display_name = "Pulso API"
  description  = "Serves /api/network and /api/vehicles. Read-only on the warehouse."
  project      = var.project_id
}

resource "google_service_account" "ingest" {
  account_id   = "pulso-ingest"
  display_name = "Pulso ingestion"
  description  = "Downloads the Renfe and CRTM feeds, archives to GCS, loads BigQuery."
  project      = var.project_id
}

# Separate from `ingest` on purpose, though the two do similar work. They are different
# workloads: one runs for four minutes a day, the other runs forever. Sharing an account
# would mean the audit log could not say which of them wrote a row, and rotating a key
# for one would stop the other. Its grants are also narrower — the recorder never touches
# raw or marts, and only reads dimensions.
resource "google_service_account" "recorder" {
  account_id   = "pulso-recorder"
  display_name = "Pulso recorder"
  description  = "Polls Renfe GTFS-RT, appends observations to BigQuery, archives to GCS."
  project      = var.project_id
}

# Running any query needs this at project level. It grants the right to *start a job*,
# not the right to read any particular data — that is the dataset grants below.
resource "google_project_iam_member" "bigquery_job_user" {
  for_each = {
    api      = google_service_account.api.email
    ingest   = google_service_account.ingest.email
    recorder = google_service_account.recorder.email
  }

  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${each.value}"
}

# The API reads dimensions and facts. Deliberately not raw, not ops.
#
# For the Stage 3 conversation: conventions.md §1 says marts is "the only thing an API
# touches", and this grant is that rule not yet being true. When marts exists, this list
# should shrink to marts alone — and the shrinking is the proof it happened.
resource "google_bigquery_dataset_iam_member" "api_reader" {
  for_each = toset(["dimensions", "facts"])

  dataset_id = google_bigquery_dataset.this[each.key].dataset_id
  project    = var.project_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.api.email}"
}

# The ingestion job writes every layer except marts, which dbt owns from Stage 3.
resource "google_bigquery_dataset_iam_member" "ingest_writer" {
  for_each = toset(["raw", "dimensions", "facts", "ops"])

  dataset_id = google_bigquery_dataset.this[each.key].dataset_id
  project    = var.project_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.ingest.email}"
}

# The recorder reads the schedule to filter the feed to Madrid, and writes its
# observations. Editor on facts and ops, viewer on dimensions, nothing at all on raw or
# marts — narrower than the ingestion job, because it does less.
resource "google_bigquery_dataset_iam_member" "recorder_writer" {
  for_each = toset(["facts", "ops"])

  dataset_id = google_bigquery_dataset.this[each.key].dataset_id
  project    = var.project_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.recorder.email}"
}

resource "google_bigquery_dataset_iam_member" "recorder_reader" {
  dataset_id = google_bigquery_dataset.this["dimensions"].dataset_id
  project    = var.project_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.recorder.email}"
}

# Create, not admin: the archive is append-only by design, so the job that writes it has
# no need for permission to delete from it.
resource "google_storage_bucket_iam_member" "ingest_archive_writer" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.ingest.email}"
}

resource "google_storage_bucket_iam_member" "recorder_archive_writer" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.recorder.email}"
}

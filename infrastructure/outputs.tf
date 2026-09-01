output "datasets" {
  description = "BigQuery datasets and their location."
  value       = { for k, d in google_bigquery_dataset.this : k => d.location }
}

output "raw_bucket" {
  description = "gs:// URI of the immutable source archive."
  value       = "gs://${google_storage_bucket.raw.name}"
}


output "service_accounts" {
  description = "Identities the Railway services authenticate as. Keys are created by hand — see README.md."
  value = {
    api    = google_service_account.api.email
    ingest = google_service_account.ingest.email
  }
}

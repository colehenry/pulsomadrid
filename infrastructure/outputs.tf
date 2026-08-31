output "datasets" {
  description = "BigQuery datasets and their location."
  value       = { for k, d in google_bigquery_dataset.this : k => d.location }
}

output "raw_bucket" {
  description = "gs:// URI of the immutable source archive."
  value       = "gs://${google_storage_bucket.raw.name}"
}

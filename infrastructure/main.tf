# Pulso Madrid infrastructure.
#
# Everything here already exists — it was created by hand while building the first
# pipeline, then imported. `terraform plan` showing no changes is the proof that this
# file describes reality rather than an aspiration.
#
# Not managed here: BigQuery tables. Their definitions live in
# pipelines/gtfs/ddls.sql, with column descriptions inline. They move to dbt in Stage 3.

locals {
  # raw        as published, current load only
  # dimensions the network: stations, lines, stopping patterns
  # facts      the schedule: trips and stops
  # marts      precomputed answers the API reads
  # ops        pipeline run records
  datasets = {
    raw        = "Source data as published, filtered to Madrid, no type coercion. Truncated and replaced on each load."
    dimensions = "The network: what exists. Stations, lines and stopping patterns."
    facts      = "The schedule: what runs and when. Trips and their stops."
    marts      = "Precomputed answers the API reads. Populated from Stage 3."
    ops        = "Pipeline run records and rejected rows. Written by every source."
  }
}

resource "google_project_service" "enabled" {
  for_each = toset([
    "bigquery.googleapis.com",
    "storage.googleapis.com",
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "iam.googleapis.com",
  ])

  project = var.project_id
  service = each.key

  # Leave the API enabled if it is removed from this list; disabling one silently
  # breaks anything still using it.
  disable_on_destroy = false
}

resource "google_bigquery_dataset" "this" {
  for_each = local.datasets

  dataset_id  = each.key
  description = each.value
  location    = var.bq_location
  project     = var.project_id

  labels = {
    managed_by = "terraform"
  }

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket" "raw" {
  name                        = var.raw_bucket_name
  location                    = var.bq_location
  project                     = var.project_id
  uniform_bucket_level_access = true
  storage_class               = "STANDARD"

  # The archive is the layer we reprocess from, so nothing is ever deleted — it only
  # gets colder. A full Renfe feed is 16.5 MB, so a year of weekly loads is under 1 GB.
  lifecycle_rule {
    condition { age = 30 }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition { age = 90 }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  depends_on = [google_project_service.enabled]
}

variable "project_id" {
  description = "GCP project holding everything in this configuration."
  type        = string
  default     = "pulso-madrid"
}

variable "region" {
  description = "Region for regional resources. Madrid, to sit close to users and to the Spanish data sources."
  type        = string
  default     = "europe-southwest1"
}

variable "bq_location" {
  description = <<-EOT
    BigQuery dataset location. Permanent once a dataset exists: it cannot be changed,
    and a query touching two locations fails. EU keeps Spanish data in one jurisdiction.
  EOT
  type        = string
  default     = "EU"
}

variable "raw_bucket_name" {
  description = "Bucket holding the unmodified original of every file we ingest."
  type        = string
  default     = "pulso-madrid-raw"
}

variable "bq_daily_query_limit_mib" {
  description = <<-EOT
    Hard cap on BigQuery bytes scanned per day, in MiB. Queries beyond it fail rather
    than bill. The default is 209715200 MiB (200 TiB); 51200 MiB is 50 GiB.
  EOT
  type        = number
  default     = 51200
}

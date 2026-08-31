terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # State lives in GCS so it is durable and not tied to one laptop. The state bucket
  # itself is the one thing Terraform cannot create for itself; see README.md.
  backend "gcs" {
    bucket = "pulso-madrid-tfstate"
    prefix = "infrastructure"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

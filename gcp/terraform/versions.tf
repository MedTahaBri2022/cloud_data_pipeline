terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.5"
    }
  }

  # Remote state for a shared environment (create the bucket once, by hand):
  # backend "gcs" {
  #   bucket = "<project>-terraform-state"
  #   prefix = "retail-pipeline"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region

  default_labels = {
    app         = "retail-pipeline"
    environment = var.environment
    managed_by  = "terraform"
  }
}

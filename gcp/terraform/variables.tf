variable "project_id" {
  description = "GCP project that hosts the platform."
  type        = string
}

variable "environment" {
  description = "dev, staging or prod. Production resources are protected against deletion."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "region" {
  description = "Region of the bucket, the Cloud Run job and the scheduler."
  type        = string
  default     = "europe-west1"
}

variable "bigquery_location" {
  description = "Location of the BigQuery datasets. Must contain the bucket's region for load jobs."
  type        = string
  default     = "EU"
}

variable "export_retention_days" {
  description = "Exported files are deleted after this many days: BigQuery holds the data, the files are only a staging area and an audit trail."
  type        = number
  default     = 30

  validation {
    condition     = var.export_retention_days >= 1 && var.export_retention_days <= 365
    error_message = "export_retention_days must be between 1 and 365."
  }
}

variable "etl_image_tag" {
  description = "Tag of the pipeline image pushed to Artifact Registry."
  type        = string
  default     = "latest"
}

variable "export_schedule" {
  description = "Cron expression (UTC) of the export job. Runs after the hourly pipeline."
  type        = string
  default     = "30 * * * *"
}

variable "warehouse_network" {
  description = "VPC network the Cloud Run job uses to reach the PostgreSQL warehouse (Cloud SQL private IP or a peered network). Null keeps the default egress."
  type        = string
  default     = null
}

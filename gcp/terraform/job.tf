# The export runs as a Cloud Run job: the same pipeline image as the
# Kubernetes CronJob, started with another stage name.
resource "google_cloud_run_v2_job" "export" {
  name                = "${local.name}-export"
  location            = var.region
  deletion_protection = local.protected

  template {
    task_count = 1

    template {
      service_account = google_service_account.export.email
      timeout         = "900s"
      max_retries     = 1

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/retail-etl:${var.etl_image_tag}"
        args  = ["export_to_gcp"]

        env {
          name  = "GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "GCS_BUCKET"
          value = google_storage_bucket.exports.name
        }
        env {
          name  = "BQ_DATASET"
          value = google_bigquery_dataset.warehouse.dataset_id
        }
        env {
          name = "WAREHOUSE_DSN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.warehouse_dsn.secret_id
              version = "latest"
            }
          }
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }

      dynamic "vpc_access" {
        for_each = var.warehouse_network == null ? [] : [var.warehouse_network]
        content {
          egress = "PRIVATE_RANGES_ONLY"
          network_interfaces {
            network = vpc_access.value
          }
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.export_reads_dsn]
}

resource "google_cloud_scheduler_job" "export" {
  name      = "${local.name}-export"
  region    = var.region
  schedule  = var.export_schedule
  time_zone = "Etc/UTC"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/${google_cloud_run_v2_job.export.id}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_runs_export]
}

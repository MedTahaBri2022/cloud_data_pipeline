# Two identities with the least privilege each needs. Neither has a
# project-wide editor or owner role.

# The export job: writes files to its bucket, loads tables in its dataset,
# reads one secret.
resource "google_service_account" "export" {
  account_id   = "${local.name}-export"
  display_name = "Retail pipeline export job (${var.environment})"
}

resource "google_storage_bucket_iam_member" "export_writes_bucket" {
  bucket = google_storage_bucket.exports.name
  role   = "roles/storage.objectUser"
  member = google_service_account.export.member
}

resource "google_bigquery_dataset_iam_member" "export_edits_warehouse" {
  dataset_id = google_bigquery_dataset.warehouse.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.export.member
}

# Running a load job is a project-level permission in BigQuery.
resource "google_project_iam_member" "export_runs_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = google_service_account.export.member
}

resource "google_secret_manager_secret_iam_member" "export_reads_dsn" {
  secret_id = google_secret_manager_secret.warehouse_dsn.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.export.member
}

# The scheduler: allowed to start this one job and nothing else.
resource "google_service_account" "scheduler" {
  account_id   = "${local.name}-scheduler"
  display_name = "Retail pipeline scheduler (${var.environment})"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_runs_export" {
  name     = google_cloud_run_v2_job.export.name
  location = google_cloud_run_v2_job.export.location
  role     = "roles/run.invoker"
  member   = google_service_account.scheduler.member
}

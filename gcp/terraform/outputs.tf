output "exports_bucket" {
  value = google_storage_bucket.exports.name
}

output "warehouse_dataset" {
  value = "${var.project_id}.${google_bigquery_dataset.warehouse.dataset_id}"
}

output "marts_dataset" {
  value = "${var.project_id}.${google_bigquery_dataset.marts.dataset_id}"
}

output "image_repository" {
  description = "docker push target for the pipeline image."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "export_job" {
  value = google_cloud_run_v2_job.export.name
}

output "warehouse_dsn_secret" {
  description = "Add the connection string with: gcloud secrets versions add <this> --data-file=-"
  value       = google_secret_manager_secret.warehouse_dsn.secret_id
}

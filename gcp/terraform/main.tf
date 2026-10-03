locals {
  name      = "retail-${var.environment}"
  protected = var.environment == "prod"

  # BigQuery dataset ids only accept letters, digits and underscores.
  warehouse_dataset = "retail_warehouse_${var.environment}"
  marts_dataset     = "retail_marts_${var.environment}"

  schema_dir = "${path.module}/../bigquery/schemas"
  marts_dir  = "${path.module}/../bigquery/marts"

  tables = toset(["dim_date", "dim_customer", "dim_product", "fact_order_items"])
  marts  = toset(["monthly_revenue", "product_performance", "customer_segments", "sales_by_country_channel"])

  services = toset([
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ])
}

resource "google_project_service" "this" {
  for_each = local.services

  service = each.value
  # Turning an API off on destroy would break anything else using the project.
  disable_on_destroy = false
}

# ------------------------------------------------------------------ storage
# Landing area of the exports: one folder per run, deleted after the
# retention period. Never public, access through IAM only.
resource "google_storage_bucket" "exports" {
  name     = "${var.project_id}-${local.name}-exports"
  location = var.region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !local.protected

  lifecycle_rule {
    condition {
      age = var.export_retention_days
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.this]
}

# ----------------------------------------------------------------- BigQuery
resource "google_bigquery_dataset" "warehouse" {
  dataset_id    = local.warehouse_dataset
  friendly_name = "Retail warehouse (${var.environment})"
  description   = "Star schema exported from the PostgreSQL warehouse."
  location      = var.bigquery_location

  delete_contents_on_destroy = !local.protected

  depends_on = [google_project_service.this]
}

resource "google_bigquery_dataset" "marts" {
  dataset_id    = local.marts_dataset
  friendly_name = "Retail marts (${var.environment})"
  description   = "Reporting views read by BI tools."
  location      = var.bigquery_location

  delete_contents_on_destroy = !local.protected

  depends_on = [google_project_service.this]
}

# The same JSON schema files are used here and by the export job, so the
# table definition and the load cannot drift apart.
resource "google_bigquery_table" "warehouse" {
  for_each = local.tables

  dataset_id          = google_bigquery_dataset.warehouse.dataset_id
  table_id            = each.key
  schema              = file("${local.schema_dir}/${each.key}.json")
  deletion_protection = local.protected

  # The fact table is partitioned by day and clustered on the join keys: a
  # dashboard filtered on a month scans that month only.
  dynamic "time_partitioning" {
    for_each = each.key == "fact_order_items" ? [1] : []
    content {
      type  = "DAY"
      field = "order_date"
    }
  }

  clustering = each.key == "fact_order_items" ? ["customer_key", "product_key"] : null
}

resource "google_bigquery_table" "marts" {
  for_each = local.marts

  dataset_id          = google_bigquery_dataset.marts.dataset_id
  table_id            = each.key
  deletion_protection = local.protected

  view {
    query = templatefile("${local.marts_dir}/${each.key}.sql", {
      warehouse = "${var.project_id}.${google_bigquery_dataset.warehouse.dataset_id}"
    })
    use_legacy_sql = false
  }

  # BigQuery checks the query of a view when it is created.
  depends_on = [google_bigquery_table.warehouse]
}

# ---------------------------------------------------------- image registry
resource "google_artifact_registry_repository" "images" {
  repository_id = local.name
  location      = var.region
  format        = "DOCKER"
  description   = "Images of the retail pipeline."

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  depends_on = [google_project_service.this]
}

# ------------------------------------------------------------------ secret
# Only the secret container is managed here. The connection string itself is
# added outside Terraform so that it never lands in the state file:
#   gcloud secrets versions add <id> --data-file=-
resource "google_secret_manager_secret" "warehouse_dsn" {
  secret_id = "${local.name}-warehouse-dsn"

  replication {
    auto {}
  }

  depends_on = [google_project_service.this]
}

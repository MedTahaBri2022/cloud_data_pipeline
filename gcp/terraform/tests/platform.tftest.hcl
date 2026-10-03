# Offline tests of the configuration: `terraform test` plans it against a
# mocked Google provider, so no GCP account or credentials are needed.

mock_provider "google" {}

variables {
  project_id = "demo-project"
}

run "bucket_is_private_and_expires" {
  command = plan

  assert {
    condition     = google_storage_bucket.exports.public_access_prevention == "enforced"
    error_message = "The exports bucket must never be public."
  }
  assert {
    condition     = google_storage_bucket.exports.uniform_bucket_level_access
    error_message = "Access to the bucket must go through IAM only."
  }
  assert {
    condition     = one([for rule in google_storage_bucket.exports.lifecycle_rule : one([for c in rule.condition : c.age])]) == 30
    error_message = "Exported files must expire after the retention period."
  }
}

run "fact_table_is_partitioned_and_clustered" {
  command = plan

  assert {
    condition     = google_bigquery_table.warehouse["fact_order_items"].time_partitioning[0].field == "order_date"
    error_message = "The fact table must be partitioned by order date."
  }
  assert {
    condition     = google_bigquery_table.warehouse["fact_order_items"].clustering == tolist(["customer_key", "product_key"])
    error_message = "The fact table must be clustered on its join keys."
  }
  assert {
    condition     = length(google_bigquery_table.warehouse["dim_date"].time_partitioning) == 0
    error_message = "Dimensions are small and must not be partitioned."
  }
}

run "marts_are_views_on_the_warehouse_dataset" {
  command = plan

  assert {
    condition     = length(google_bigquery_table.marts) == 4
    error_message = "The four reporting marts must exist."
  }
  assert {
    condition     = strcontains(google_bigquery_table.marts["monthly_revenue"].view[0].query, "demo-project.retail_warehouse_dev.fact_order_items")
    error_message = "Views must read the warehouse dataset of the same environment."
  }
  assert {
    condition     = !google_bigquery_table.marts["monthly_revenue"].view[0].use_legacy_sql
    error_message = "Views must use standard SQL."
  }
}

run "identities_have_narrow_roles" {
  command = plan

  assert {
    condition     = google_project_iam_member.export_runs_jobs.role == "roles/bigquery.jobUser"
    error_message = "The only project-level role of the export job is to run BigQuery jobs."
  }
  assert {
    condition     = google_cloud_run_v2_job_iam_member.scheduler_runs_export.role == "roles/run.invoker"
    error_message = "The scheduler may only start the job."
  }
  assert {
    condition = length([
      for env in google_cloud_run_v2_job.export.template[0].template[0].containers[0].env :
      env if env.name == "WAREHOUSE_DSN" && length(env.value_source) == 1 && env.value == null
    ]) == 1
    error_message = "The connection string must come from Secret Manager, not from a plain variable."
  }
}

run "dev_resources_can_be_destroyed" {
  command = plan

  assert {
    condition     = !google_bigquery_table.warehouse["fact_order_items"].deletion_protection
    error_message = "Dev tables must be disposable."
  }
}

run "prod_resources_are_protected" {
  command = plan

  variables {
    environment = "prod"
  }

  assert {
    condition     = google_bigquery_table.warehouse["fact_order_items"].deletion_protection
    error_message = "Production tables must be protected against deletion."
  }
  assert {
    condition     = !google_storage_bucket.exports.force_destroy
    error_message = "A production bucket must not be destroyed with its content."
  }
  assert {
    condition     = google_cloud_run_v2_job.export.deletion_protection
    error_message = "The production job must be protected against deletion."
  }
}

run "unknown_environment_is_rejected" {
  command = plan

  variables {
    environment = "test"
  }

  expect_failures = [var.environment]
}

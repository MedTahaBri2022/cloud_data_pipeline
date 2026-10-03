# Cloud Data Pipeline & Analytics Platform

An end-to-end data pipeline for structured retail data: events are ingested
through a Node.js API into MongoDB, validated and loaded by a Python ETL into a
PostgreSQL star schema, orchestrated by Apache Airflow, and served to
Power BI through a reporting layer. Every component is containerized and the
platform also runs on Kubernetes. The warehouse is also exported to
Google Cloud (Cloud Storage, then BigQuery), with the infrastructure written in
Terraform.

**Node.js · Python · Apache Airflow · PostgreSQL · MongoDB · Docker · Kubernetes · Google Cloud (BigQuery, Cloud Storage, Cloud Run) · Terraform · Power BI**

[![CI](https://github.com/MedTahaBri2022/cloud_data_pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/MedTahaBri2022/cloud_data_pipeline/actions/workflows/ci.yml)

## Architecture

```
 upstream systems                 back-office exports
 (order events, JSON)             (customers.csv, products.csv)
        │                                   │
        ▼                                   │
┌────────────────┐                          │
│ ingestion-api  │  Node.js / Express       │
│ shape checks   │                          │
└───────┬────────┘                          │
        ▼                                   │
┌────────────────┐                          │
│ MongoDB        │  raw landing zone        │
│ raw_orders     │  (one document per order)│
└───────┬────────┘                          │
        │        Airflow DAG `retail_pipeline` (hourly)
        ▼                                   ▼
   load_orders (incremental)          load_reference
        └──────────────┬────────────────────┘
                       ▼
┌───────────────────────────────────────────────────────────┐
│ PostgreSQL warehouse                                      │
│  staging.*     validated copies of the sources            │
│  etl.*         watermarks, rejected records, check results│
│  warehouse.*   star schema: dim_date, dim_customer,       │
│                dim_product, fact_order_items              │
│  marts.*       materialized views for reporting           │
└───────────────────────┬───────────────────────────────────┘
                        ▼
          Power BI  ·  /reports/* JSON API
```

## The pipeline

| Task | What it does |
| --- | --- |
| `init_schema` | Creates schemas, tables and marts if missing (idempotent DDL). |
| `load_reference` | Validates customers and products (CSV) and upserts them into staging. |
| `load_orders` | Reads only the order documents changed since the last run (watermark), validates them, upserts them. |
| `build_warehouse` | Staging to star schema in SQL: surrogate keys, generated calendar, measures computed once. |
| `check_quality` | Six reconciliation and integrity checks. A failure stops the run **before** the reporting layer is refreshed. |
| `refresh_marts` | Refreshes the materialized views read by Power BI. |

Design decisions:

- **Idempotent by construction.** Every write is an upsert on a business key
  and the watermark moves in the same transaction as the data it covers. A
  task can be retried, or a whole run replayed, without duplicates – there is
  a test that runs the pipeline twice and compares.
- **Incremental, with a safety overlap.** Each run re-reads the five minutes
  before the watermark: an event written while the previous run was reading
  would otherwise be skipped forever. Re-reading is harmless thanks to the
  upserts.
- **Rejected, not dropped.** Invalid records go to `etl.rejected_records` with
  every reason they failed. When the source sends a corrected version the
  rejection disappears on its own.
- **An order is atomic.** One bad line rejects the whole order: loading its
  valid lines only would silently understate its revenue.
- **Two levels of validation.** The API checks the *shape* of an event and
  stores it even if it is wrong for the business; the pipeline applies the
  business rules. Refusing bad events at the door would lose the evidence that
  a producer sends bad data.
- **Updates are first-class.** A new event for an existing order (status
  change, correction, removed line) replaces it down to the fact table.
- **One code base, three ways to run it.** The same `pipeline` package is
  called task by task by Airflow, as one command by the Kubernetes CronJob
  (`python -m pipeline all`) and stage by stage by a developer.
- **Light DAG file.** Drivers are imported inside the tasks, so the scheduler
  parses the DAG without touching a database driver.
- **Data failures are not retried.** Tasks retry twice with exponential
  backoff, except `check_quality`: bad data does not get better on retry.

## Results of a local run

6,300 events sent (6,000 orders plus 300 orders sent twice, as *placed* then
*delivered*):

| | |
| --- | ---: |
| Raw documents in MongoDB | 6,300 |
| Orders loaded | 6,116 |
| Orders rejected (5 different reasons) | 184 |
| Fact rows | 14,863 |
| Revenue, staging vs fact | 1,078,204.98 = 1,078,204.98 |
| Quality checks passed | 6 / 6 |
| DAG run duration | about 12 s |

## Run it locally

Requirements: Docker, Node.js 22+ (for the event simulator).

```bash
docker compose up -d --build        # PostgreSQL, MongoDB, API, Airflow

cd ingestion-api
npm install
npm run simulate -- 5000            # send 5,000 order events to the API
```

Then open Airflow at <http://localhost:8081> (user `admin`, password `admin`
unless overridden in `.env`), un-pause `retail_pipeline` and trigger it, or:

```bash
docker compose exec airflow-scheduler airflow dags unpause retail_pipeline
docker compose exec airflow-scheduler airflow dags trigger retail_pipeline
```

See the result:

```bash
curl localhost:8080/reports/pipeline            # watermark, rejections, checks
curl localhost:8080/reports/monthly-revenue
curl "localhost:8080/reports/top-products?limit=5"
```

| Service | URL |
| --- | --- |
| Ingestion / reporting API | http://localhost:8080 |
| Airflow | http://localhost:8081 |
| PostgreSQL (`warehouse`) | `localhost:54324`, user `pipeline` |
| MongoDB (`raw`) | `localhost:27018` |

The credentials in `docker-compose.yml` are development defaults for a local
machine; override them in `.env` (see `.env.example`).

### Without Airflow

```bash
cd etl
pip install -r requirements.txt
python -m pipeline all              # or a single stage: python -m pipeline load_orders
```

## Tests

```bash
cd etl && pip install -r requirements-dev.txt && pytest
cd ingestion-api && npm test
docker compose run --rm airflow-scheduler python /opt/airflow/check_dags.py
```

- **26 unit tests** of the validation rules (pure functions).
- **4 integration tests** against real PostgreSQL and MongoDB: first load,
  replay without change, incremental updates and corrections, and a failed
  quality check that is recorded and then repaired. They are skipped when the
  databases are not running, and mandatory in CI.
- **13 tests of the Google Cloud export**: serialisation, load settings, a
  reconciliation failure with fake clients, and an end-to-end run against the
  Cloud Storage and BigQuery emulators (export, re-export, and the four marts
  compared row by row with PostgreSQL).
- **7 Terraform tests** (`terraform test`, mocked provider).
- **10 API tests** with injected fake stores.
- **DAG integrity**: the DAG imports, has the expected task order and
  settings.
- CI also runs the whole DAG end to end in the Airflow image and checks that
  the reporting API returns data.

## Kubernetes

```bash
kubectl apply -f k8s/namespace.yaml
# create the Secret (see k8s/secret.example.yaml), then:
kubectl apply -k k8s
kubectl -n retail-pipeline create job --from=cronjob/retail-etl first-run
```

| Manifest | Kind | Notes |
| --- | --- | --- |
| `postgres.yaml`, `mongo.yaml` | StatefulSet + headless Service | PersistentVolumeClaim per pod, readiness probes |
| `ingestion-api.yaml` | Deployment (2 replicas) + NodePort Service | rolling update with `maxUnavailable: 0`, probes, read-only root filesystem, non-root |
| `etl-cronjob.yaml` | CronJob (hourly) | `concurrencyPolicy: Forbid`, bounded retries and deadline |
| `kustomization.yaml` | ConfigMap generator | settings change → new ConfigMap name → rolling restart |

Secrets are created out-of-band and never committed. The images
(`retail-etl`, `retail-ingestion-api`) are built from `etl/Dockerfile` and
`ingestion-api/Dockerfile`.

Verified on a local single-node k3s cluster: 2,000 events sent through the
NodePort, a job created from the CronJob loaded 1,942 orders, rejected 58 and
passed the six checks in 8 seconds.

## Google Cloud

The same warehouse can be published to BigQuery, so analysts query it with
SQL in the console and BI tools connect to a managed warehouse instead of the
operational database.

```
PostgreSQL warehouse ──► export_to_gcp (Cloud Run job, every hour)
                             │  1. one consistent snapshot of the 4 tables
                             ▼
                  Cloud Storage  gs://…-exports/warehouse/run=<time>/*.json
                             │  2. load jobs, WRITE_TRUNCATE
                             ▼
                  BigQuery  retail_warehouse_<env>  (fact partitioned by day,
                             │                       clustered on join keys)
                             ▼
                  BigQuery  retail_marts_<env>      (4 views, same rules as
                                                     the PostgreSQL marts)
```

**Infrastructure** (`gcp/terraform`, 29 resources): the bucket (private,
files deleted after 30 days), two datasets, the four tables and the four mart
views, an Artifact Registry repository for the pipeline image, the Cloud Run
job and the Cloud Scheduler trigger, a Secret Manager secret for the database
connection string, and two service accounts with narrow roles. Production
(`environment = "prod"`) protects tables, job and bucket against deletion.

Design decisions:

- **One schema file per table** (`gcp/bigquery/schemas`), read by Terraform to
  create the table and by the job to load it: they cannot drift apart.
- **Consistent snapshot.** The four tables are read in one read-only,
  repeatable-read transaction, so a fact never points to a customer exported
  from another instant.
- **Atomic, repeatable loads.** Each load job replaces its table in one
  operation (`WRITE_TRUNCATE`); a dashboard never sees half a load and running
  the export twice gives the same tables.
- **Reconciled.** After loading, the row count of each BigQuery table is
  compared with the number of rows exported; a difference fails the job.
- **Less personal data.** Customer e-mail addresses are not exported.
- **No secret in Terraform state.** Terraform creates the secret; its value is
  added with `gcloud secrets versions add`.
- **Same marts, same numbers.** The BigQuery views are the PostgreSQL marts
  translated to GoogleSQL. Comparing them row by row found a real defect: the
  RFM quartiles had no tie-breaker, so customers with equal values were ranked
  differently by the two engines (and could change between two refreshes).
  Both now order by `customer_id` as well.

Deploy:

```bash
cd gcp/terraform
cp terraform.tfvars.example terraform.tfvars    # set project_id
terraform init && terraform apply
gcloud secrets versions add retail-dev-warehouse-dsn --data-file=-   # paste the DSN
docker build -f etl/Dockerfile -t <image_repository>/retail-etl:latest . && docker push …
```

Run the export locally against the emulators:

```bash
docker compose up -d postgres mongo
docker compose -f gcp/docker-compose.emulators.yml up -d
cd etl && pytest tests/test_gcp_export.py
```

On the local demo warehouse (14,863 fact rows) the export takes about
4 seconds against the emulators, and the four BigQuery marts are identical to
the PostgreSQL ones.

## Reporting

[reporting/powerbi.md](reporting/powerbi.md) describes how to connect
Power BI to the warehouse: tables, relationships and DAX measures. The marts:

| Mart | Content |
| --- | --- |
| `marts.monthly_revenue` | orders, units, revenue, margin and margin % per month |
| `marts.product_performance` | sales per product with a revenue rank |
| `marts.customer_segments` | RFM scores (quartiles) and a segment per customer |
| `marts.sales_by_country_channel` | revenue and average order value |

## Repository layout

```
ingestion-api/   Node.js API (ingestion + reporting endpoints), simulator
etl/             Python package `pipeline`: validation, stages, quality checks, tests
sql/             schema, staging → star schema transform, marts
airflow/         DAG, image, DAG integrity check
k8s/             Kubernetes manifests (kustomize)
gcp/             Terraform for Google Cloud, BigQuery schemas and marts, emulators
reporting/       Power BI model and measures
data/raw/        reference CSV files (generated, with a few invalid rows on purpose)
```

## Limits

- The data is synthetic, generated by `ingestion-api/scripts`.
- Dimensions are type 1 (latest value wins); no history of attribute changes.
- Airflow runs with the LocalExecutor in docker-compose. On Kubernetes the
  pipeline runs as a CronJob; Airflow itself is not deployed there.
- No authentication on the API.
- The Google Cloud configuration has been validated, planned and tested with
  a mocked provider, and the export runs against emulators; it has not been
  applied to a real GCP project. Reaching the PostgreSQL warehouse from Cloud
  Run needs a network path (Cloud SQL or a VPC, variable `warehouse_network`).
- The Power BI part is a documented model, not a `.pbix` file.

## License

MIT

# Cloud Data Pipeline & Analytics Platform

An end-to-end data pipeline for structured retail data: events are ingested
through a Node.js API into MongoDB, validated and loaded by a Python ETL into a
PostgreSQL star schema, orchestrated by Apache Airflow, and served to
Power BI through a reporting layer. Every component is containerized and the
platform also runs on Kubernetes.

**Node.js · Python · Apache Airflow · PostgreSQL · MongoDB · Docker · Kubernetes · Power BI**

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
reporting/       Power BI model and measures
data/raw/        reference CSV files (generated, with a few invalid rows on purpose)
```

## Limits

- The data is synthetic, generated by `ingestion-api/scripts`.
- Dimensions are type 1 (latest value wins); no history of attribute changes.
- Airflow runs with the LocalExecutor in docker-compose. On Kubernetes the
  pipeline runs as a CronJob; Airflow itself is not deployed there.
- No authentication on the API.
- The Power BI part is a documented model, not a `.pbix` file.

## License

MIT

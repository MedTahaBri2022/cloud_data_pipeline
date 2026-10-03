"""Export of the star schema to Google Cloud: Cloud Storage, then BigQuery.

    python -m pipeline export_to_gcp

Runs after the pipeline, as a Cloud Run job (see gcp/terraform). Three steps:

1. Read the four warehouse tables in one read-only, repeatable-read
   transaction, so the dimensions and the facts come from the same instant,
   and write them as newline-delimited JSON files.
2. Upload the files to the exports bucket, one folder per run. The files stay
   there for the retention period: they show exactly what was loaded.
3. Load each file into its BigQuery table with WRITE_TRUNCATE. A load job
   replaces a table atomically, so a dashboard never sees half a load, and
   running the export twice gives the same tables.

The row count of every BigQuery table is then compared with the number of rows
exported; a difference stops the job with an error.

Customer e-mail addresses are not exported: the analytics side does not need
them, and personal data that is not copied cannot leak from a copy.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import psycopg2
import psycopg2.extensions

from .config import Settings
from .quality import CheckResult, DataQualityError

log = logging.getLogger(__name__)

# Column lists match gcp/bigquery/schemas/*.json, which also define the tables
# in Terraform.
EXPORTS = {
    "dim_date": """
        SELECT date_key, full_date, year, quarter, month, month_name, day_of_week, is_weekend
          FROM warehouse.dim_date ORDER BY date_key""",
    "dim_customer": """
        SELECT customer_key, customer_id, name, country, signup_date
          FROM warehouse.dim_customer ORDER BY customer_key""",
    "dim_product": """
        SELECT product_key, product_id, name, category, unit_cost, list_price
          FROM warehouse.dim_product ORDER BY product_key""",
    "fact_order_items": """
        SELECT f.order_id, f.product_key, f.customer_key, f.date_key, d.full_date AS order_date,
               f.status, f.channel, f.quantity, f.unit_price, f.revenue, f.cost, f.margin
          FROM warehouse.fact_order_items f
          JOIN warehouse.dim_date d ON d.date_key = f.date_key
         ORDER BY f.order_id, f.product_key""",
}

FETCH_SIZE = 5_000


def _jsonable(value: Any) -> Any:
    # NUMERIC goes as a string: a float would round money.
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"cannot serialise {type(value).__name__}")


@contextmanager
def _snapshot(settings: Settings) -> Iterator[Any]:
    connection = psycopg2.connect(settings.warehouse_dsn)
    try:
        connection.set_session(
            isolation_level=psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ,
            readonly=True,
        )
        with connection:
            yield connection
    finally:
        connection.close()


def write_ndjson(connection: Any, table: str, sql: str, path: Path) -> int:
    """Stream a query to a newline-delimited JSON file; returns the row count."""
    rows = 0
    # A named (server-side) cursor streams the rows instead of loading the
    # whole fact table in memory.
    with connection.cursor(name=f"export_{table}") as cursor, open(
        path, "w", encoding="utf-8", newline="\n"
    ) as handle:
        cursor.itersize = FETCH_SIZE
        cursor.execute(sql)
        columns = None
        for record in cursor:
            if columns is None:
                columns = [column.name for column in cursor.description]
            handle.write(json.dumps(dict(zip(columns, record)), default=_jsonable))
            handle.write("\n")
            rows += 1
    return rows


def _clients(settings: Settings) -> tuple[Any, Any]:
    """Real clients, or emulator clients when the *_EMULATOR_HOST variables are set."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery, storage

    bigquery_emulator = os.environ.get("BIGQUERY_EMULATOR_HOST")
    if bigquery_emulator:
        bigquery_client = bigquery.Client(
            project=settings.gcp_project,
            credentials=AnonymousCredentials(),
            client_options=ClientOptions(api_endpoint=bigquery_emulator),
        )
    else:
        bigquery_client = bigquery.Client(project=settings.gcp_project)

    if os.environ.get("STORAGE_EMULATOR_HOST"):
        storage_client = storage.Client(
            project=settings.gcp_project, credentials=AnonymousCredentials()
        )
    else:
        storage_client = storage.Client(project=settings.gcp_project)
    return storage_client, bigquery_client


def _load_config(settings: Settings, table: str) -> Any:
    from google.cloud import bigquery

    schema_file = settings.schema_dir / f"{table}.json"
    return bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=[
            bigquery.SchemaField.from_api_repr(field)
            for field in json.loads(schema_file.read_text(encoding="utf-8"))
        ],
        # A value BigQuery cannot read fails the whole load instead of being
        # skipped silently.
        max_bad_records=0,
    )


def export_to_gcp(
    settings: Settings,
    storage_client: Any = None,
    bigquery_client: Any = None,
    run_id: str | None = None,
) -> dict[str, int]:
    if not settings.gcp_project or not settings.gcs_bucket:
        raise ValueError("GCP_PROJECT and GCS_BUCKET must be set to export to Google Cloud")
    if storage_client is None or bigquery_client is None:
        storage_client, bigquery_client = _clients(settings)
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bucket = storage_client.bucket(settings.gcs_bucket)

    exported: dict[str, int] = {}
    uris: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as folder:
        with _snapshot(settings) as connection:
            for table, sql in EXPORTS.items():
                path = Path(folder) / f"{table}.json"
                exported[table] = write_ndjson(connection, table, sql, path)

        for table in EXPORTS:
            name = f"warehouse/run={run_id}/{table}.json"
            bucket.blob(name).upload_from_filename(
                str(Path(folder) / f"{table}.json"), content_type="application/x-ndjson"
            )
            uris[table] = f"gs://{settings.gcs_bucket}/{name}"
            log.info("uploaded %s rows to %s", exported[table], uris[table])

    for table, uri in uris.items():
        destination = f"{settings.gcp_project}.{settings.bq_dataset}.{table}"
        job = bigquery_client.load_table_from_uri(
            uri, destination, job_config=_load_config(settings, table)
        )
        job.result()  # raises if the load failed
        log.info("loaded %s", destination)

    failures = []
    for table, expected in exported.items():
        destination = f"{settings.gcp_project}.{settings.bq_dataset}.{table}"
        rows = list(bigquery_client.query(f"SELECT COUNT(*) AS n FROM `{destination}`").result())
        loaded = rows[0]["n"]
        if loaded != expected:
            failures.append(
                CheckResult(
                    name=f"bigquery_rows_{table}",
                    passed=False,
                    observed=f"exported {expected}, BigQuery has {loaded}",
                )
            )
    if failures:
        raise DataQualityError(failures)

    log.info("export %s complete: %s", run_id, exported)
    return exported

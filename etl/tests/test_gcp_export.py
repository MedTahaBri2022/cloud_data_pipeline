"""Tests of the Google Cloud export (pipeline/gcp.py).

The unit tests run anywhere. The end-to-end test needs PostgreSQL, MongoDB and
two emulators, Cloud Storage (fake-gcs-server) and BigQuery (goccy
bigquery-emulator):

    docker compose up -d postgres mongo
    docker compose -f gcp/docker-compose.emulators.yml up -d
    pytest tests/test_gcp_export.py

It is skipped when they are not reachable, except in CI.
"""

from __future__ import annotations

import json
import os
import string
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg2
import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from pipeline import gcp, stages
from pipeline.config import Settings
from pipeline.quality import DataQualityError

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "gcp" / "bigquery" / "schemas"
MARTS_DIR = REPO_ROOT / "gcp" / "bigquery" / "marts"
PROJECT = "demo-project"
DATASET = "retail_warehouse_test"
BUCKET = "retail-test-exports"


# ---------------------------------------------------------------- unit tests


def test_money_and_dates_are_serialised_without_loss():
    line = json.dumps(
        {"revenue": Decimal("1078204.98"), "day": date(2026, 3, 15)}, default=gcp._jsonable
    )
    assert line == '{"revenue": "1078204.98", "day": "2026-03-15"}'


def test_unknown_types_are_refused():
    with pytest.raises(TypeError):
        gcp._jsonable(object())


def test_every_exported_table_has_a_schema_file_and_no_email():
    for table in gcp.EXPORTS:
        fields = [f["name"] for f in json.loads((SCHEMA_DIR / f"{table}.json").read_text())]
        assert fields, table
        assert "email" not in fields


def test_load_replaces_the_table_and_tolerates_no_bad_record():
    settings = _settings()
    config = gcp._load_config(settings, "fact_order_items")
    assert config.write_disposition == "WRITE_TRUNCATE"
    assert config.source_format == "NEWLINE_DELIMITED_JSON"
    assert config.max_bad_records == 0
    assert [field.name for field in config.schema][:5] == [
        "order_id", "product_key", "customer_key", "date_key", "order_date",
    ]


def test_export_needs_a_project_and_a_bucket():
    with pytest.raises(ValueError):
        gcp.export_to_gcp(_settings(gcs_bucket=""), storage_client=object(), bigquery_client=object())


class _FakeBlob:
    def __init__(self, uploads, name):
        self.uploads, self.name = uploads, name

    def upload_from_filename(self, filename, content_type=None):
        self.uploads.append(self.name)


class _FakeStorage:
    def __init__(self):
        self.uploads = []

    def bucket(self, name):
        storage = self

        class Bucket:
            def blob(self, blob_name):
                return _FakeBlob(storage.uploads, blob_name)

        return Bucket()


class _FakeBigQuery:
    """Loads succeed, but the fact table comes back one row short."""

    def __init__(self):
        self.loads = []

    def load_table_from_uri(self, uri, destination, job_config):
        self.loads.append(destination)

        class Job:
            def result(self):
                return None

        return Job()

    def query(self, sql):
        short = "fact_order_items" in sql

        class Result:
            def result(self):
                return [{"n": 2 if short else 3}]

        return Result()


def test_a_row_count_mismatch_in_bigquery_fails_the_export(monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr(gcp, "_snapshot", lambda settings: nullcontext(None))
    monkeypatch.setattr(gcp, "write_ndjson", lambda connection, table, sql, path: path.touch() or 3)
    storage, bigquery = _FakeStorage(), _FakeBigQuery()

    with pytest.raises(DataQualityError) as error:
        gcp.export_to_gcp(_settings(), storage, bigquery, run_id="r1")

    assert [f.name for f in error.value.failures] == ["bigquery_rows_fact_order_items"]
    assert storage.uploads == [f"warehouse/run=r1/{t}.json" for t in gcp.EXPORTS]
    assert bigquery.loads == [f"{PROJECT}.{DATASET}.{t}" for t in gcp.EXPORTS]


# ---------------------------------------------------------- end-to-end test


def _settings(**overrides) -> Settings:
    values = dict(
        warehouse_dsn=os.environ.get(
            "TEST_WAREHOUSE_DSN", "postgresql://pipeline:pipeline@localhost:54324/warehouse_test"
        ),
        mongo_url=os.environ.get("TEST_MONGO_URL", "mongodb://localhost:27018"),
        mongo_database="raw_gcp_test",
        data_dir=REPO_ROOT / "data" / "raw",
        sql_dir=REPO_ROOT / "sql",
        gcp_project=PROJECT,
        gcs_bucket=BUCKET,
        bq_dataset=DATASET,
        schema_dir=SCHEMA_DIR,
    )
    values.update(overrides)
    return Settings(**values)


def _unreachable(reason):
    if os.environ.get("REQUIRE_GCP_EMULATORS"):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    """A small warehouse in PostgreSQL, an empty bucket and empty BigQuery tables."""
    os.environ.setdefault("STORAGE_EMULATOR_HOST", "http://localhost:4443")
    os.environ.setdefault("BIGQUERY_EMULATOR_HOST", "http://localhost:9050")

    data_dir = tmp_path_factory.mktemp("raw")
    (data_dir / "customers.csv").write_text(
        "customer_id,name,email,country,signup_date\n"
        "C1,Ada Lovelace,ada@example.com,FR,2025-01-10\n"
        "C2,Alan Turing,alan@example.com,MA,2025-02-11\n",
        encoding="utf-8",
    )
    (data_dir / "products.csv").write_text(
        "product_id,name,category,unit_cost,list_price\n"
        "P1,Notebook,Stationery,2.00,5.00\n"
        "P2,Keyboard,Electronics,30.00,80.00\n",
        encoding="utf-8",
    )
    settings = _settings(data_dir=data_dir)

    try:
        connection = psycopg2.connect(settings.warehouse_dsn, connect_timeout=3)
        mongo = MongoClient(settings.mongo_url, serverSelectionTimeoutMS=3000)
        mongo.admin.command("ping")
    except (psycopg2.OperationalError, PyMongoError) as error:
        _unreachable(f"databases not reachable: {error}")
    try:
        storage_client, bigquery_client = gcp._clients(settings)
        list(bigquery_client.list_datasets(timeout=3))
        list(storage_client.list_buckets(timeout=3))
    except Exception as error:  # noqa: BLE001 - any failure means "no emulator"
        _unreachable(f"GCP emulators not reachable: {error}")

    with connection, connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS etl, staging, warehouse, marts CASCADE")
    connection.close()

    raw = mongo[settings.mongo_database]["raw_orders"]
    raw.drop()
    ingested = datetime.now(timezone.utc) - timedelta(hours=1)
    raw.insert_many(
        [
            {"_id": "O1", "order_id": "O1", "customer_id": "C1", "status": "delivered",
             "channel": "web", "ordered_at": "2026-03-15T10:00:00Z", "ingested_at": ingested,
             "items": [{"product_id": "P1", "quantity": 2, "unit_price": 5.0},
                       {"product_id": "P2", "quantity": 1, "unit_price": 75.5}]},
            {"_id": "O2", "order_id": "O2", "customer_id": "C2", "status": "cancelled",
             "channel": "store", "ordered_at": "2026-03-20T10:00:00Z", "ingested_at": ingested,
             "items": [{"product_id": "P1", "quantity": 1, "unit_price": 5.0}]},
            {"_id": "O3", "order_id": "O3", "customer_id": "C2", "status": "delivered",
             "channel": "store", "ordered_at": "2026-04-02T10:00:00Z", "ingested_at": ingested,
             "items": [{"product_id": "P2", "quantity": 2, "unit_price": 80.0}]},
        ]
    )
    mongo.close()
    for stage in stages.STAGES.values():
        stage(settings)

    # In GCP these are created by Terraform (gcp/terraform/main.tf).
    from google.cloud import bigquery

    bucket = storage_client.bucket(BUCKET)
    if not bucket.exists():
        storage_client.create_bucket(BUCKET)
    bigquery_client.delete_dataset(DATASET, delete_contents=True, not_found_ok=True)
    bigquery_client.create_dataset(DATASET)
    for table in gcp.EXPORTS:
        definition = bigquery.Table(
            f"{PROJECT}.{DATASET}.{table}",
            schema=gcp._load_config(settings, table).schema,
        )
        if table == "fact_order_items":
            definition.time_partitioning = bigquery.TimePartitioning(field="order_date")
            definition.clustering_fields = ["customer_key", "product_key"]
        bigquery_client.create_table(definition)

    return settings, storage_client, bigquery_client


def _bq(client, sql):
    return [dict(row.items()) for row in client.query(sql).result()]


def test_export_copies_the_warehouse_to_bigquery(environment):
    settings, storage_client, bigquery_client = environment

    counts = gcp.export_to_gcp(settings, storage_client, bigquery_client, run_id="run1")

    assert counts == {"dim_date": counts["dim_date"], "dim_customer": 2, "dim_product": 2,
                      "fact_order_items": 4}
    facts = _bq(
        bigquery_client,
        f"SELECT order_id, order_date, revenue FROM `{PROJECT}.{DATASET}.fact_order_items` "
        "ORDER BY order_id, product_key",
    )
    assert facts == [
        {"order_id": "O1", "order_date": date(2026, 3, 15), "revenue": Decimal("10")},
        {"order_id": "O1", "order_date": date(2026, 3, 15), "revenue": Decimal("75.5")},
        {"order_id": "O2", "order_date": date(2026, 3, 20), "revenue": Decimal("5")},
        {"order_id": "O3", "order_date": date(2026, 4, 2), "revenue": Decimal("160")},
    ]
    files = sorted(blob.name for blob in storage_client.list_blobs(BUCKET, prefix="warehouse/run=run1/"))
    assert files == sorted(f"warehouse/run=run1/{table}.json" for table in gcp.EXPORTS)


def test_exporting_again_replaces_instead_of_appending(environment):
    settings, storage_client, bigquery_client = environment

    counts = gcp.export_to_gcp(settings, storage_client, bigquery_client, run_id="run2")

    for table, expected in counts.items():
        rows = _bq(bigquery_client, f"SELECT COUNT(*) AS n FROM `{PROJECT}.{DATASET}.{table}`")
        assert rows == [{"n": expected}], table


def test_bigquery_marts_give_the_same_figures_as_postgresql(environment):
    settings, _, bigquery_client = environment
    warehouse = f"{PROJECT}.{DATASET}"

    sql = string.Template((MARTS_DIR / "monthly_revenue.sql").read_text(encoding="utf-8"))
    in_bigquery = _bq(
        bigquery_client,
        "SELECT year_month, orders, revenue FROM ("
        + sql.substitute(warehouse=warehouse)
        + ") ORDER BY year_month",
    )
    with stages.warehouse(settings) as cursor:
        cursor.execute("SELECT year_month, orders, revenue FROM marts.monthly_revenue ORDER BY year_month")
        in_postgres = [
            {"year_month": month, "orders": orders, "revenue": revenue}
            for month, orders, revenue in cursor.fetchall()
        ]

    # The cancelled order O2 is excluded on both sides.
    assert in_postgres == [
        {"year_month": "2026-03", "orders": 1, "revenue": Decimal("85.50")},
        {"year_month": "2026-04", "orders": 1, "revenue": Decimal("160.00")},
    ]
    assert in_bigquery == in_postgres


MART_KEYS = {
    "monthly_revenue": "year_month",
    "product_performance": "product_id",
    "customer_segments": "customer_id",
    "sales_by_country_channel": "country, channel",
}


@pytest.mark.parametrize("mart", sorted(MART_KEYS))
def test_every_mart_is_identical_in_both_engines(environment, mart):
    settings, _, bigquery_client = environment
    sql = string.Template((MARTS_DIR / f"{mart}.sql").read_text(encoding="utf-8"))
    order = MART_KEYS[mart]

    in_bigquery = [
        tuple(row.values())
        for row in bigquery_client.query(
            f"SELECT * FROM ({sql.substitute(warehouse=f'{PROJECT}.{DATASET}')}) ORDER BY {order}"
        ).result()
    ]
    with stages.warehouse(settings) as cursor:
        cursor.execute(f"SELECT * FROM marts.{mart} ORDER BY {order}")
        in_postgres = cursor.fetchall()

    assert in_bigquery and in_bigquery == [tuple(row) for row in in_postgres]

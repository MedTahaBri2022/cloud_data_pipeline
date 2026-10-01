"""End-to-end test of the pipeline against real PostgreSQL and MongoDB.

    docker compose up -d postgres mongo
    pytest

Skipped automatically when the databases are not reachable, so the pure
unit tests still run anywhere.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg2
import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from pipeline import stages
from pipeline.config import Settings
from pipeline.quality import DataQualityError

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(timezone.utc)

CUSTOMERS_CSV = """customer_id,name,email,country,signup_date
C1,Ada Lovelace,ada@example.com,FR,2025-01-10
C2,Alan Turing,alan@example.com,MA,2025-02-11
C3,Broken,not-an-email,MA,2025-02-11
"""
PRODUCTS_CSV = """product_id,name,category,unit_cost,list_price
P1,Notebook,Stationery,2.00,5.00
P2,Keyboard,Electronics,30.00,80.00
"""


def order(order_id, customer="C1", status="delivered", items=None, minutes_ago=600):
    return {
        "_id": order_id,
        "order_id": order_id,
        "customer_id": customer,
        "status": status,
        "channel": "web",
        "ordered_at": "2026-03-15T10:00:00Z",
        "items": items or [{"product_id": "P1", "quantity": 2, "unit_price": 5.0}],
        "ingested_at": NOW - timedelta(minutes=minutes_ago),
    }


@pytest.fixture(scope="module")
def settings(tmp_path_factory) -> Settings:
    data_dir = tmp_path_factory.mktemp("raw")
    (data_dir / "customers.csv").write_text(CUSTOMERS_CSV, encoding="utf-8")
    (data_dir / "products.csv").write_text(PRODUCTS_CSV, encoding="utf-8")

    settings = Settings(
        warehouse_dsn=os.environ.get(
            "TEST_WAREHOUSE_DSN",
            "postgresql://pipeline:pipeline@localhost:54324/warehouse_test",
        ),
        mongo_url=os.environ.get("TEST_MONGO_URL", "mongodb://localhost:27018"),
        mongo_database="raw_test",
        data_dir=data_dir,
        sql_dir=REPO_ROOT / "sql",
    )

    try:
        connection = psycopg2.connect(settings.warehouse_dsn, connect_timeout=3)
        client = MongoClient(settings.mongo_url, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
    except (psycopg2.OperationalError, PyMongoError) as error:
        # CI sets REQUIRE_DATABASES so that a broken service is a failure there,
        # not a silently skipped test suite.
        if os.environ.get("REQUIRE_DATABASES"):
            pytest.fail(f"databases not reachable: {error}")
        pytest.skip(f"databases not reachable: {error}")

    with connection, connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS etl, staging, warehouse, marts CASCADE")
    connection.close()
    client[settings.mongo_database]["raw_orders"].drop()
    client.close()
    return settings


@pytest.fixture()
def raw_orders(settings):
    client = MongoClient(settings.mongo_url, tz_aware=True)
    yield client[settings.mongo_database]["raw_orders"]
    client.close()


def query(settings, sql, params=None):
    with stages.warehouse(settings) as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def run_all(settings):
    return {name: stage(settings) for name, stage in stages.STAGES.items()}


def test_first_run_loads_valid_data_and_reports_the_rest(settings, raw_orders):
    raw_orders.insert_many(
        [
            order(
                "O1",
                items=[
                    {"product_id": "P1", "quantity": 2, "unit_price": 5.0},
                    {"product_id": "P2", "quantity": 1, "unit_price": 75.5},
                ],
            ),
            order("O2", customer="C2", status="cancelled"),
            order("O3", customer="C404"),
            order(
                "O4",
                items=[{"product_id": "P1", "quantity": 0, "unit_price": 5.0}],
                minutes_ago=60,
            ),
        ]
    )

    counts = run_all(settings)

    assert counts["load_reference"] == {"customers": 2, "products": 2, "rejected": 1}
    assert counts["load_orders"] == {"read": 4, "loaded": 2, "rejected": 2}
    assert counts["build_warehouse"] == {"fact_rows": 3}

    facts = query(
        settings,
        """
        SELECT f.order_id, p.product_id, c.customer_id, f.date_key,
               f.revenue, f.cost, f.margin
          FROM warehouse.fact_order_items f
          JOIN warehouse.dim_product p USING (product_key)
          JOIN warehouse.dim_customer c USING (customer_key)
         ORDER BY f.order_id, p.product_id
        """,
    )
    assert facts == [
        ("O1", "P1", "C1", 20260315, Decimal("10.00"), Decimal("4.00"), Decimal("6.00")),
        ("O1", "P2", "C1", 20260315, Decimal("75.50"), Decimal("30.00"), Decimal("45.50")),
        ("O2", "P1", "C2", 20260315, Decimal("10.00"), Decimal("4.00"), Decimal("6.00")),
    ]

    rejected = dict(
        query(settings, "SELECT record_id, reasons FROM etl.rejected_records ORDER BY record_id")
    )
    assert rejected == {
        "C3": ["email is not valid"],
        "O3": ["unknown customer C404"],
        "O4": ["item 1: quantity must be a positive integer"],
    }

    # The cancelled order is in the warehouse but not in the sales marts.
    assert query(settings, "SELECT year_month, orders, revenue FROM marts.monthly_revenue") == [
        ("2026-03", 1, Decimal("85.50"))
    ]


def test_running_again_changes_nothing(settings):
    before = query(settings, "SELECT count(*), sum(revenue) FROM warehouse.fact_order_items")

    counts = run_all(settings)

    after = query(settings, "SELECT count(*), sum(revenue) FROM warehouse.fact_order_items")
    assert after == before
    assert query(settings, "SELECT count(*) FROM etl.rejected_records") == [(3,)]
    # Incremental: only the document inside the overlap window is read again
    # (O4, still invalid), not the three older ones.
    assert counts["load_orders"] == {"read": 1, "loaded": 0, "rejected": 1}


def test_updates_and_corrections_are_picked_up_incrementally(settings, raw_orders):
    # O2 is delivered after all, O3 is corrected, O1 loses a line, O5 is new.
    for doc in [
        order("O2", customer="C2", status="delivered", minutes_ago=0),
        order("O3", customer="C2", minutes_ago=0),
        order("O1", minutes_ago=0),
        order("O5", minutes_ago=0),
    ]:
        raw_orders.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    counts = run_all(settings)

    assert counts["load_orders"] == {"read": 5, "loaded": 4, "rejected": 1}
    assert counts["build_warehouse"] == {"fact_rows": 4}
    assert query(
        settings, "SELECT order_id, status FROM staging.orders ORDER BY order_id"
    ) == [("O1", "delivered"), ("O2", "delivered"), ("O3", "delivered"), ("O5", "delivered")]
    # O3 is no longer listed as rejected; O4 still is.
    assert query(
        settings,
        "SELECT record_id FROM etl.rejected_records WHERE source = 'orders'",
    ) == [("O4",)]
    assert query(settings, "SELECT orders, revenue FROM marts.monthly_revenue") == [
        (4, Decimal("40.00"))
    ]


def test_a_failed_check_stops_the_run_before_the_marts(settings):
    with stages.warehouse(settings) as cursor:
        cursor.execute(
            "UPDATE warehouse.fact_order_items SET revenue = revenue + 1 WHERE order_id = 'O5'"
        )

    with pytest.raises(DataQualityError) as raised:
        stages.check_quality(settings)

    failed = {failure.name for failure in raised.value.failures}
    assert failed == {"revenue_reconciles_with_staging", "margin_is_revenue_minus_cost"}
    # The failure is recorded for later inspection.
    assert query(
        settings,
        "SELECT count(*) FROM etl.quality_results WHERE NOT passed",
    ) == [(2,)]

    # Rebuilding from staging repairs the warehouse.
    stages.build_warehouse(settings)
    assert stages.check_quality(settings) == {"checks": 6}

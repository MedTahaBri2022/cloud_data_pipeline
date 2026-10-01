"""The pipeline stages: one function per task of the Airflow DAG.

Every stage is idempotent (upserts, watermark moved in the same transaction
as the data it covers), so a task can be retried or a whole run replayed
without creating duplicates.
"""

from __future__ import annotations

import csv
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator

import psycopg2
from psycopg2.extras import Json, execute_values
from pymongo import MongoClient

from .config import Settings
from .quality import DataQualityError, run_checks
from .validation import (
    Customer,
    Order,
    Product,
    Rejection,
    validate_customer,
    validate_order,
    validate_product,
)

log = logging.getLogger(__name__)

ORDERS_SOURCE = "mongo.raw_orders"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Documents are re-read from slightly before the watermark: a write that was
# in flight during the previous run (timestamped before the watermark but
# committed after the read) would otherwise be skipped forever. Re-reading is
# free of side effects because the load is an upsert.
WATERMARK_OVERLAP = timedelta(minutes=5)

MARTS = (
    "monthly_revenue",
    "product_performance",
    "customer_segments",
    "sales_by_country_channel",
)


@contextmanager
def warehouse(settings: Settings) -> Iterator[Any]:
    """One transaction: committed if the block succeeds, rolled back if not."""
    connection = psycopg2.connect(settings.warehouse_dsn)
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'UTC'")
            yield cursor
    finally:
        connection.close()


def init_schema(settings: Settings) -> None:
    with warehouse(settings) as cursor:
        for name in ("001_schema.sql", "003_marts.sql"):
            cursor.execute((settings.sql_dir / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- reference


def _read_csv(path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _split(results: Iterable[Any]) -> tuple[list[Any], list[Rejection]]:
    valid: list[Any] = []
    rejected: list[Rejection] = []
    for result in results:
        (rejected if isinstance(result, Rejection) else valid).append(result)
    return valid, rejected


def _record_rejections(cursor, source: str, accepted_ids, rejected: list[Rejection]) -> None:
    # A record that was rejected before and is valid now is no longer an issue.
    if accepted_ids:
        cursor.execute(
            "DELETE FROM etl.rejected_records WHERE source = %s AND record_id = ANY(%s)",
            (source, list(accepted_ids)),
        )
    # The same record may appear twice in a batch; keep its latest version.
    latest = {rejection.record_id: rejection for rejection in rejected}
    if latest:
        execute_values(
            cursor,
            """
            INSERT INTO etl.rejected_records (source, record_id, reasons, payload)
            VALUES %s
            ON CONFLICT ON CONSTRAINT uq_rejected_source_record DO UPDATE
               SET reasons = EXCLUDED.reasons,
                   payload = EXCLUDED.payload,
                   rejected_at = now()
            """,
            [(r.source, r.record_id, list(r.reasons), Json(r.payload)) for r in latest.values()],
        )


def load_reference(settings: Settings) -> dict[str, int]:
    """Customers and products arrive as CSV exports of the back office."""
    customers, bad_customers = _split(
        validate_customer(row) for row in _read_csv(settings.data_dir / "customers.csv")
    )
    products, bad_products = _split(
        validate_product(row) for row in _read_csv(settings.data_dir / "products.csv")
    )

    # Later rows win when an id is repeated in a file.
    customers_by_id: dict[str, Customer] = {c.customer_id: c for c in customers}
    products_by_id: dict[str, Product] = {p.product_id: p for p in products}

    with warehouse(settings) as cursor:
        execute_values(
            cursor,
            """
            INSERT INTO staging.customers (customer_id, name, email, country, signup_date)
            VALUES %s
            ON CONFLICT (customer_id) DO UPDATE
               SET name = EXCLUDED.name, email = EXCLUDED.email,
                   country = EXCLUDED.country, signup_date = EXCLUDED.signup_date,
                   loaded_at = now()
            """,
            [
                (c.customer_id, c.name, c.email, c.country, c.signup_date)
                for c in customers_by_id.values()
            ],
        )
        execute_values(
            cursor,
            """
            INSERT INTO staging.products (product_id, name, category, unit_cost, list_price)
            VALUES %s
            ON CONFLICT (product_id) DO UPDATE
               SET name = EXCLUDED.name, category = EXCLUDED.category,
                   unit_cost = EXCLUDED.unit_cost, list_price = EXCLUDED.list_price,
                   loaded_at = now()
            """,
            [
                (p.product_id, p.name, p.category, p.unit_cost, p.list_price)
                for p in products_by_id.values()
            ],
        )
        _record_rejections(cursor, "customers", customers_by_id.keys(), bad_customers)
        _record_rejections(cursor, "products", products_by_id.keys(), bad_products)

    counts = {
        "customers": len(customers_by_id),
        "products": len(products_by_id),
        "rejected": len(bad_customers) + len(bad_products),
    }
    log.info("reference data loaded: %s", counts)
    return counts


# ------------------------------------------------------------------- orders


def load_orders(settings: Settings) -> dict[str, int]:
    """Incremental load of the raw order documents stored in MongoDB."""
    with warehouse(settings) as cursor:
        cursor.execute("SELECT position FROM etl.watermarks WHERE source = %s", (ORDERS_SOURCE,))
        row = cursor.fetchone()
        watermark: datetime = row[0] if row else EPOCH

        cursor.execute("SELECT customer_id FROM staging.customers")
        known_customers = {r[0] for r in cursor.fetchall()}
        cursor.execute("SELECT product_id FROM staging.products")
        known_products = {r[0] for r in cursor.fetchall()}

        client = MongoClient(settings.mongo_url, tz_aware=True, serverSelectionTimeoutMS=10_000)
        try:
            documents = list(
                client[settings.mongo_database]["raw_orders"]
                .find({"ingested_at": {"$gt": watermark - WATERMARK_OVERLAP}})
                .sort("ingested_at", 1)
            )
        finally:
            client.close()

        if not documents:
            log.info("no new order since %s", watermark.isoformat())
            return {"read": 0, "loaded": 0, "rejected": 0}

        orders, rejected = _split(
            validate_order(doc, known_customers, known_products) for doc in documents
        )
        _upsert_orders(cursor, orders)
        _record_rejections(cursor, "orders", [o.order_id for o in orders], rejected)

        # Same transaction as the data: if anything above fails, the watermark
        # does not move and the next run picks the same documents again.
        new_watermark = max(doc["ingested_at"] for doc in documents)
        cursor.execute(
            """
            INSERT INTO etl.watermarks (source, position) VALUES (%s, %s)
            ON CONFLICT (source) DO UPDATE
               SET position = EXCLUDED.position, updated_at = now()
            """,
            (ORDERS_SOURCE, new_watermark),
        )

    counts = {"read": len(documents), "loaded": len(orders), "rejected": len(rejected)}
    log.info("orders loaded: %s", counts)
    return counts


def _upsert_orders(cursor, orders: list[Order]) -> None:
    if not orders:
        return
    execute_values(
        cursor,
        """
        INSERT INTO staging.orders (order_id, customer_id, status, channel, ordered_at)
        VALUES %s
        ON CONFLICT (order_id) DO UPDATE
           SET customer_id = EXCLUDED.customer_id, status = EXCLUDED.status,
               channel = EXCLUDED.channel, ordered_at = EXCLUDED.ordered_at,
               loaded_at = now()
        """,
        [(o.order_id, o.customer_id, o.status, o.channel, o.ordered_at) for o in orders],
    )
    # The newest version of an order replaces all of its lines.
    cursor.execute(
        "DELETE FROM staging.order_items WHERE order_id = ANY(%s)",
        ([o.order_id for o in orders],),
    )
    execute_values(
        cursor,
        "INSERT INTO staging.order_items (order_id, product_id, quantity, unit_price) VALUES %s",
        [
            (o.order_id, item.product_id, item.quantity, item.unit_price)
            for o in orders
            for item in o.items
        ],
    )


# -------------------------------------------------- warehouse and reporting


def build_warehouse(settings: Settings) -> dict[str, int]:
    with warehouse(settings) as cursor:
        cursor.execute((settings.sql_dir / "002_transform.sql").read_text(encoding="utf-8"))
        cursor.execute("SELECT count(*) FROM warehouse.fact_order_items")
        facts = cursor.fetchone()[0]
    log.info("warehouse built: %s fact rows", facts)
    return {"fact_rows": facts}


def check_quality(settings: Settings) -> dict[str, int]:
    with warehouse(settings) as cursor:
        results = run_checks(cursor)
    # Raised after the transaction committed, so failed checks are recorded too.
    failures = [result for result in results if not result.passed]
    if failures:
        raise DataQualityError(failures)
    log.info("%s data quality checks passed", len(results))
    return {"checks": len(results)}


def refresh_marts(settings: Settings) -> dict[str, int]:
    with warehouse(settings) as cursor:
        for mart in MARTS:
            cursor.execute(f"REFRESH MATERIALIZED VIEW marts.{mart}")
    log.info("%s marts refreshed", len(MARTS))
    return {"marts": len(MARTS)}


STAGES = {
    "init_schema": init_schema,
    "load_reference": load_reference,
    "load_orders": load_orders,
    "build_warehouse": build_warehouse,
    "check_quality": check_quality,
    "refresh_marts": refresh_marts,
}

"""Snapshot of the warehouse as CSV files, the data source of the Power BI report.

    WAREHOUSE_DSN=postgresql://pipeline:pipeline@localhost:54324/warehouse \
        python reporting/powerbi/export_data.py

The report imports these files, so it opens without a running database. Run
this again after the pipeline to refresh the snapshot, then Refresh in
Power BI Desktop. Customer e-mail addresses are left out.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import psycopg2

OUT = Path(__file__).resolve().parent / "data"

QUERIES = {
    "dim_date": """
        SELECT date_key, full_date, year, quarter, month, month_name,
               to_char(full_date, 'YYYY-MM') AS year_month, day_of_week, is_weekend
          FROM warehouse.dim_date ORDER BY date_key""",
    "dim_customer": """
        SELECT customer_key, customer_id, name, country, signup_date
          FROM warehouse.dim_customer ORDER BY customer_key""",
    "dim_product": """
        SELECT product_key, product_id, name, category, unit_cost, list_price
          FROM warehouse.dim_product ORDER BY product_key""",
    "fact_order_items": """
        SELECT order_id, product_key, customer_key, date_key, status, channel,
               quantity, unit_price, revenue, cost, margin
          FROM warehouse.fact_order_items ORDER BY order_id, product_key""",
    "customer_segments": """
        SELECT customer_id, recency_score, frequency_score, monetary_score, segment
          FROM marts.customer_segments ORDER BY customer_id""",
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    dsn = os.environ.get("WAREHOUSE_DSN", "postgresql://pipeline:pipeline@localhost:54324/warehouse")
    with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
        for name, sql in QUERIES.items():
            cursor.execute(sql)
            with open(OUT / f"{name}.csv", "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow([column.name for column in cursor.description])
                rows = cursor.fetchall()
                writer.writerows(rows)
            print(f"{name}: {len(rows)} rows")


if __name__ == "__main__":
    main()

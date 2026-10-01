"""Data quality checks run after each load.

Each check is a SQL query returning one row `(passed, observed)`. A failing
check fails the pipeline run before the reporting layer is refreshed, so a
dashboard never shows data that did not reconcile.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    sql: str


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    observed: str


class DataQualityError(Exception):
    def __init__(self, failures: list[CheckResult]):
        self.failures = failures
        summary = "; ".join(f"{f.name} ({f.observed})" for f in failures)
        super().__init__(f"{len(failures)} data quality check(s) failed: {summary}")


CHECKS: tuple[Check, ...] = (
    Check(
        "fact_rows_match_staging",
        """
        SELECT s.n = f.n, format('staging=%s fact=%s', s.n, f.n)
          FROM (SELECT count(*) AS n FROM staging.order_items) s,
               (SELECT count(*) AS n FROM warehouse.fact_order_items) f
        """,
    ),
    Check(
        "revenue_reconciles_with_staging",
        """
        SELECT s.total = f.total, format('staging=%s fact=%s', s.total, f.total)
          FROM (SELECT coalesce(sum(quantity * unit_price), 0) AS total
                  FROM staging.order_items) s,
               (SELECT coalesce(sum(revenue), 0) AS total
                  FROM warehouse.fact_order_items) f
        """,
    ),
    Check(
        "no_non_positive_quantity",
        """
        SELECT count(*) = 0, format('%s rows', count(*))
          FROM warehouse.fact_order_items
         WHERE quantity <= 0
        """,
    ),
    Check(
        "customer_business_keys_unique",
        """
        SELECT count(*) = count(DISTINCT customer_id),
               format('%s rows, %s distinct', count(*), count(DISTINCT customer_id))
          FROM warehouse.dim_customer
        """,
    ),
    Check(
        "every_fact_has_a_calendar_day",
        """
        SELECT count(*) = 0, format('%s orphan rows', count(*))
          FROM warehouse.fact_order_items f
          LEFT JOIN warehouse.dim_date d ON d.date_key = f.date_key
         WHERE d.date_key IS NULL
        """,
    ),
    Check(
        "margin_is_revenue_minus_cost",
        """
        SELECT count(*) = 0, format('%s rows', count(*))
          FROM warehouse.fact_order_items
         WHERE margin <> revenue - cost
        """,
    ),
)


def run_checks(cursor, checks: tuple[Check, ...] = CHECKS) -> list[CheckResult]:
    """Runs every check, records the outcome and returns the results."""
    results: list[CheckResult] = []
    for check in checks:
        cursor.execute(check.sql)
        passed, observed = cursor.fetchone()
        results.append(CheckResult(check.name, bool(passed), observed))

    cursor.executemany(
        "INSERT INTO etl.quality_results (check_name, passed, observed) VALUES (%s, %s, %s)",
        [(r.name, r.passed, r.observed) for r in results],
    )
    return results

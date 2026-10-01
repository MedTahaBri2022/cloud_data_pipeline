"""
### Retail pipeline

Hourly load of the retail sources into the warehouse.

| Task | What it does |
| --- | --- |
| `init_schema` | Creates schemas, tables and marts if they are missing |
| `load_reference` | Customers and products (CSV) to staging, invalid rows rejected |
| `load_orders` | New/changed order documents (MongoDB) to staging, incrementally |
| `build_warehouse` | Staging to star schema (dimensions, fact) |
| `check_quality` | Reconciliation and integrity checks; a failure stops the run |
| `refresh_marts` | Refreshes the reporting layer read by Power BI |

Every task is idempotent, so a failed run can simply be cleared and re-run.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.decorators import dag, task


def stage(name: str, **task_kwargs):
    """Wraps a pipeline stage in an Airflow task.

    The pipeline package is imported inside the task, not at module level:
    the scheduler parses this file every few seconds and must not pay for
    (or depend on) database drivers to do so.
    """

    @task(task_id=name, **task_kwargs)
    def run() -> dict | None:
        from pipeline.config import Settings
        from pipeline.stages import STAGES

        # The returned counts are stored as XCom and visible in the UI.
        return STAGES[name](Settings.from_env())

    return run()


@dag(
    dag_id="retail_pipeline",
    schedule="@hourly",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    # Loads are incremental (watermark), so past intervals need no backfill.
    catchup=False,
    # Two overlapping runs would race on the watermark.
    max_active_runs=1,
    default_args={
        "owner": "data-engineering",
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
        "retry_exponential_backoff": True,
        "execution_timeout": timedelta(minutes=20),
    },
    tags=["retail", "etl"],
    doc_md=__doc__,
)
def retail_pipeline():
    (
        stage("init_schema")
        >> stage("load_reference")
        >> stage("load_orders")
        >> stage("build_warehouse")
        # Bad data does not get better on retry: fail at once.
        >> stage("check_quality", retries=0)
        >> stage("refresh_marts")
    )


retail_pipeline()

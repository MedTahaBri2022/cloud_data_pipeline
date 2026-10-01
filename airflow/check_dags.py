"""DAG integrity check, run inside the Airflow image (locally and in CI).

Catches what unit tests of the pipeline cannot: a DAG file that does not
import, a cycle, or tasks wired in the wrong order.
"""

from __future__ import annotations

import sys

from airflow.models import DagBag

EXPECTED_ORDER = [
    "init_schema",
    "load_reference",
    "load_orders",
    "build_warehouse",
    "check_quality",
    "refresh_marts",
]


def main() -> int:
    bag = DagBag(dag_folder="/opt/airflow/dags", include_examples=False)
    if bag.import_errors:
        print(f"import errors: {bag.import_errors}")
        return 1

    dag = bag.get_dag("retail_pipeline")
    if dag is None:
        print("retail_pipeline was not found")
        return 1

    order = [task.task_id for task in dag.topological_sort()]
    if order != EXPECTED_ORDER:
        print(f"unexpected task order: {order}")
        return 1
    if dag.catchup or dag.max_active_runs != 1:
        print("the DAG must not catch up and must run one at a time")
        return 1
    if dag.get_task("check_quality").retries != 0:
        print("check_quality must not be retried")
        return 1

    print(f"retail_pipeline OK: {' -> '.join(order)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

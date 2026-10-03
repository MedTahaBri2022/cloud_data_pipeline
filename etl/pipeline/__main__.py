"""Command line entry point.

    python -m pipeline all              # the whole pipeline, in order
    python -m pipeline load_orders      # a single stage
    python -m pipeline export_to_gcp    # copy the warehouse to BigQuery (not part of "all")

Airflow calls the same stage functions; this entry point is what the
Kubernetes CronJob runs, and what a developer uses without Airflow.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Settings
from .quality import DataQualityError
from .stages import STAGES


def _export_to_gcp(settings: Settings) -> None:
    # Imported on demand: the other stages do not need the Google libraries.
    from .gcp import export_to_gcp

    export_to_gcp(settings)


OPTIONAL_STAGES = {"export_to_gcp": _export_to_gcp}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=["all", *STAGES, *OPTIONAL_STAGES], help="stage to run")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()

    try:
        for name in STAGES if args.stage == "all" else [args.stage]:
            {**STAGES, **OPTIONAL_STAGES}[name](settings)
    except DataQualityError as error:
        logging.error("%s", error)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Runtime configuration, read from the environment (12-factor)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    warehouse_dsn: str
    mongo_url: str
    mongo_database: str
    data_dir: Path
    sql_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            warehouse_dsn=os.environ.get(
                "WAREHOUSE_DSN",
                "postgresql://pipeline:pipeline@localhost:54324/warehouse",
            ),
            mongo_url=os.environ.get("MONGO_URL", "mongodb://localhost:27018"),
            mongo_database=os.environ.get("MONGO_DATABASE", "raw"),
            data_dir=Path(os.environ.get("DATA_DIR", _REPO_ROOT / "data" / "raw")),
            sql_dir=Path(os.environ.get("SQL_DIR", _REPO_ROOT / "sql")),
        )

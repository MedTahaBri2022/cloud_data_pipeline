"""Consistency check of the Power BI project, run in CI.

    python reporting/powerbi/check_project.py

Power BI Desktop is Windows-only, so CI cannot open the report. This script
checks what breaks a report silently when the model changes: every field a
visual uses (columns, measures, sort fields) must exist in the semantic
model, every page must be listed, and every column must have a source column
in the CSV snapshot.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = HERE / "RetailAnalytics.SemanticModel" / "definition"
REPORT = HERE / "RetailAnalytics.Report" / "definition"
DATA = HERE / "data"

COLUMN = re.compile(r"^\tcolumn (?:'([^']+)'|(\S+))")
MEASURE = re.compile(r"^\tmeasure (?:'([^']+)'|(\S+))")
SOURCE = re.compile(r"^\t\tsourceColumn: (.+)$")


def read_model() -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    columns: dict[str, set[str]] = {}
    measures: dict[str, set[str]] = {}
    sources: dict[str, set[str]] = {}
    for path in sorted((MODEL / "tables").glob("*.tmdl")):
        table = path.stem
        columns[table], measures[table], sources[table] = set(), set(), set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if match := COLUMN.match(line):
                columns[table].add(match.group(1) or match.group(2))
            elif match := MEASURE.match(line):
                measures[table].add(match.group(1) or match.group(2))
            elif match := SOURCE.match(line):
                sources[table].add(match.group(1).strip())
    return columns, measures, sources


def fields(node):
    """Every Column / Measure reference anywhere in a visual definition."""
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            if kind in node and isinstance(node[kind], dict) and "Property" in node[kind]:
                entity = node[kind]["Expression"]["SourceRef"]["Entity"]
                yield kind, entity, node[kind]["Property"]
        for value in node.values():
            yield from fields(value)
    elif isinstance(node, list):
        for value in node:
            yield from fields(value)


def main() -> int:
    errors: list[str] = []
    columns, measures, sources = read_model()

    for table, names in sources.items():
        csv_file = DATA / f"{table}.csv"
        if not csv_file.exists():
            errors.append(f"{table}: no snapshot file {csv_file.name}")
            continue
        with open(csv_file, newline="", encoding="utf-8") as handle:
            header = set(next(csv.reader(handle)))
        for name in sorted(names - header):
            errors.append(f"{table}: source column {name} is not in {csv_file.name}")

    pages = json.loads((REPORT / "pages" / "pages.json").read_text(encoding="utf-8"))
    folders = sorted(p.name for p in (REPORT / "pages").iterdir() if p.is_dir())
    if sorted(pages["pageOrder"]) != folders:
        errors.append(f"pages.json lists {pages['pageOrder']}, folders are {folders}")

    visuals = sorted((REPORT / "pages").glob("*/visuals/*/visual.json"))
    for path in visuals:
        visual = json.loads(path.read_text(encoding="utf-8"))
        where = f"{path.parent.parent.parent.name}/{path.parent.name}"
        for kind, entity, prop in fields(visual):
            known = columns if kind == "Column" else measures
            if prop not in known.get(entity, set()):
                errors.append(f"{where}: {kind.lower()} {entity}[{prop}] does not exist in the model")

    for error in errors:
        print("ERROR", error)
    print(f"{len(visuals)} visuals on {len(folders)} pages, "
          f"{sum(map(len, measures.values()))} measures, {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

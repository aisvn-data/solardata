"""Record and compare the contents of the Parquet tree.

Why this exists: ``data/processed/parquet/`` is committed, and ``python -m etl
all`` *overwrites* it.  So once CI runs the pipeline, the committed copy is
gone and there is nothing left to compare against.  CI therefore snapshots the
committed layout before building and checks it afterwards, which catches the
one failure mode ``etl verify`` cannot see on its own: the database is correct
but the published Parquet was never regenerated, or was generated from a
different build.

Usage::

    python scripts/parquet_manifest.py write  data/processed/parquet -o /tmp/before.json
    python scripts/parquet_manifest.py check data/processed/parquet --against /tmp/before.json

Both commands exit non-zero on a mismatch, so they work as CI steps without
extra shell logic.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def manifest(root: Path) -> dict:
    """Row count per partition, plus a total.

    Partitions are read from the Hive-style directory names rather than from
    pyarrow's ``partition_expression``, which is an ``Expression`` object and
    not a mapping; the paths are stable across pyarrow versions and are what a
    reader actually navigates by.

    Counts rather than checksums on purpose: byte-level checksums of zstd output
    are not stable across pyarrow versions, so a version bump would report drift
    that no reader would ever notice.
    """
    try:
        import pyarrow.parquet as pq
    except ImportError:  # pragma: no cover - depends on environment
        raise SystemExit(
            "pyarrow is required for this script: pip install -r requirements.txt"
        ) from None

    if not root.is_dir():
        return {"root": str(root), "partitions": {}, "total_rows": 0}

    partitions: dict[str, int] = {}
    for path in sorted(root.rglob("*.parquet")):
        directory = path.parent
        station = _value(directory, "station", "?")
        year = _value(directory, "year", "?")
        name = f"station={station}/year={year}"
        partitions[name] = partitions.get(name, 0) + pq.ParquetFile(path).metadata.num_rows

    return {
        "root": str(root),
        "partitions": dict(sorted(partitions.items())),
        "total_rows": sum(partitions.values()),
    }


def _value(directory: Path, key: str, default: str) -> str:
    """Value of a Hive-style ``key=value`` directory component."""
    name = directory.name
    if name.startswith(f"{key}="):
        return name[len(key) + 1 :]
    # Fall back to any ancestor, so a nested layout still resolves.
    for parent in directory.parents:
        if parent.name.startswith(f"{key}="):
            return parent.name[len(key) + 1 :]
        if parent == directory.parent.parent:
            break
    return default


def compare(before: dict, after: dict) -> list[str]:
    """Human-readable differences, empty when identical."""
    problems: list[str] = []
    a, b = before.get("partitions", {}), after.get("partitions", {})
    for name in sorted(set(a) | set(b)):
        was, now = a.get(name), b.get(name)
        if was != now:
            problems.append(f"{name}: committed {was} rows, rebuilt {now} rows")
    if not problems and before.get("total_rows") != after.get("total_rows"):
        problems.append(
            f"total rows: committed {before.get('total_rows')}, rebuilt {after.get('total_rows')}"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    write = sub.add_parser("write", help="record the current layout to a JSON file")
    write.add_argument("root")
    write.add_argument("-o", "--output", required=True)

    check = sub.add_parser("check", help="compare the current layout to a recording")
    check.add_argument("root")
    check.add_argument("--against", required=True)

    args = parser.parse_args(argv)

    if args.command == "write":
        data = manifest(Path(args.root))
        Path(args.output).write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(
            f"recorded {len(data['partitions'])} partitions, "
            f"{data['total_rows']:,} rows -> {args.output}"
        )
        return 0

    before = json.loads(Path(args.against).read_text(encoding="utf-8"))
    after = manifest(Path(args.root))
    if before.get("total_rows", 0) == 0:
        print("no committed parquet to compare against (first build)")
        return 0

    problems = compare(before, after)
    if problems:
        print("COMMITTED PARQUET IS STALE:")
        for problem in problems:
            print(f"  {problem}")
        print("\nRun `python -m etl all` and commit data/processed/parquet/.")
        return 1

    print(f"parquet matches the rebuild: {after['total_rows']:,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())

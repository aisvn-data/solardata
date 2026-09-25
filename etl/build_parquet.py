"""Export the readings table to Hive-partitioned Parquet.

Parquet is the interchange format: it is typed, so the ``NULL`` vs ``0``
distinction survives, it is columnar so the sparse wide ``readings`` table
compresses well, and ``pyarrow.dataset``/duckdb read it without a custom parser.

Layout::

    data/processed/parquet/station=aisvn/year=2020/part-0.parquet

Requires ``pyarrow``.  The stage is skipped with a clear message rather than
failing the build when it is missing, because SQLite alone is a complete store.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from etl.normalize.metrics import METRIC_BY_COLUMN

#: Which canonical columns are worth carrying into the interchange format.
#: Keeps the file narrow enough to stay small while covering the channels the
#: analysis actually uses.
EXPORTED_METRICS = (
    "solar_v",
    "solar2_v",
    "battery_v",
    "current_a",
    "current2_a",
    "power_w",
    "load_v",
    "temp_c",
    "lipo_v",
    "lipo2_v",
)
#: Columns carried into the interchange format, in order.  Kept free of any
#: pyarrow import so this module can be imported without pyarrow installed.
READING_COLUMNS: tuple[str, ...] = (
    "station_id",
    "ts_utc",
    "ts_local",
    *EXPORTED_METRICS,
    "boot_count",
    "quality_flags",
)


def _schema(pa):
    """Explicit Arrow schema for the exported columns.

    Without an explicit schema pyarrow infers the ``null`` type for a column
    that happens to be all-NULL in one partition -- phumy2 has no ``solar_v`` or
    ``battery_v`` at all -- and a Parquet file containing a null-typed column
    cannot be read back by a consumer.
    """
    fields = [
        ("station_id", pa.string()),
        ("ts_utc", pa.string()),
        ("ts_local", pa.string()),
        *(
            (name, pa.int64() if METRIC_BY_COLUMN[name].kind == "count" else pa.float64())
            for name in EXPORTED_METRICS
        ),
        ("boot_count", pa.int64()),
        ("quality_flags", pa.string()),
    ]
    return pa.schema(fields)


def _require_pyarrow():
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "pyarrow is required for the Parquet stage. Install it with "
            "`pip install -r requirements.txt`, or skip this stage with "
            "`python -m etl all --only db,export`."
        ) from exc
    return pq


def build(conn: sqlite3.Connection, target: Path, *, verbose: bool = True) -> int:
    """Write one Parquet file per (station, year).  Returns rows written."""
    pq = _require_pyarrow()
    import pyarrow as pa

    if target.exists():
        for path in sorted(target.rglob("*.parquet")):
            path.unlink()
    target.mkdir(parents=True, exist_ok=True)

    # Distinct partitions, cheapest possible query on the primary key.
    partitions = conn.execute(
        "SELECT DISTINCT station_id, substr(ts_utc, 1, 4) AS year FROM readings"
        " ORDER BY station_id, year"
    ).fetchall()

    total = 0
    for station_id, year in partitions:
        columns = ", ".join(READING_COLUMNS)
        rows = conn.execute(
            f"SELECT {columns} FROM readings"
            " WHERE station_id = ? AND substr(ts_utc, 1, 4) = ?"
            " ORDER BY ts_utc",
            (station_id, year),
        ).fetchall()
        if not rows:
            continue
        columns_out = {name: [row[name] for row in rows] for name in READING_COLUMNS}
        table = pa.Table.from_pydict(columns_out, schema=_schema(pa))
        directory = target / f"station={station_id}" / f"year={year}"
        directory.mkdir(parents=True, exist_ok=True)
        out = directory / "part-0.parquet"
        pq.write_table(
            table,
            out,
            compression="zstd",
            use_dictionary=True,
            # Row-group statistics are what make predicate pushdown work.
            row_group_size=50_000,
        )
        total += len(rows)
        if verbose:
            size_kb = out.stat().st_size / 1024
            print(f"  {station_id:<14} {year}  {len(rows):>7} rows  {size_kb:>8.1f} KiB")
    return total

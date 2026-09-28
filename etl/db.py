"""Connections, the generated DDL, and run bookkeeping.

Three of the database's tables are **generated** from ``etl.catalog`` rather
than written in ``schema.sql``:

``s_<station>`` (eight of them)
    One reading table per station, holding only the channels that station
    collects. This is the shape the project asked for and it is also the honest
    one: a channel is a fact about a site, so a table listing channels is a fact
    about a site, and there is no longer a 32-column grid in which 75% of every
    row is NULL and no reader can tell which NULLs mean "not connected" and
    which mean "not measured".

``readings_hourly`` and ``readings_daily``
    The union, over stations, of the published channels' statistics. A station
    leaves NULL every column it does not collect, and the CSVs the site fetches
    are cut with only that station's columns, so nothing a station does not
    measure ever reaches a browser.

Generating them means the catalog is the only place a column is named. 0.8 named
columns in ``schema.sql``, ``normalize/metrics.py``, ``rollup_schema.py`` and
``build_exports.py`` and the four had already drifted once.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from . import catalog

__all__ = [
    "ROLLUP_STATS",
    "SCHEMA_PATH",
    "connect",
    "create_generated_tables",
    "finish_run",
    "init_schema",
    "log_build",
    "now_iso",
    "rollup_columns",
    "rollup_ddl",
    "start_run",
    "station_ddl",
]

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: Statistic abbreviations, and what the report and the browser call them. Held
#: here because the DDL, the aggregate SQL and the CSV header all use them.
ROLLUP_STATS: dict[str, str] = {
    "avg": "mean",
    "min": "minimum",
    "max": "peak",
}


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """A connection with the pragmas this pipeline needs and nothing else.

    ``synchronous = OFF`` and a 64 MiB page cache are the reason the ingest of
    731,885 rows across 364 files is a minute rather than five. Neither costs
    anything here: the database is a build artefact, rebuilt from
    ``data/raw`` on demand, and a torn write costs one rebuild rather than data.
    """
    if read_only:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA cache_size = -64000")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    create_generated_tables(conn)
    conn.commit()


# ---------------------------------------------------------------------------
# Generated DDL
# ---------------------------------------------------------------------------


def _sql_type(kind: str, is_counter: bool) -> str:
    if kind == "text":
        return "TEXT"
    if is_counter:
        return "INTEGER"
    return "REAL"


def station_ddl(station: catalog.Station) -> str:
    """The CREATE TABLE for one station's readings.

    The primary key is ``ts_utc`` alone, because the table is already scoped to
    one station, and ``WITHOUT ROWID`` because the table *is* the index: a
    WITHOUT ROWID table with a TEXT primary key stores the rows in key order,
    which is both smaller than a rowid table plus an index and free to range-scan.
    That is most of the 98.9 MiB the readings cost in 0.8 going away.
    """
    lines = [f"    {ch.name:<16} {_sql_type(ch.kind, ch.counter)}," for ch in station.channels]
    body = "\n".join(lines)
    return f"""
CREATE TABLE IF NOT EXISTS {station.table} (
    ts_utc     TEXT NOT NULL PRIMARY KEY,
    ts_local   TEXT NOT NULL,
{body}
    flags        TEXT NOT NULL DEFAULT '',
    source_file_id INTEGER REFERENCES source_files(file_id),
    sheet_row    INTEGER
) WITHOUT ROWID
"""


def rollup_columns() -> tuple[tuple[str, str, str], ...]:
    """``(column, channel, stat)`` for every published statistic, in CSV order.

    The union over stations, so ``phumy2`` -- which has no ``power_w`` because
    the pin is not a measurement -- still finds the column in the table and
    leaves it NULL. Ordering is by station of first appearance, which is the
    registry order, and then by the catalog's own channel order, so the header is
    stable and readable rather than alphabetical.
    """
    seen: dict[str, tuple[str, str]] = {}
    order: list[str] = []
    for station in catalog.STATIONS:
        for ch in station.published:
            for stat in ch.stats:
                column = ch.stat_column(stat)
                if column not in seen:
                    seen[column] = (ch.name, stat)
                    order.append(column)
    return tuple((c, seen[c][0], seen[c][1]) for c in order)


def oor_columns() -> tuple[str, ...]:
    """Per-channel out-of-range counters, for the banded published channels only.

    A channel with no band gets no counter: it would only ever ship zeros, and a
    zero that means "not measured" is worse than no column at all.
    """
    seen: dict[str, None] = {}
    for station in catalog.STATIONS:
        for ch in station.published:
            if ch.band and ch.kind != "text":
                seen.setdefault(f"{ch.name}_n_oor", None)
    return tuple(seen)


def rollup_ddl(hourly: bool) -> str:
    """The CREATE TABLE for one rollup.

    Counters are INTEGER and everything else REAL, so ``boot_count_min`` does not
    become 1.9999999999999998 and ``AVG``/``MIN``/``MAX`` behave.

    ``boot_count`` needs no special case: it is an ordinary published channel
    whose ``stats`` are ``("min", "max")`` rather than the usual three, and a
    mean of an uptime counter across a reboot would average two boot sessions
    into a number that never happened.  ``millis_ms`` is the same.
    """
    kind_by_channel: dict[str, tuple[str, bool]] = {}
    for station in catalog.STATIONS:
        for ch in station.published:
            kind_by_channel.setdefault(ch.name, (ch.kind, ch.counter))

    values = "\n".join(
        f"    {column:<24} {'INTEGER' if kind_by_channel[channel][1] else 'REAL'},"
        for column, channel, _stat in rollup_columns()
    )
    oor = "\n".join(f"    {c:<24} INTEGER NOT NULL DEFAULT 0," for c in oor_columns())

    if hourly:
        head = "    station_id   TEXT NOT NULL,\n    ts_utc       TEXT NOT NULL,"
        tail = "    PRIMARY KEY (station_id, ts_utc)"
    else:
        head = (
            "    station_id   TEXT NOT NULL,\n    day          TEXT NOT NULL,\n"
            "    ts_utc_day   TEXT NOT NULL,\n    n_hours      INTEGER NOT NULL,"
        )
        tail = "    PRIMARY KEY (station_id, day)"

    name = "readings_hourly" if hourly else "readings_daily"
    return f"""
CREATE TABLE IF NOT EXISTS {name} (
{head}
    n_samples      INTEGER NOT NULL,
    n_out_of_range INTEGER NOT NULL DEFAULT 0,
{oor}
{values}
{tail}
) WITHOUT ROWID
"""


def create_generated_tables(conn: sqlite3.Connection) -> None:
    for station in catalog.STATIONS:
        conn.execute(station_ddl(station))
    conn.execute(rollup_ddl(hourly=True))
    conn.execute(rollup_ddl(hourly=False))


def write_catalog(conn: sqlite3.Connection) -> int:
    """Write the catalog into ``station_channels`` and ``station_layouts``.

    Verbatim from ``etl.catalog``, so the database carries its own
    documentation and a query never has to import the Python package to find out
    what a station collects. The report and ``public/data/stations.json`` are
    generated from these tables.
    """
    rows = []
    for station in catalog.STATIONS:
        for layout in station.layouts:
            conn.execute(
                "INSERT OR REPLACE INTO station_layouts"
                " (station_id, n_columns, header_json, channels, n_files, note)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    station.station_id,
                    layout.n_columns,
                    json.dumps(list(layout.header)),
                    json.dumps(list(layout.channels)),
                    layout.n_files,
                    layout.note,
                ),
            )
        # A channel's column index is the lowest index it appears at in any of
        # the station's layouts. It is documentation, not a key: the ingest
        # resolves the layout by width and then uses the layout's own order.
        index: dict[str, int] = {}
        for layout in station.layouts:
            for i, name in enumerate(layout.channels, start=1):
                index.setdefault(name, i)
        for ch in station.channels:
            rows.append(
                (
                    station.station_id,
                    ch.name,
                    index.get(ch.name),
                    ch.label,
                    ch.kind,
                    ch.description,
                    ch.unit,
                    ch.raw_unit,
                    ch.scale,
                    ch.band_lo,
                    ch.band_hi,
                    ch.band_note,
                    ",".join(ch.stats),
                    1 if ch.publish else 0,
                    ch.exclude,
                    ch.exclude_note,
                    1 if ch.counter else 0,
                    ch.decimals,
                )
            )
    conn.executemany(
        "INSERT OR REPLACE INTO station_channels"
        " (station_id, channel, col_index, label, kind, description, unit, raw_unit,"
        "  scale, band_lo, band_hi, band_note, stats, published, exclude_reason,"
        "  exclude_note, is_counter, decimals)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


# ---------------------------------------------------------------------------
# Run bookkeeping
# ---------------------------------------------------------------------------


def start_run(conn: sqlite3.Connection, raw_dir: str, tool_version: str) -> int:
    cursor = conn.execute(
        "INSERT INTO ingest_runs (started_at, tool_version, raw_dir) VALUES (?, ?, ?)",
        (now_iso(), tool_version, str(raw_dir)),
    )
    return int(cursor.lastrowid or 0)


def finish_run(conn: sqlite3.Connection, run_id: int, notes: str = "") -> None:
    conn.execute(
        "UPDATE ingest_runs SET finished_at = ?, notes = ? WHERE run_id = ?",
        (now_iso(), notes, run_id),
    )
    conn.commit()


def log_build(
    conn: sqlite3.Connection,
    run_id: int | None,
    artefact: str,
    target: str,
    rows: int,
    size: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO build_log (run_id, artefact, target, rows_written, bytes_written, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, artefact, target, rows, size, now_iso()),
    )
    conn.commit()

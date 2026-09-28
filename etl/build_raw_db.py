"""Build the ultra-compact solardata_raw.db store.

Ingests raw cells directly from data/raw into integer-typed tables
with Unix epoch seconds timestamps (ts INTEGER PRIMARY KEY), reducing storage
from >100 MB down to ~22 MB for full-resolution browser usage.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from . import catalog
from .config import Settings, is_excel_lock_file
from .readers import times, xlsx


@dataclass
class RawSummary:
    total_rows: int = 0
    per_station: dict[str, int] = field(default_factory=dict)
    size_bytes: int = 0
    failed: int = 0

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    def line(self) -> str:
        return f"{self.total_rows:,} raw readings across {len(self.per_station)} stations ({self.size_mb:.2f} MiB)"


def raw_table_name(station_id: str) -> str:
    """The raw table name for a station: 'r_' prefix."""
    return "r_" + station_id.replace("-", "_")


def build_raw_db(settings: Settings, *, verbose: bool = True) -> RawSummary:
    """Build solardata_raw.db from scratch from settings.raw_sources()."""
    settings.ensure_dirs()

    # Remove previous database and sidecars
    for p in (
        settings.raw_db_path,
        Path(f"{settings.raw_db_path}-wal"),
        Path(f"{settings.raw_db_path}-shm"),
    ):
        p.unlink(missing_ok=True)

    conn = sqlite3.connect(settings.raw_db_path)
    summary = RawSummary()

    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")

        conn.execute(
            """
            CREATE TABLE raw_metadata (
                station_id TEXT PRIMARY KEY,
                table_name TEXT NOT NULL,
                n_rows INTEGER NOT NULL,
                min_ts INTEGER,
                max_ts INTEGER,
                sha256 TEXT NOT NULL
            )
            """
        )

        sources = settings.raw_sources()
        for source in sources:
            station = catalog.station_for_source(source.name)
            if station is None:
                continue

            paths = (
                [source]
                if source.is_file()
                else sorted(source.glob("*.xlsx"), key=lambda p: p.name.lower())
            )
            # Filter out Excel locks
            paths = [p for p in paths if not is_excel_lock_file(p.name)]
            if not paths:
                continue

            table = raw_table_name(station.station_id)
            # Find the primary layout
            layout = station.layouts[0]
            cols = list(layout.channels)
            col_defs = ", ".join(f"{c} INTEGER" for c in cols)

            conn.execute(f"CREATE TABLE {table} (ts INTEGER PRIMARY KEY, {col_defs}) WITHOUT ROWID")

            tz = ZoneInfo(station.tz)
            station_rows = 0
            min_ts: int | None = None
            max_ts: int | None = None
            last_digest = ""

            placeholders = ", ".join(["?"] * (len(cols) + 1))
            insert_sql = (
                f"INSERT OR IGNORE INTO {table} (ts, {', '.join(cols)}) VALUES ({placeholders})"
            )

            for path in paths:
                last_digest = xlsx.file_digest(path)
                block = xlsx.detect_block(path)
                batch: list[list[object]] = []

                for _sheet_row, cells in xlsx.iter_cells(path, block):
                    if not cells or not times.looks_like_timestamp(cells[0]):
                        continue
                    try:
                        local_dt = times.parse_local(cells[0])
                        utc_dt = times.to_utc(local_dt, tz)
                        ts = int(utc_dt.timestamp())
                    except Exception:
                        continue

                    if min_ts is None or ts < min_ts:
                        min_ts = ts
                    if max_ts is None or ts > max_ts:
                        max_ts = ts

                    row_vals: list[object] = [ts]
                    for v in cells[1:]:
                        if v is None or v == "" or v == -992:
                            row_vals.append(None)
                        else:
                            try:
                                f = float(v)
                                row_vals.append(int(f) if f.is_integer() else f)
                            except (ValueError, TypeError):
                                row_vals.append(None)

                    while len(row_vals) < len(cols) + 1:
                        row_vals.append(None)
                    batch.append(row_vals[: len(cols) + 1])

                if batch:
                    conn.executemany(insert_sql, batch)
                    conn.commit()
                    station_rows += len(batch)

            conn.execute(
                "INSERT INTO raw_metadata (station_id, table_name, n_rows, min_ts, max_ts, sha256) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (station.station_id, table, station_rows, min_ts, max_ts, last_digest),
            )
            conn.commit()

            summary.per_station[station.station_id] = station_rows
            summary.total_rows += station_rows
            if verbose:
                print(f"  {station.station_id:<16} {station_rows:>7,} readings -> {table}")

        conn.execute("VACUUM")
    finally:
        conn.close()

    summary.size_bytes = settings.raw_db_path.stat().st_size
    return summary

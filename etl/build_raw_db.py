"""Build the ultra-compact solardata_raw.db store.

Ingests raw cells directly from data/raw into integer-typed tables
with Unix epoch seconds timestamps (ts INTEGER PRIMARY KEY), reducing storage
from >100 MB down to ~22 MB for full-resolution browser usage.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import catalog
from .config import SENTINELS, Settings, is_excel_lock_file
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


def normalization_digest() -> str:
    """SHA-256 digest of data/config/normalization.json."""
    path = Path(__file__).resolve().parent.parent / "data" / "config" / "normalization.json"
    if not path.exists():
        return ""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def is_raw_cache_valid(settings: Settings) -> bool:
    """Return True if solardata_raw.db exists and matches current raw files & normalization rules."""
    if not settings.raw_db_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{settings.raw_db_path}?mode=ro", uri=True)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if not {
                "raw_manifest",
                "raw_config_manifest",
                "raw_metadata",
                "raw_source_files",
                "raw_rejects",
            }.issubset(tables):
                return False

            cur_norm_hash = normalization_digest()
            stored = conn.execute(
                "SELECT val FROM raw_config_manifest WHERE key = 'normalization_sha256'"
            ).fetchone()
            if not stored or stored[0] != cur_norm_hash:
                return False

            manifest_rows = conn.execute(
                "SELECT rel_path, size_bytes, mtime, sha256 FROM raw_manifest"
            ).fetchall()
            manifest_map = {r[0]: (r[1], r[2], r[3]) for r in manifest_rows}

            sources = settings.raw_sources()
            actual_files: list[Path] = []
            for src in sources:
                paths = (
                    [src]
                    if src.is_file()
                    else sorted(src.glob("*.xlsx"), key=lambda p: p.name.lower())
                )
                paths = [p for p in paths if not is_excel_lock_file(p.name)]
                actual_files.extend(paths)

            if len(actual_files) != len(manifest_map):
                return False

            for path in actual_files:
                try:
                    rel = str(path.relative_to(settings.raw_dir)).replace("\\", "/")
                except ValueError:
                    rel = path.name
                if rel not in manifest_map:
                    return False
                stat = path.stat()
                stored_size, stored_mtime, stored_hash = manifest_map[rel]
                if stat.st_size != stored_size:
                    return False
                if stat.st_mtime != stored_mtime and xlsx.file_digest(path) != stored_hash:
                    return False
            return True
        finally:
            conn.close()
    except Exception:
        return False


def load_cached_raw_summary(settings: Settings) -> RawSummary:
    """Reconstruct RawSummary from cached raw_metadata table."""
    conn = sqlite3.connect(f"file:{settings.raw_db_path}?mode=ro", uri=True)
    try:
        summary = RawSummary()
        for row in conn.execute(
            "SELECT station_id, n_rows FROM raw_metadata ORDER BY station_id"
        ).fetchall():
            summary.per_station[row[0]] = row[1]
            summary.total_rows += row[1]
        summary.size_bytes = settings.raw_db_path.stat().st_size
        return summary
    finally:
        conn.close()


def build_raw_db(settings: Settings, *, verbose: bool = True, force: bool = False) -> RawSummary:
    """Build solardata_raw.db from scratch from settings.raw_sources(), cached if unchanged."""
    settings.ensure_dirs()

    if not force and is_raw_cache_valid(settings):
        summary = load_cached_raw_summary(settings)
        if verbose:
            print("  (cached - no raw files or normalization rules changed)")
            for station_id, n_rows in sorted(summary.per_station.items()):
                print(f"  {station_id:<16} {n_rows:>7,} readings -> {raw_table_name(station_id)}")
        return summary

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
        conn.execute(
            """
            CREATE TABLE raw_manifest (
                rel_path TEXT PRIMARY KEY,
                size_bytes INTEGER NOT NULL,
                mtime REAL NOT NULL,
                sha256 TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE raw_config_manifest (
                key TEXT PRIMARY KEY,
                val TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE raw_source_files (
                file_id INTEGER PRIMARY KEY,
                source_dir TEXT NOT NULL,
                station_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                rel_path TEXT NOT NULL UNIQUE,
                sha256 TEXT,
                bytes INTEGER,
                has_header INTEGER NOT NULL,
                header_json TEXT,
                n_columns INTEGER NOT NULL,
                layout_channels TEXT,
                n_rows INTEGER NOT NULL,
                n_ingested INTEGER NOT NULL DEFAULT 0,
                n_rejected INTEGER NOT NULL DEFAULT 0,
                n_duplicate_ts INTEGER NOT NULL DEFAULT 0,
                n_notes INTEGER NOT NULL DEFAULT 0,
                extra_blocks INTEGER NOT NULL DEFAULT 0,
                excluded TEXT,
                min_ts_utc TEXT,
                max_ts_utc TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE raw_rejects (
                reject_id INTEGER PRIMARY KEY,
                file_id INTEGER,
                station_id TEXT,
                sheet_row INTEGER,
                column_name TEXT,
                raw_value TEXT,
                reason TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE raw_notes (
                note_id INTEGER PRIMARY KEY,
                file_id INTEGER,
                station_id TEXT,
                ts_utc TEXT,
                column_name TEXT,
                note TEXT NOT NULL
            )
            """
        )

        file_id_counter = 0
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
            col_defs = ", ".join(
                f"{ch.name} {'TEXT' if ch.kind == 'text' else 'INTEGER'}" for ch in station.channels
            )

            conn.execute(
                f"CREATE TABLE {table} (ts INTEGER PRIMARY KEY, {col_defs}, flags TEXT NOT NULL DEFAULT '') WITHOUT ROWID"
            )

            tz = ZoneInfo(station.tz)
            station_rows = 0
            min_ts: int | None = None
            max_ts: int | None = None
            last_digest = ""

            for path in paths:
                file_id_counter += 1
                file_id = file_id_counter
                file_rejects: list[tuple] = []
                last_digest = xlsx.file_digest(path)
                block = xlsx.detect_block(path)
                layout = station.layout_for(block.n_columns)
                if layout is None:
                    continue
                file_cols = list(layout.channels)
                insert_cols = [*file_cols, "flags"]
                placeholders = ", ".join(["?"] * (len(insert_cols) + 1))
                insert_sql = f"INSERT OR IGNORE INTO {table} (ts, {', '.join(insert_cols)}) VALUES ({placeholders})"
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
                    row_flags = ""
                    for idx, ch_name in enumerate(file_cols):
                        ch = station.by_name[ch_name]
                        v = cells[idx + 1] if idx + 1 < len(cells) else None
                        if v is None or v == "":
                            row_vals.append(None)
                        elif ch.kind == "text":
                            s = str(v).strip()
                            row_vals.append(s if s else None)
                        else:
                            try:
                                f = float(v)
                                if f != f or f in (float("inf"), float("-inf")) or f in SENTINELS:
                                    row_vals.append(None)
                                    row_flags = "sentinel"
                                    file_rejects.append(
                                        (
                                            file_id,
                                            station.station_id,
                                            _sheet_row,
                                            ch_name,
                                            str(v),
                                            "sentinel",
                                        )
                                    )
                                else:
                                    row_vals.append(int(f) if f.is_integer() else f)
                            except (ValueError, TypeError):
                                row_vals.append(None)

                    row_vals.append(row_flags)
                    batch.append(row_vals)

                if batch:
                    conn.executemany(insert_sql, batch)
                    conn.commit()
                    station_rows += len(batch)

                if file_rejects:
                    conn.executemany(
                        "INSERT INTO raw_rejects (file_id, station_id, sheet_row, column_name, raw_value, reason) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        file_rejects,
                    )
                    conn.commit()

                try:
                    rel = str(path.relative_to(settings.raw_dir)).replace("\\", "/")
                except ValueError:
                    rel = path.name
                stat = path.stat()
                conn.execute(
                    "INSERT OR REPLACE INTO raw_manifest (rel_path, size_bytes, mtime, sha256) "
                    "VALUES (?, ?, ?, ?)",
                    (rel, stat.st_size, stat.st_mtime, last_digest),
                )
                min_utc_str = (
                    times.iso_utc(datetime.fromtimestamp(min_ts, tz=UTC)) if min_ts else None
                )
                max_utc_str = (
                    times.iso_utc(datetime.fromtimestamp(max_ts, tz=UTC)) if max_ts else None
                )
                conn.execute(
                    "INSERT INTO raw_source_files (file_id, source_dir, station_id, filename, rel_path, "
                    "sha256, bytes, has_header, header_json, n_columns, layout_channels, n_rows, "
                    "n_ingested, n_rejected, n_duplicate_ts, n_notes, extra_blocks, excluded, min_ts_utc, max_ts_utc) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        file_id,
                        rel.split("/")[0] if "/" in rel else path.stem,
                        station.station_id,
                        path.name,
                        rel,
                        last_digest,
                        stat.st_size,
                        1 if block.has_header else 0,
                        json.dumps(list(layout.header)) if layout.header else None,
                        block.n_columns,
                        json.dumps(list(layout.channels)),
                        block.n_rows,
                        len(batch),
                        len(file_rejects),
                        0,
                        0,
                        block.extra_blocks,
                        None,
                        min_utc_str,
                        max_utc_str,
                    ),
                )
                conn.commit()

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

        conn.execute(
            "INSERT OR REPLACE INTO raw_config_manifest (key, val) VALUES (?, ?)",
            ("normalization_sha256", normalization_digest()),
        )
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()

    summary.size_bytes = settings.raw_db_path.stat().st_size
    return summary

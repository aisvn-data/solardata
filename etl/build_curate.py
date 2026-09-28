"""Stage 2 Curation: solardata_raw.db -> solardata.db.

Reads the compact normalized readings from solardata_raw.db,
applies confirmed physical scales, dated hardware corrections,
and analytical plausibility bands from data/config/curation.json,
and generates the station tables and rejects in solardata.db.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__, catalog, db
from .build_db import (
    RunSummary,
    _columns_covering,
    _merge,
    _register_stations,
    _update_coverage,
)
from .build_raw_db import build_raw_db, raw_table_name
from .config import (
    BAD_WINDOWS,
    FLAG_BAD_WINDOW,
    FLAG_NO_SIGNAL,
    FLAG_OUT_OF_RANGE,
    NULL_WINDOWS,
    REASON_NULL_WINDOW,
    Settings,
)
from .readers import times


def curate(settings: Settings, *, verbose: bool = True) -> RunSummary:
    """Build solardata.db directly from solardata_raw.db."""
    settings.ensure_dirs()
    t0 = time.perf_counter()

    # 1. Guarantee solardata_raw.db is built and up to date (cached if unchanged)
    build_raw_db(settings, verbose=verbose, force=False)

    # 2. Reset solardata.db
    for path in (
        settings.db_path,
        Path(f"{settings.db_path}-wal"),
        Path(f"{settings.db_path}-shm"),
    ):
        path.unlink(missing_ok=True)

    conn = db.connect(settings.db_path)
    raw_conn = sqlite3.connect(f"file:{settings.raw_db_path}?mode=ro", uri=True)
    raw_conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA synchronous = NORMAL")
        db.init_schema(conn)
        _register_stations(conn)
        db.write_catalog(conn)
        run_id = db.start_run(conn, str(settings.raw_dir), __version__)
        conn.commit()

        summary = RunSummary(run_id=run_id)

        # 3. Copy source_files from solardata_raw.db
        files_rows = raw_conn.execute("SELECT * FROM raw_source_files ORDER BY file_id").fetchall()
        for f in files_rows:
            conn.execute(
                "INSERT INTO source_files (file_id, run_id, source_dir, station_id, filename, rel_path, "
                "sha256, bytes, has_header, header_json, n_columns, layout_channels, n_rows, "
                "n_ingested, n_rejected, n_duplicate_ts, n_notes, extra_blocks, excluded, min_ts_utc, max_ts_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f["file_id"],
                    run_id,
                    f["source_dir"],
                    f["station_id"],
                    f["filename"],
                    f["rel_path"],
                    f["sha256"],
                    f["bytes"],
                    f["has_header"],
                    f["header_json"],
                    f["n_columns"],
                    f["layout_channels"],
                    f["n_rows"],
                    f["n_ingested"],
                    f["n_rejected"],
                    f["n_duplicate_ts"],
                    f["n_notes"],
                    f["extra_blocks"],
                    f["excluded"],
                    f["min_ts_utc"],
                    f["max_ts_utc"],
                ),
            )
            summary.files += 1

        # 4. Copy raw_rejects (sentinels)
        raw_rejects = raw_conn.execute(
            "SELECT file_id, station_id, sheet_row, column_name, raw_value, reason FROM raw_rejects"
        ).fetchall()
        if raw_rejects:
            conn.executemany(
                "INSERT INTO rejects (run_id, file_id, station_id, sheet_row, column_name, raw_value, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(run_id, *r) for r in raw_rejects],
            )
            summary.rows_rejected += len(raw_rejects)
        conn.commit()

        # 5. Process readings for each station from r_<station>
        for station in catalog.STATIONS:
            raw_table = raw_table_name(station.station_id)
            channels = list(station.channels)
            col_names = [ch.name for ch in channels]
            table = station.table

            columns = ("ts_utc", "ts_local", *col_names, "flags", "source_file_id", "sheet_row")
            placeholders = ", ".join("?" * len(columns))
            insert_sql = (
                f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
            )

            # Get file_id for this station
            stn_file = raw_conn.execute(
                "SELECT file_id FROM raw_source_files WHERE station_id = ? LIMIT 1",
                (station.station_id,),
            ).fetchone()
            file_id = stn_file[0] if stn_file else 1

            tzinfo = ZoneInfo(station.tz)
            cursor = raw_conn.execute(
                f"SELECT ts, {', '.join(col_names)}, flags FROM {raw_table} ORDER BY ts"
            )

            batch: list[list[object]] = []
            null_window_rejects: list[tuple] = []
            station_ingested = 0
            sheet_row = 1

            for row in cursor:
                ts = row[0]
                raw_flags = row[-1]
                sheet_row += 1
                dt_utc = datetime.fromtimestamp(ts, tz=UTC)
                ts_utc = times.iso_utc(dt_utc)
                dt_local = dt_utc.astimezone(tzinfo)
                ts_local = times.iso_local(dt_local)

                row_vals: list[object] = [ts_utc, ts_local]
                cell_flags: list[tuple[str, ...]] = [(raw_flags,)] if raw_flags else []

                for idx, ch in enumerate(channels, start=1):
                    raw_v = row[idx]
                    if raw_v is None:
                        row_vals.append(None)
                    elif ch.kind == "text":
                        row_vals.append(str(raw_v))
                    else:
                        val = round(raw_v * ch.scale, 9) if ch.scale != 1.0 else raw_v
                        val = ch.correct(val, ts_utc)
                        row_vals.append(val)
                        if not ch.in_band(val):
                            cell_flags.append((FLAG_OUT_OF_RANGE,))

                # Check NULL_WINDOWS
                window_flags: list[tuple[str, ...]] = []
                for null_cols in _columns_covering(NULL_WINDOWS, station.station_id, ts_utc):
                    for idx, ch in enumerate(channels, start=2):
                        if ch.name in null_cols and row_vals[idx] is not None:
                            val_before = row_vals[idx]
                            row_vals[idx] = None
                            window_flags.append((f"{FLAG_NO_SIGNAL}:{ch.name}",))
                            null_window_rejects.append(
                                (
                                    run_id,
                                    file_id,
                                    station.station_id,
                                    sheet_row,
                                    ch.name,
                                    str(val_before),
                                    REASON_NULL_WINDOW,
                                )
                            )

                # Check BAD_WINDOWS
                bad_flags: list[tuple[str, ...]] = []
                for bad_cols in _columns_covering(BAD_WINDOWS, station.station_id, ts_utc):
                    for idx, ch in enumerate(channels, start=2):
                        if ch.name in bad_cols and row_vals[idx] is not None:
                            bad_flags.append((f"{FLAG_BAD_WINDOW}:{ch.name}",))

                flags_str = _merge(*cell_flags, *window_flags, *bad_flags)
                row_vals.append(flags_str)
                row_vals.append(file_id)
                row_vals.append(sheet_row)

                batch.append(row_vals)
                station_ingested += 1

                if len(batch) >= 10000:
                    conn.executemany(insert_sql, batch)
                    batch.clear()

            if batch:
                conn.executemany(insert_sql, batch)
                batch.clear()

            if null_window_rejects:
                conn.executemany(
                    "INSERT INTO rejects (run_id, file_id, station_id, sheet_row, column_name, raw_value, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    null_window_rejects,
                )
                summary.rows_rejected += len(null_window_rejects)

            # Update source_files rejected count
            conn.execute(
                "UPDATE source_files SET n_rejected = n_rejected + ? WHERE file_id = ?",
                (len(null_window_rejects), file_id),
            )
            conn.commit()

            summary.rows_ingested += station_ingested
            if verbose:
                print(f"  {station.station_id:<16} {station_ingested:>7,} curated -> {table}")

        _update_coverage(conn, summary)
        conn.commit()

        notes = (
            f"{summary.rows_ingested} readings from {summary.files} files across "
            f"{len(summary.per_station)} stations"
        )
        db.finish_run(conn, run_id, notes)
        db.log_build(conn, run_id, "db", str(settings.db_path), summary.rows_ingested)

        elapsed = time.perf_counter() - t0
        if verbose:
            print(f"  curation completed in {elapsed:.2f}s ({summary.rows_ingested:,} readings)")
        return summary
    finally:
        raw_conn.close()
        conn.close()

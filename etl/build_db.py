"""Ingest ``data/raw`` into the canonical SQLite store.

Per file, in order:
  1. detect the primary column block and whether row 1 is a header
  2. resolve the station from the folder name
  3. map headers to canonical columns (or admit we do not know, per column)
  4. read every body row, parse the timestamp, coerce the values
  5. insert, letting the ``(station_id, ts_utc)`` primary key absorb chunk-boundary
     duplicates and recording how many were absorbed

Everything the ingest refuses to store lands in ``rejects`` or ``notes``.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from etl import __version__, stations
from etl.config import (
    BAD_WINDOWS,
    FLAG_DUPLICATE_TS,
    FLAG_MISALIGNED,
    NULL_WINDOWS,
    ROW_EXCLUSIONS,
    Settings,
)
from etl.db import connect, finish_run, init_schema, log_build, start_run
from etl.normalize.metrics import (
    CANONICAL_COLUMNS,
    METRIC_BY_COLUMN,
    build_row_mapping,
)
from etl.normalize.quality import (
    coerce_cell,
    looks_like_note,
    merge_flags,
)
from etl.readers.times import (
    TimestampError,
    iso_local,
    iso_utc,
    looks_like_header,
    looks_like_timestamp,
    parse_local,
    to_utc,
)
from etl.readers.xlsx import detect_block, file_digest, iter_all_cells, iter_cells

_INSERT_COLUMNS = (
    "station_id",
    "ts_utc",
    "ts_local",
    "tz",
    *CANONICAL_COLUMNS,
    "quality_flags",
    "source_file_id",
    "sheet_row",
)

#: ``INSERT OR IGNORE`` against the ``(station_id, ts_utc)`` primary key is what
#: absorbs duplicates, which the schema documents as the first destination for
#: every raw cell.  ``rowcount == 0`` therefore means "this exact instant was
#: already recorded for this station", not "something went wrong" -- so the
#: caller must not treat it as an error.
_INSERT_SQL = (
    f"INSERT OR IGNORE INTO readings ({', '.join(_INSERT_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_INSERT_COLUMNS))})"
)


def _column_letter(index: int) -> str:
    """0-based column index to spreadsheet letter, for citing a note's location."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _row_timestamp(cells: list[str], tzinfo) -> str | None:
    """UTC timestamp of a row we already accepted, used to anchor a note."""
    if not cells or not cells[0]:
        return None
    try:
        return iso_utc(to_utc(parse_local(cells[0]), tzinfo))
    except TimestampError:
        return None


@dataclass
class FileScan:
    """Cheap structural facts about one raw file, read before the real pass."""

    path: Path
    rel_path: str
    has_header: bool
    n_columns: int
    header: tuple[str, ...] | None
    first_ts: str | None
    #: Resolved later by :func:`_resolve_donors`.
    donor: FileScan | None = None

    @property
    def inferred(self) -> bool:
        return self.donor is not None

    @property
    def effective_header(self) -> tuple[str, ...] | None:
        """Own header if present, otherwise the donor's."""
        if self.has_header and self.header:
            return self.header
        return self.donor.header if self.donor else None

    @property
    def donor_name(self) -> str | None:
        return self.donor.rel_path if self.donor else None


def _scan_file(path: Path, rel_path: str) -> FileScan:
    block = detect_block(path)
    first_ts = None
    for _row_no, cells in iter_cells(path, block):
        if cells and cells[0] and not looks_like_header(cells[0]):
            first_ts = cells[0]
        break
    return FileScan(
        path=path,
        rel_path=rel_path,
        has_header=block.header is not None,
        n_columns=block.n_columns,
        header=block.header,
        first_ts=first_ts,
    )


def _scan_folder(raw_dir: Path) -> list[FileScan]:
    scans = []
    for path in sorted(raw_dir.glob("*.xlsx"), key=lambda p: p.name.lower()):
        rel = path.relative_to(raw_dir.parent).as_posix()
        scans.append(_scan_file(path, rel))
    return scans


def _resolve_donors(scans: list[FileScan]) -> None:
    """Give every headerless file the schema of a sibling whose layout matches.

    The archive is a chronological sequence of 2000-row chunks, and the header
    row only survives on the chunks that happened to be re-exported. So the
    closest *preceding* file that does carry a header is the best available
    description of what a headerless file's columns mean.

    **Width must match.** This is not a nicety. On 2020-06-17 the ``aisvn``
    applet gained a ``power`` column, going from 10 columns to 11, and the
    header-bearing chunk that predates it is the one donor logic would reach
    for. Applying a 10-column header to an 11-column row shifts every channel
    from index 4 onwards by one: ``load`` lands in ``wind``, ``wind`` lands in
    ``temp``, ``temp`` lands in ``solar2``, and the real ``boot`` counter is
    dropped. That silently mislabelled 45,986 readings -- 59% of the station --
    and it presented as a sensor fault: ``temp_c`` was receiving the ``wind``
    channel, which reads 0.0 when there is no wind, so 41,698 readings looked
    like sub-5 degC temperatures in Ho Chi City. It took the collector's own
    margin notes to make the window look like a hardware problem.

    So donor selection is: exact width match first, then nearest preceding by
    parsed time, then nearest following. A donor of the wrong width is not
    used at all.
    """
    donors = [s for s in scans if s.has_header and s.header]
    if not donors:
        return

    def _time(scan: FileScan) -> datetime:
        """Sort on the *parsed* instant, never the raw string.

        Column A is US-locale text, so "April ..." sorts before "August ..."
        lexicographically.  Ordering donors on the raw string would hand a
        September chunk the schema of an April one.
        """
        if not scan.first_ts:
            return datetime.max
        try:
            return parse_local(scan.first_ts)
        except TimestampError:
            return datetime.max

    for scan in scans:
        if scan.has_header or not scan.first_ts:
            continue

        matching = [d for d in donors if len(d.header) == scan.n_columns]
        if not matching:
            # Nothing in this folder describes a layout this wide. Leave the
            # file unmapped: `usable_width` then collapses to the key column
            # only, and the row still lands in `rejects`/`source_files` with
            # `inferred = 1` so the gap is visible rather than silent.
            continue

        anchor = _time(scan)
        preceding = [d for d in matching if _time(d) <= anchor]
        if preceding:
            scan.donor = max(preceding, key=_time)
        else:
            # No earlier match: take the earliest following one. The layout is
            # what matters here, not the direction, and the donor is recorded
            # either way so the inference is auditable.
            scan.donor = min(matching, key=_time)


@dataclass
class FileOutcome:
    file_id: int
    rel_path: str
    station_id: str
    has_header: bool
    ingested: int = 0
    duplicates: int = 0
    rejected: int = 0
    notes: int = 0
    min_ts: str | None = None
    max_ts: str | None = None


@dataclass
class RunSummary:
    run_id: int
    files: int = 0
    rows_ingested: int = 0
    rows_duplicate: int = 0
    rows_rejected: int = 0
    notes: int = 0
    failed: int = 0
    unknown_dirs: list[str] = field(default_factory=list)
    per_station: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    per_station_range: dict[str, tuple[str, str]] = field(default_factory=dict)


def _register_station(conn: sqlite3.Connection, station: stations.Station) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO stations"
        " (station_id, display_name, location, tz, applet, source_dirs, notes, is_production)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            station.station_id,
            station.display_name,
            station.location,
            station.tz,
            station.applet,
            json.dumps(list(station.source_dirs)),
            station.notes,
            0 if station.station_id in stations.NON_PRODUCTION else 1,
        ),
    )


def _register_metric_defs(
    conn: sqlite3.Connection,
    station: stations.Station,
    source_dir: str,
    header: tuple[str, ...] | None,
    n_columns: int,
    inferred: bool,
) -> None:
    """Record the column meaning for this folder.

    ``inferred`` is 1 when the column names were borrowed from a sibling file
    because this one had no header row.  That flag is the honest signal that we
    guessed the layout rather than read it, and it is what the report surfaces.
    """
    for mapping in build_row_mapping(header, n_columns):
        if mapping.index >= n_columns:
            continue
        metric = METRIC_BY_COLUMN.get(mapping.column) if mapping.column else None
        conn.execute(
            "INSERT INTO metric_defs"
            " (station_id, source_dir, col_index, raw_name, canonical_col, unit, kind,"
            "  confidence, inferred, reason, n_files)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)"
            " ON CONFLICT(station_id, source_dir, col_index) DO UPDATE SET"
            "   n_files = n_files + 1,"
            "   raw_name = CASE WHEN excluded.inferred = 1 THEN metric_defs.raw_name"
            "                   ELSE excluded.raw_name END,"
            "   canonical_col = COALESCE(excluded.canonical_col, metric_defs.canonical_col),"
            "   unit = COALESCE(excluded.unit, metric_defs.unit),"
            "   kind = COALESCE(excluded.kind, metric_defs.kind),"
            "   confidence = CASE WHEN excluded.confidence = 'high' THEN 'high'"
            "                      ELSE metric_defs.confidence END",
            (
                station.station_id,
                source_dir,
                mapping.index,
                mapping.raw_name,
                mapping.column,
                metric.unit if metric else None,
                metric.kind if metric else None,
                mapping.confidence,
                1 if inferred else 0,
                mapping.reason,
            ),
        )


def _bad_window_flags(station_id: str, ts_utc: str, row: dict) -> tuple[str, ...]:
    """Flag readings inside a window the collector has declared bad.

    The values are kept, not nulled: a window known to be unreliable is still
    evidence, and the flag is what stops a query from using it by accident. The
    declared windows are half-open, so the good period starts at ``valid_to``.
    """
    flags: list[str] = []
    for win_station, valid_from, valid_to, columns, _why in BAD_WINDOWS:
        if win_station != station_id or not (valid_from <= ts_utc < valid_to):
            continue
        for column in columns.split(","):
            column = column.strip()
            if row.get(column) is not None:
                flags.append(f"bad_window:{column}")
    return tuple(flags)


def _null_windows(station_id: str, ts_utc: str, row: dict) -> tuple[tuple[str, ...], list[str]]:
    """Null channels inside a window where the stored value is affirmatively wrong.

    Returns ``(flags, reasons)``. Unlike :func:`_bad_window_flags` this discards
    the number, because here the number makes a false claim -- 0.0 V from a
    panel says "produced nothing" when the truth is "not connected". Every
    affected cell is also written to ``rejects`` so nothing disappears silently.
    """
    flags: list[str] = []
    reasons: list[str] = []
    for win_station, valid_from, valid_to, columns, why in NULL_WINDOWS:
        if win_station != station_id or not (valid_from <= ts_utc < valid_to):
            continue
        for column in columns.split(","):
            column = column.strip()
            if row.get(column) is None:
                continue
            row[column] = None
            flags.append(f"no_signal:{column}")
            reasons.append(why)
    return tuple(flags), reasons


def _insert_file(
    conn: sqlite3.Connection,
    run_id: int,
    station: stations.Station,
    source_dir: str,
    scan: FileScan,
    digest: str,
) -> FileOutcome:
    path = scan.path
    block = detect_block(path)
    effective = scan.effective_header

    cur = conn.execute(
        "INSERT OR REPLACE INTO source_files"
        " (run_id, source_dir, station_id, filename, rel_path, sha256, bytes, has_header,"
        "  header_json, n_columns, n_body_rows, extra_blocks, repeated_headers,"
        "  schema_donor, inferred)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            source_dir,
            station.station_id,
            path.name,
            scan.rel_path,
            digest,
            path.stat().st_size,
            1 if block.header else 0,
            json.dumps(list(block.header)) if block.header else None,
            block.n_columns,
            block.n_rows,
            block.extra_blocks,
            len(block.repeated_headers),
            scan.donor_name,
            1 if scan.inferred else 0,
        ),
    )
    file_id = int(cur.lastrowid)

    # A donor header can be wider or narrower than the file it describes; only
    # the columns the file actually has are meaningful.
    usable_width = min(block.n_columns, len(effective)) if effective else block.n_columns
    misaligned = bool(effective) and len(effective) != block.n_columns
    if misaligned:
        # Should be unreachable now that _resolve_donors matches on width, but
        # a file that carries its own header can still disagree with itself.
        usable_width = min(block.n_columns, len(effective))
    _register_metric_defs(conn, station, source_dir, effective, usable_width, scan.inferred)

    # Row-level exclusion decided by the collector, e.g. the reinstall window in
    # data/raw/aisvn/IFTTT_aisvn (25).xlsx.
    row_floor = 0
    row_floor_reason = ""
    for suffix, first_row, why in ROW_EXCLUSIONS:
        if scan.rel_path.endswith(suffix.replace("/", "\\")) or scan.rel_path.endswith(suffix):
            row_floor = first_row
            row_floor_reason = why
            break

    outcome = FileOutcome(
        file_id=file_id,
        rel_path=scan.rel_path,
        station_id=station.station_id,
        has_header=block.header is not None,
    )

    mappings = {
        m.index: m for m in build_row_mapping(effective, block.n_columns) if m.index < usable_width
    }
    tzinfo = station.tzinfo
    free_text: list[str] = []
    rejects: list[tuple] = []
    cells_by_row: dict[int, list[str]] = {}

    for sheet_row, cells in iter_cells(path, block):
        if not cells:
            continue
        raw_ts = cells[0]
        if not raw_ts:
            continue
        if row_floor and sheet_row < row_floor:
            rejects.append(
                (
                    file_id,
                    station.station_id,
                    sheet_row,
                    "time",
                    raw_ts,
                    row_floor_reason,
                )
            )
            continue
        if looks_like_header(raw_ts):
            rejects.append(
                (file_id, station.station_id, sheet_row, "time", raw_ts, "repeated header row")
            )
            continue
        try:
            local = parse_local(raw_ts)
        except TimestampError as exc:
            rejects.append((file_id, station.station_id, sheet_row, "time", raw_ts, str(exc)))
            continue
        cells_by_row[sheet_row] = cells

        ts_utc = iso_utc(to_utc(local, tzinfo))
        row: dict[str, object] = {
            "station_id": station.station_id,
            "ts_utc": ts_utc,
            "ts_local": iso_local(local),
            "tz": station.tz,
            "quality_flags": "",
            "source_file_id": file_id,
            "sheet_row": sheet_row,
        }
        flags: list[tuple[str, ...]] = []

        for index, mapping in mappings.items():
            if index >= len(cells):
                continue
            column = mapping.column
            if column is None:
                continue
            metric = METRIC_BY_COLUMN.get(column)
            result = coerce_cell(cells[index], metric, free_text_out=free_text)
            row[column] = result.value
            flags.append(result.flags)

        null_flags, null_reasons = _null_windows(station.station_id, ts_utc, row)
        row_flags = merge_flags(
            *flags,
            (FLAG_MISALIGNED,) if misaligned else (),
            _bad_window_flags(station.station_id, ts_utc, row),
            null_flags,
        )
        row["quality_flags"] = row_flags
        values = tuple(row.get(name) for name in _INSERT_COLUMNS)
        inserted = conn.execute(_INSERT_SQL, values).rowcount
        if inserted:
            outcome.ingested += 1
            if outcome.min_ts is None or ts_utc < outcome.min_ts:
                outcome.min_ts = ts_utc
            if outcome.max_ts is None or ts_utc > outcome.max_ts:
                outcome.max_ts = ts_utc
        else:
            # A genuine duplicate: the same station already has a reading for
            # this instant.  In this archive these come from overlapping 2000-row
            # chunk boundaries and from IFTTT re-sends, concentrated in `test`
            # and `voltage-phumy` where a sheet concatenates several exports.
            #
            # Note this is NOT the same thing as the ~121,000 timestamps shared
            # between different stations: those are separate instruments
            # sampling the same wall clock, and they are both kept.  Only the
            # same-station collision is absorbed, and it is recorded here so the
            # row is traceable rather than merely counted.
            #
            # `reason` is a stable category, not a sentence.  The instant is
            # already in `raw_value` and the station in `station_id`, and the
            # report groups by `reason` -- embedding the timestamp here would
            # turn 4,403 duplicates into 4,361 singleton groups.
            outcome.duplicates += 1
            rejects.append(
                (
                    file_id,
                    station.station_id,
                    sheet_row,
                    "time",
                    raw_ts,
                    FLAG_DUPLICATE_TS,
                )
            )
        for reason in null_reasons:
            rejects.append(
                (
                    file_id,
                    station.station_id,
                    sheet_row,
                    ",".join(c for c in row_flags if c.startswith("no_signal:")),
                    raw_ts,
                    reason,
                )
            )

    for text in free_text:
        conn.execute(
            "INSERT INTO notes (run_id, station_id, file_id, ts_utc, column_name, note)"
            " VALUES (?, ?, ?, NULL, NULL, ?)",
            (run_id, station.station_id, file_id, text),
        )
        outcome.notes += 1

    # Notes written in spare columns *outside* the primary block.  These are the
    # experiment annotations ("discharge 7.5 Ah with 0.3A ...") and they carry
    # the context that makes an otherwise baffling reading explicable.  Columns
    # inside the primary block are already handled by ``coerce_cell``, so only
    # the side blocks are scanned here to avoid recording a note twice.
    for sheet_row, col_index, value in iter_all_cells(path, block):
        if col_index < block.n_columns:
            continue
        if looks_like_timestamp(value) or not looks_like_note(value):
            continue
        anchor = _row_timestamp(cells_by_row.get(sheet_row, []), tzinfo)
        conn.execute(
            "INSERT INTO notes (run_id, station_id, file_id, ts_utc, column_name, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                station.station_id,
                file_id,
                anchor,
                _column_letter(col_index),
                value,
            ),
        )
        outcome.notes += 1

    for row in rejects:
        conn.execute(
            "INSERT INTO rejects (run_id, file_id, station_id, sheet_row, column_name, raw_value, reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, *row),
        )
        outcome.rejected += 1

    conn.execute(
        "UPDATE source_files SET n_ingested = ?, n_rejected = ?, n_duplicate_ts = ?,"
        " min_ts_utc = ?, max_ts_utc = ? WHERE file_id = ?",
        (
            outcome.ingested,
            outcome.rejected,
            outcome.duplicates,
            outcome.min_ts,
            outcome.max_ts,
            file_id,
        ),
    )
    return outcome


def build_aggregates(conn: sqlite3.Connection) -> tuple[int, int]:
    """Materialise the hourly and daily rollups the website reads.

    Daily energy is derived from the hourly table rather than from ``readings``,
    because averaging then summing in one pass over per-sample rows would
    weight partial hours the same as full ones.
    """
    conn.execute("DELETE FROM readings_hourly")
    hourly = conn.execute(
        """
        INSERT INTO readings_hourly
            (station_id, ts_utc, n_samples,
             solar_v_avg, solar_v_max, solar_v_min,
             battery_v_avg, battery_v_min,
             power_w_avg, power_w_max,
             temp_c_avg, temp_c_min, temp_c_max,
             current_a_avg, energy_wh)
        SELECT
            station_id,
            substr(ts_utc, 1, 13) || ':00:00Z' AS hour,
            COUNT(*),
            AVG(solar_v),   MAX(solar_v),   MIN(solar_v),
            AVG(battery_v), MIN(battery_v),
            AVG(power_w),   MAX(power_w),
            AVG(temp_c),    MIN(temp_c),    MAX(temp_c),
            AVG(current_a),
            AVG(power_w) * (COUNT(*) * 2.0 / 3600.0)   -- 2-minute nominal cadence
        FROM readings
        GROUP BY station_id, hour
        """
    ).rowcount

    conn.execute("DELETE FROM readings_daily")
    daily = conn.execute(
        """
        INSERT INTO readings_daily
            (station_id, day, ts_utc_day, n_samples, n_hours,
             solar_v_avg, solar_v_max,
             battery_v_min, battery_v_max,
             power_w_avg, power_w_max, energy_wh,
             temp_c_min, temp_c_avg, temp_c_max)
        SELECT
            h.station_id,
            substr(h.ts_utc, 1, 10)                       AS day,
            substr(h.ts_utc, 1, 11) || '00:00:00Z'        AS day_start,
            SUM(h.n_samples),
            COUNT(*),
            AVG(h.solar_v_avg),   MAX(h.solar_v_max),
            MIN(h.battery_v_min), MAX(h.battery_v_min),
            AVG(h.power_w_avg),   MAX(h.power_w_max),
            SUM(h.energy_wh),
            MIN(h.temp_c_min),    AVG(h.temp_c_avg),    MAX(h.temp_c_max)
        FROM readings_hourly h
        GROUP BY h.station_id, day
        """
    ).rowcount
    conn.commit()
    return hourly, daily


def _update_station_ranges(conn: sqlite3.Connection) -> None:
    """Store each station's actual observed coverage on the stations row."""
    conn.execute(
        """
        UPDATE stations SET
            first_ts_utc = (SELECT MIN(ts_utc) FROM readings r WHERE r.station_id = stations.station_id),
            last_ts_utc  = (SELECT MAX(ts_utc) FROM readings r WHERE r.station_id = stations.station_id),
            n_readings  = (SELECT COUNT(*)    FROM readings r WHERE r.station_id = stations.station_id)
        """
    )
    conn.commit()


def ingest(settings: Settings, *, verbose: bool = True) -> RunSummary:
    # The database is deleted and rebuilt from scratch on every run.  There is
    # deliberately no incremental-update path: `source_files.sha256` already
    # records what was read, so a partial resume would be a second code path to
    # keep correct, and a stale row is far worse than a three-minute rebuild.
    settings.ensure_dirs()
    if settings.db_path.exists():
        settings.db_path.unlink()
    # WAL sidecars survive a hard delete and would otherwise be adopted by the
    # fresh database, corrupting it.
    for suffix in ("-wal", "-shm"):
        extra = settings.db_path.with_name(settings.db_path.name + suffix)
        if extra.exists():
            extra.unlink()

    conn = connect(settings.db_path)
    init_schema(conn)
    run_id = start_run(conn, settings.raw_dir, __version__)

    for station in stations.STATIONS:
        _register_station(conn, station)
    conn.commit()

    summary = RunSummary(run_id=run_id)
    raw_dirs = settings.raw_dirs()
    summary.unknown_dirs = [d.name for d in raw_dirs if d.name not in stations.BY_SOURCE_DIR]

    for raw_dir in raw_dirs:
        station = stations.station_for_dir(raw_dir.name)
        if station is None:
            if verbose:
                print(f"  ! skipping unknown folder {raw_dir.name!r} (not in the station registry)")
            continue

        scans = _scan_folder(raw_dir)
        _resolve_donors(scans)
        n_inferred = sum(1 for s in scans if s.inferred)
        if verbose:
            print(
                f"\n{raw_dir.name} -> station {station.station_id} "
                f"({len(scans)} files, {len(scans) - n_inferred} with a header, "
                f"{n_inferred} inheriting a schema)"
            )

        for scan in scans:
            digest = file_digest(scan.path)
            try:
                outcome = _insert_file(conn, run_id, station, raw_dir.name, scan, digest)
            except Exception as exc:
                # One malformed file must not abandon the other 363.  The
                # failure is counted and printed, and the quality report shows
                # `ingest_runs.notes`, so a silently short build is visible.
                conn.rollback()
                summary.files += 1
                summary.failed += 1
                if verbose:
                    print(f"    x {scan.path.name}: {type(exc).__name__}: {exc}")
                continue
            summary.files += 1
            summary.rows_ingested += outcome.ingested
            summary.rows_duplicate += outcome.duplicates
            summary.rows_rejected += outcome.rejected
            summary.notes += outcome.notes
            summary.per_station[station.station_id] += outcome.ingested
            if outcome.min_ts and outcome.max_ts:
                lo, hi = summary.per_station_range.get(
                    station.station_id, (outcome.min_ts, outcome.max_ts)
                )
                summary.per_station_range[station.station_id] = (
                    min(lo, outcome.min_ts),
                    max(hi, outcome.max_ts),
                )
        conn.commit()
        if verbose:
            print(
                f"    cumulative: {summary.rows_ingested:>7} rows"
                f"   dup {summary.rows_duplicate:>6}   reject {summary.rows_rejected:>5}"
            )

    if verbose:
        print("\nbuilding hourly/daily aggregates ...")
    hourly, daily = build_aggregates(conn)

    notes = (
        f"{summary.files} files; {summary.rows_ingested} rows; "
        f"{summary.rows_duplicate} duplicate timestamps absorbed; "
        f"{summary.rows_rejected} rejected; {hourly} hourly / {daily} daily buckets"
    )
    finish_run(conn, run_id, notes)
    log_build(
        conn,
        run_id,
        "db",
        settings.db_path.name,
        summary.rows_ingested,
        settings.db_path.stat().st_size,
    )
    _update_station_ranges(conn)
    conn.close()
    return summary

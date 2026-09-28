"""The ingest: 364 XLSX files into eight tables, from scratch, every time.

From scratch means from scratch
-------------------------------
``ingest`` deletes ``solardata.db`` and rebuilds it. There is no incremental
path, and that is the point rather than an omission: an incremental update has to
be right about which rows changed, which files were re-chunked, and what a
partial failure left behind, and every one of those is a way to end up with a
database that looks complete and is not. The archive is immutable, the build is
a minute, and ``data/processed/solardata.db`` is a gitignored artefact, so
there is nothing to be gained by being clever and everything to lose.

What a raw cell becomes
-----------------------
Exactly one of four things, and which one is never a guess:

* a number in the station table's column for that channel, in the channel's
  published unit -- the confirmed scale applied once, here, so the database, the
  rollups, the CSV and the browser all hold the same number in the same unit;
* NULL with a ``quality_flags`` entry saying why, plus a ``rejects`` row;
* a ``rejects`` row, for a row that never became a reading at all;
* a ``notes`` row, if it is human prose.

A value is never dropped for being implausible. It is kept and flagged, because
"this does not look right" is a finding and a filter is a decision nobody made.

Out of range means one thing
---------------------------
The band comes from ``etl.catalog`` and belongs to ``(station, channel)``, and it
is tested against the value in the unit that value is stored in. 0.8 tested a
raw millivolt cell against a volt band, which is why it could mark 631,252
readings out of range on arithmetic rather than on the hardware. The count is
now roughly 3,700 and every one of them is a claim about a sensor.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from . import catalog, db
from .config import (
    BAD_WINDOWS,
    FILE_EXCLUSIONS,
    FLAG_BAD_WINDOW,
    FLAG_FREE_TEXT,
    FLAG_MISALIGNED,
    FLAG_NO_SIGNAL,
    FLAG_OUT_OF_RANGE,
    FLAG_SENTINEL,
    NULL_WINDOWS,
    REASON_DUPLICATE_TS,
    REASON_NULL_WINDOW,
    REASON_PRE_REINSTALL,
    REASON_REPEATED_HEADER,
    REASON_SENTINEL,
    REASON_STATION_SETUP,
    REASON_UNPARSEABLE_TS,
    ROW_EXCLUSIONS,
    SENTINELS,
    Settings,
    is_excel_lock_file,
)
from .readers import times, xlsx
from .readers.times import TimestampError

__all__ = ["RunSummary", "ingest"]

#: A free-text cell shorter than this is noise. Used for prose found *inside* a
#: measurement column, where a long string really is an anomaly worth recording.
FREE_TEXT_MIN_LENGTH = 20

#: A side-block cell shorter than this is noise. Lower than the above on purpose:
#: the side blocks are where the humans wrote, and "STROMAUSFALL!!" is 14
#: characters of genuine prose that a length threshold throws away. 0.8 recovered
#: it with a structural test and a 20-character floor lost it.
SIDE_BLOCK_MIN_LENGTH = 4

_CLOCK_RE = re.compile(r"^\d{1,2}[:.]\d{2}")
_DATEISH_RE = re.compile(r"^\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?$")

#: Every column name the catalog knows, across every station and layout. A side
#: block repeats its header at the top, and a header word is not prose: without
#: this, the maker-webhooks sheets contribute twenty-two notes that are all just
#: the words "boot", "solar" and "battery". The vocabulary is taken from the
#: catalog rather than written out here, so a new channel's header is excluded
#: automatically.
_HEADER_WORDS: frozenset[str] = frozenset(
    word.lower()
    for station in catalog.STATIONS
    for layout in station.layouts
    for word in layout.header
) | frozenset({"time", "date", "timestamp", "datetime"})


def _looks_like_prose(value: str) -> bool:
    """Is this side-block cell something a person wrote?

    Structural, not a length test.  A cell is prose if it is not a number, not a
    timestamp, not a header word, not a clock or a date, and long enough to be a
    word.  Those are the five things a spreadsheet puts in a cell that is not
    prose, and asking about them directly is what recovers the short notes --
    "STROMAUSFALL!!", "leave home", "arrive at school", "at Library ..." -- that a
    20-character floor drops on the floor.
    """
    text = value.strip()
    if len(text) < SIDE_BLOCK_MIN_LENGTH:
        return False
    if text.lower() in _HEADER_WORDS:
        return False
    if times.looks_like_timestamp(text) or times.looks_like_header(text):
        return False
    if _CLOCK_RE.match(text) or _DATEISH_RE.match(text):
        return False
    try:
        float(text)
    except ValueError:
        return True
    return False


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

    def line(self) -> str:
        return (
            f"{self.rows_ingested:,} readings from {self.files} files across "
            f"{len(self.per_station)} stations; {self.rows_duplicate:,} duplicate "
            f"timestamps, {self.rows_rejected:,} rejected cells, {self.notes} notes"
        )


@dataclass
class _FileScan:
    """What one raw file is, before any of it is read for values."""

    path: Path
    rel_path: str
    station: catalog.Station
    layout: catalog.Layout
    block: xlsx.SheetBlock
    exclusion: str | None


# ---------------------------------------------------------------------------
# Cell coercion
# ---------------------------------------------------------------------------


def _coerce(
    raw: str,
    channel: catalog.Channel,
    free_text: list[str],
    ts_utc: str | None = None,
) -> tuple[float | str | None, tuple[str, ...]]:
    """One cell, to one value and its flags.

    The order matters and is the same order in every case:

    1. empty is NULL and flags nothing. A blank cell is a gap in the sheet, and
       a gap is not a zero.
    2. a number is the value; anything else is prose or a hole. Prose of
       sentence length is recovered into notes; everything else is ``free_text``.
    3. a NaN or infinite float is a sentinel.
    4. a sentinel is checked **in the raw unit, before the scale**, because
       -992 is -992 whether the channel publishes volts or millivolts, and
       -992 * 0.001 = -0.992 is not a sentinel, it is a plausible-looking
       number. 0.8 got this right by accident of ordering; it is written this
       way so that it stays right.
    5. the confirmed scale, once.
    6. any declared correction whose window covers this instant, once each.
    7. the band, against the corrected value, in the unit the value is stored
       in. Out of range keeps the value.
    """
    text = raw.strip()
    if not text:
        return None, ()

    if channel.kind == "text":
        return text, ()

    try:
        number = float(text)
    except ValueError:
        if len(text) >= FREE_TEXT_MIN_LENGTH:
            free_text.append(text)
        return None, (FLAG_FREE_TEXT,)

    if number != number or number in (float("inf"), float("-inf")):
        return None, (FLAG_SENTINEL,)

    if number in SENTINELS:
        return None, (FLAG_SENTINEL,)

    if channel.scale != 1.0:
        number = round(number * channel.scale, 9)

    number = channel.correct(number, ts_utc)

    if not channel.in_band(number):
        return number, (FLAG_OUT_OF_RANGE,)

    return number, ()


def _merge(*groups: tuple[str, ...]) -> str:
    """The comma-joined union of several flag tuples, in order, without repeats."""
    seen: dict[str, None] = {}
    for group in groups:
        for flag in group:
            seen.setdefault(flag, None)
    return ",".join(seen)


def _columns_covering(
    windows: tuple[tuple[str, str, str, tuple[str, ...], str], ...],
    station_id: str,
    ts_utc: str,
) -> list[tuple[str, ...]]:
    """The channel sets of every window covering this instant.

    The half-open bound is load-bearing twice over. For ``BAD_WINDOWS`` it is
    what makes the good window starting at ``valid_to`` the first clean sample
    rather than the second; for ``NULL_WINDOWS`` on ``aisvn.temp_c`` it is what
    keeps the 114 genuine tenths that follow the placeholder.
    """
    return [
        columns
        for stn, valid_from, valid_to, columns, _why in windows
        if stn == station_id and valid_from <= ts_utc < valid_to
    ]


def _exclusion_reason(rel_path: str) -> str | None:
    tail = rel_path.replace("\\", "/").lower()
    for prefix, why in FILE_EXCLUSIONS:
        if tail.endswith(prefix.lower()):
            return why
    return None


def _row_floor(rel_path: str) -> tuple[int, str] | None:
    tail = rel_path.replace("\\", "/").lower()
    for prefix, first_row, why in ROW_EXCLUSIONS:
        if tail.endswith(prefix.lower()):
            return first_row, why
    return None


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _scan_source(source: Path, station: catalog.Station, raw_dir: Path) -> list[_FileScan]:
    """Every XLSX in a folder, or the single XLSX file itself.

    An undeclared width is a hard error, not a fallback.  A width the catalog does
    not know means the sheet has a shape nobody has looked at, and 0.8's answer
    to that was to borrow a header from a neighbouring file, which is how 90% of
    an archive's measurements can be discarded while every timestamp still
    ingests and nothing reports a problem.

    The one exception is a file that is excluded whole: nothing is read out of
    it, so it does not need a layout, and a renamed or deleted sheet should not
    be able to stop the build.  It is scanned and its width recorded, unremarked.

    An Excel owner file (``~$...``) is not scanned at all.  It is 165 bytes of
    lock, it holds no rows, and openpyxl raises `PermissionError` on it -- so
    leaving it to the layout lookup produced a failure that named a file which
    looked like archive content, blamed the directory for it, and cost two minutes
    of ingest to diagnose.  Skipped, and counted so the run says so.
    """
    if source.is_file():
        paths = [source]
    else:
        paths = sorted(source.glob("*.xlsx"), key=lambda p: p.name.lower())

    scans: list[_FileScan] = []
    skipped_locks: list[str] = []
    for path in paths:
        if is_excel_lock_file(path.name):
            skipped_locks.append(str(path.name))
            continue
        try:
            rel_path = str(path.relative_to(raw_dir)).replace("\\", "/")
        except ValueError:
            rel_path = path.name
        block = xlsx.detect_block(path)
        exclusion = _exclusion_reason(rel_path)
        layout = station.layout_for(block.n_columns)
        if layout is None and exclusion is None:
            known = ", ".join(str(w) for w in sorted(station.by_width))
            raise ValueError(
                f"{rel_path}: station {station.station_id} has no layout for a "
                f"{block.n_columns}-column sheet. Known widths: {known}. Add it to "
                f"etl.catalog.Station.layouts with its header and channels."
            )
        scans.append(
            _FileScan(
                path=path,
                rel_path=rel_path,
                station=station,
                layout=layout,
                block=block,
                exclusion=exclusion,
            )
        )
    for skipped in skipped_locks:
        print(f"  skipped Excel owner file (not a sheet): {skipped}")
    return scans


_scan_folder = _scan_source


# ---------------------------------------------------------------------------
# Inserting one file
# ---------------------------------------------------------------------------


def _insert_file(
    conn: sqlite3.Connection,
    run_id: int,
    scan: _FileScan,
    digest: str,
) -> tuple[int, int, int, int, str | None, str | None]:
    """Return ``(ingested, duplicates, rejected, notes, min_ts, max_ts)``."""
    station = scan.station
    rel_path = scan.rel_path
    tzinfo = ZoneInfo(station.tz)
    layout = scan.layout
    block = scan.block

    cursor = conn.execute(
        "INSERT OR REPLACE INTO source_files"
        " (run_id, source_dir, station_id, filename, rel_path, sha256, bytes,"
        "  has_header, header_json, n_columns, layout_channels, n_rows,"
        "  extra_blocks, excluded)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            rel_path.split("/")[0] if "/" in rel_path else scan.path.stem,
            station.station_id,
            scan.path.name,
            rel_path,
            digest,
            scan.path.stat().st_size,
            1 if block.has_header else 0,
            json.dumps(list(layout.header)) if layout else None,
            block.n_columns,
            json.dumps(list(layout.channels)) if layout else None,
            block.n_rows,
            block.extra_blocks,
            scan.exclusion,
        ),
    )
    file_id = int(cursor.lastrowid or 0)

    rejects: list[tuple] = []
    notes: list[tuple] = []

    # --- A whole file that is not measurements -----------------------------
    # The source_files row stays, and every data row gets its own rejects row
    # with the sheet row and the timestamp, so the decision is inspectable one
    # cell at a time rather than taken on trust from a prose exclusion.
    if scan.exclusion is not None:
        for sheet_row, cells in xlsx.iter_cells(scan.path, block):
            raw_ts = cells[0] if cells else ""
            if not raw_ts or not times.looks_like_timestamp(raw_ts):
                continue
            rejects.append(
                (
                    run_id,
                    file_id,
                    station.station_id,
                    sheet_row,
                    "time",
                    raw_ts,
                    REASON_STATION_SETUP,
                )
            )
        notes.append((run_id, file_id, station.station_id, None, None, scan.exclusion))
        return _flush(conn, file_id, run_id, rejects, notes, 0, 0, None, None)

    # --- The row floor -----------------------------------------------------
    floor = _row_floor(rel_path)
    row_floor = floor[0] if floor else 0

    # One INSERT per reading, per station table. Prepared once per file because
    # every file of one station has the same layout and therefore the same
    # columns -- which is the payoff of generating the table from the catalog.
    channel_list = [station.by_name[name] for name in layout.channels]
    columns = (
        "ts_utc",
        "ts_local",
        *(ch.name for ch in channel_list),
        "flags",
        "source_file_id",
        "sheet_row",
    )
    placeholders = ", ".join("?" * len(columns))
    insert_sql = (
        f"INSERT OR IGNORE INTO {station.table} ({', '.join(columns)}) VALUES ({placeholders})"
    )

    ingested = 0
    duplicates = 0
    min_ts: str | None = None
    max_ts: str | None = None
    # `Layout.n_columns` already counts the timestamp column, so it is the same
    # quantity as `block.n_columns`; the layout is *keyed* on that width, which
    # is why a mismatch is not possible for an ingested file. It is computed
    # anyway because an excluded file has no layout and its width is unremarkable,
    # and because a mismatch here would be the first sign that `detect_block`
    # and the catalog have drifted.
    misaligned = layout is not None and block.n_columns != layout.n_columns
    free_text: list[str] = []

    for sheet_row, cells in xlsx.iter_cells(scan.path, block):
        raw_ts = cells[0] if cells else ""
        if not raw_ts:
            continue
        if sheet_row < row_floor:
            rejects.append(
                (
                    run_id,
                    file_id,
                    station.station_id,
                    sheet_row,
                    "time",
                    raw_ts,
                    REASON_PRE_REINSTALL,
                )
            )
            continue
        if not times.looks_like_timestamp(raw_ts):
            reason = (
                REASON_REPEATED_HEADER if times.looks_like_header(raw_ts) else REASON_UNPARSEABLE_TS
            )
            rejects.append((run_id, file_id, station.station_id, sheet_row, "time", raw_ts, reason))
            continue

        try:
            ts_utc, ts_local = times.parse_to_pair(raw_ts, tzinfo)
        except TimestampError:
            rejects.append(
                (
                    run_id,
                    file_id,
                    station.station_id,
                    sheet_row,
                    "time",
                    raw_ts,
                    REASON_UNPARSEABLE_TS,
                )
            )
            continue

        values: list[object] = [ts_utc, ts_local]
        cell_flags: list[tuple[str, ...]] = []
        for offset, ch in enumerate(channel_list, start=1):
            value, flags = _coerce(
                cells[offset] if offset < len(cells) else "", ch, free_text, ts_utc
            )
            values.append(value)
            if flags:
                cell_flags.append(flags)
                if flags == (FLAG_SENTINEL,):
                    rejects.append(
                        (
                            run_id,
                            file_id,
                            station.station_id,
                            sheet_row,
                            ch.name,
                            cells[offset] if offset < len(cells) else "",
                            REASON_SENTINEL,
                        )
                    )

        # Windows. Half-open, applied after the scale, so a null window that
        # names a channel matches the channel this station actually has.
        window_flags: list[tuple[str, ...]] = []
        bad_flags: list[tuple[str, ...]] = []
        for window_columns in _columns_covering(NULL_WINDOWS, station.station_id, ts_utc):
            for index, ch in enumerate(channel_list, start=2):
                # Only a cell that *had* a value needs correcting. A blank cell
                # is already a gap and needs nothing; flagging it would assert
                # that the collector said the input was disconnected when the
                # sheet says nothing at all, which is a different claim.
                #
                # This is not hypothetical: the aisvn.temp_c window covers 1,359
                # rows whose temp cell the collector's at-source repair left
                # empty. 0.8 checked for this and nulled nothing; a rewrite that
                # dropped the check would have stamped "no_signal" on 1,359 rows
                # that are blank because there is no probe reading to record.
                if ch.name in window_columns and values[index] is not None:
                    values[index] = None
                    window_flags.append((f"{FLAG_NO_SIGNAL}:{ch.name}",))
                    rejects.append(
                        (
                            run_id,
                            file_id,
                            station.station_id,
                            sheet_row,
                            ch.name,
                            raw_ts,
                            REASON_NULL_WINDOW,
                        )
                    )
        for window_columns in _columns_covering(BAD_WINDOWS, station.station_id, ts_utc):
            for index, ch in enumerate(channel_list, start=2):
                if ch.name in window_columns and values[index] is not None:
                    bad_flags.append((f"{FLAG_BAD_WINDOW}:{ch.name}",))

        row_flags = _merge(
            *cell_flags,
            *window_flags,
            *bad_flags,
            (FLAG_MISALIGNED,) if misaligned else (),
        )
        values.extend((row_flags, file_id, sheet_row))

        if conn.execute(insert_sql, values).rowcount:
            ingested += 1
            min_ts = ts_utc if min_ts is None or ts_utc < min_ts else min_ts
            max_ts = ts_utc if max_ts is None or ts_utc > max_ts else max_ts
        else:
            # INSERT OR IGNORE's rowcount is 0 for exactly one reason: this
            # station already has this instant. Not an error. It comes from
            # overlapping 2000-row chunk boundaries and from IFTTT re-sends,
            # and the absorbed row is recorded so it stays inspectable.
            duplicates += 1
            rejects.append(
                (
                    run_id,
                    file_id,
                    station.station_id,
                    sheet_row,
                    "time",
                    raw_ts,
                    REASON_DUPLICATE_TS,
                )
            )

    for text in free_text:
        notes.append((run_id, file_id, station.station_id, None, None, text))

    # Prose in the side blocks. Only exists for this: the hand-made discharge
    # summary and the lab annotations in Voltage_phumy.
    rows_by_index = {sheet_row: cells for sheet_row, cells in xlsx.iter_cells(scan.path, block)}
    for sheet_row, col_index, value in xlsx.iter_all_cells(scan.path, block):
        if col_index < block.n_columns:
            continue
        if not _looks_like_prose(value):
            continue
        anchor = None
        row_cells = rows_by_index.get(sheet_row)
        if row_cells and row_cells[0] and times.looks_like_timestamp(row_cells[0]):
            try:
                anchor, _ = times.parse_to_pair(row_cells[0], tzinfo)
            except TimestampError:
                anchor = None
        notes.append(
            (run_id, file_id, station.station_id, anchor, chr(ord("A") + col_index), value)
        )

    return _flush(conn, file_id, run_id, rejects, notes, ingested, duplicates, min_ts, max_ts)


def _flush(
    conn: sqlite3.Connection,
    file_id: int,
    run_id: int,
    rejects: list[tuple],
    notes: list[tuple],
    ingested: int,
    duplicates: int,
    min_ts: str | None,
    max_ts: str | None,
) -> tuple[int, int, int, int, str | None, str | None]:
    """Write the buffered rejects and notes, then close out the file's row."""
    if rejects:
        conn.executemany(
            "INSERT INTO rejects (run_id, file_id, station_id, sheet_row, column_name,"
            " raw_value, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rejects,
        )
    if notes:
        conn.executemany(
            "INSERT INTO notes (run_id, file_id, station_id, ts_utc, column_name, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            notes,
        )
    conn.execute(
        "UPDATE source_files SET n_ingested = ?, n_duplicate_ts = ?, n_rejected = ?,"
        " n_notes = ?, min_ts_utc = ?, max_ts_utc = ? WHERE file_id = ?",
        (ingested, duplicates, len(rejects), len(notes), min_ts, max_ts, file_id),
    )
    return ingested, duplicates, len(rejects), len(notes), min_ts, max_ts


# ---------------------------------------------------------------------------
# Station bookkeeping
# ---------------------------------------------------------------------------


def _register_stations(conn: sqlite3.Connection) -> None:
    for station in catalog.STATIONS:
        conn.execute(
            "INSERT OR REPLACE INTO stations (station_id, table_name, display_name, location,"
            " tz, applet, source_dirs, is_production, published_group, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                station.station_id,
                station.table,
                station.display_name,
                station.location,
                station.tz,
                station.applet,
                json.dumps(list(station.source_dirs)),
                1 if station.production else 0,
                station.published_group if station.production else "not solar production",
                station.notes,
            ),
        )


def _update_coverage(conn: sqlite3.Connection, summary: RunSummary) -> None:
    """Fill in each station's observed extent, by asking its own table."""
    for station in catalog.STATIONS:
        first, last, n = conn.execute(
            f"SELECT MIN(ts_utc), MAX(ts_utc), COUNT(*) FROM {station.table}"
        ).fetchone()
        conn.execute(
            "UPDATE stations SET first_ts_utc = ?, last_ts_utc = ?, n_readings = ?"
            " WHERE station_id = ?",
            (first, last, n, station.station_id),
        )
        if n:
            summary.per_station[station.station_id] = int(n)
            summary.per_station_range[station.station_id] = (str(first), str(last))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def ingest(settings: Settings, *, verbose: bool = True) -> RunSummary:
    from . import __version__

    settings.ensure_dirs()

    # From scratch, every time. See the module docstring.
    for path in (
        settings.db_path,
        Path(f"{settings.db_path}-wal"),
        Path(f"{settings.db_path}-shm"),
    ):
        path.unlink(missing_ok=True)

    conn = db.connect(settings.db_path)
    try:
        db.init_schema(conn)
        _register_stations(conn)
        db.write_catalog(conn)
        run_id = db.start_run(conn, str(settings.raw_dir), __version__)
        conn.commit()

        summary = RunSummary(run_id=run_id)
        sources = settings.raw_sources()
        for source in sources:
            station = catalog.station_for_source(source.name)
            if station is None:
                # A source under data/raw that the registry does not know is data
                # nobody has decided what to do with. It is not ingested, and it
                # is not dropped quietly either: it is named in the run's notes
                # and printed, so "the build read 10 sources and ingested 9" is
                # visible rather than inferred from a count.
                summary.unknown_dirs.append(source.name)
                if verbose:
                    print(
                        f"  {source.name}: not in the station registry, ignored "
                        "(add it to etl.catalog if it is a station)"
                    )
                continue

            # Bound the sheet cache to one source: the cache is what makes the
            # build fast and it is also the largest thing in memory.
            xlsx.clear_read_cache()
            scans = _scan_source(source, station, settings.raw_dir)
            if verbose:
                print(f"  {source.name}: {len(scans)} files -> {station.table}")

            for scan in scans:
                digest = xlsx.file_digest(scan.path)
                try:
                    ingested, dupes, rejected, notes, *_ = _insert_file(conn, run_id, scan, digest)
                except Exception as exc:
                    conn.rollback()
                    summary.failed += 1
                    if verbose:
                        print(f"    FAILED {scan.rel_path}: {exc}")
                    continue
                summary.files += 1
                summary.rows_ingested += ingested
                summary.rows_duplicate += dupes
                summary.rows_rejected += rejected
                summary.notes += notes
            conn.commit()

        _update_coverage(conn, summary)
        conn.commit()

        reads = len(sources)
        notes = (
            f"{summary.rows_ingested} readings from {summary.files} files across "
            f"{len(summary.per_station)} stations in {reads} sources"
        )
        if summary.unknown_dirs:
            notes += f"; ignored sources not in the registry: {', '.join(summary.unknown_dirs)}"
        if summary.failed:
            notes += f"; {summary.failed} file(s) failed"
        db.finish_run(conn, run_id, notes)
        db.log_build(conn, run_id, "db", str(settings.db_path), summary.rows_ingested)
        return summary
    finally:
        conn.close()

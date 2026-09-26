"""Detect and record unit-scaling regimes after ingest.

Run *after* :mod:`etl.build_db`, because it needs the readings already in the
database to compute per-file medians.  Every proposal lands in ``regimes`` with
``status='unconfirmed'``: the pipeline flags, a human adjudicates.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from etl.normalize.metrics import METRIC_BY_COLUMN
from etl.normalize.units import detect_per_file

#: Only watch channels where a mis-scaling actually happened in the archive.
WATCHED = (
    "battery_v",
    "battery2_v",
    "solar_v",
    "solar2_v",
    "temp_c",
    "lipo_v",
    "lipo2_v",
    "load_v",
)

#: Regimes the collector has confirmed against the firmware, as
#: (station_id, column, scale).
#:
#: These deliberately have no date window. The collector's statement is that
#: these channels are logged in millivolts *as integers* for their whole record
#: -- not over some sub-period -- so the window is taken from the extent of the
#: data rather than from whatever window the detector happened to propose.
#: Pinning a window that is narrower than the channel is how phumy2.solar2_v
#: ended up scaled for its first seven months and raw for the following three
#: years.
#:
#: Note for phumy2.solar2_v: the *unit* is millivolts throughout, but the
#: *level* steps from ~5000 mV to ~1200 mV when a bridge and load are fitted.
#: Scaling the whole channel by 0.001 therefore yields panel voltage before the
#: bridge and a bridge-divider output after it. Recovering the panel voltage
#: after the bridge needs the divider ratio, which is not in the archive.
CONFIRMED: tuple[tuple[str, str, float], ...] = (
    ("aisvn-solar", "solar_v", 0.001),
    ("aisvn-solar", "lipo_v", 0.001),
    ("aisvn2", "battery2_v", 0.001),
    ("maker-webhooks", "solar_v", 0.001),
    ("maker-webhooks", "battery_v", 0.001),
    ("maker-webhooks", "load_v", 0.001),
    ("maker-webhooks", "lipo_v", 0.001),
    ("solar-2020-05", "lipo_v", 0.001),
    ("test", "solar_v", 0.001),
    ("test", "battery_v", 0.001),
    ("test", "lipo_v", 0.001),
    ("phumy2", "solar2_v", 0.001),
    ("phumy2", "lipo2_v", 0.001),
)


def _channel_extent(conn, station_id: str, column: str) -> tuple[str | None, str | None]:
    """First and last instant a channel has any value at all."""
    row = conn.execute(
        f"SELECT MIN(ts_utc), MAX(ts_utc) FROM readings"
        f" WHERE station_id = ? AND {column} IS NOT NULL",
        (station_id,),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def apply_confirmed(conn) -> int:
    """Write the collector-confirmed regimes, windowed to the channel's extent.

    Runs after the heuristic detector so a confirmed row replaces the
    proposal for the same channel rather than sitting beside it.

    ``valid_to`` is the *exclusive* end, so it is the day after the last day with
    data.  Writing the last day's own date would exclude that day from a
    half-open window, which is how ``aisvn-solar`` kept a raw 601 V on its final
    day and ``phumy2`` a raw 1384 V on 2024-02-01.
    """
    written = 0
    for station_id, column, scale in CONFIRMED:
        first, last = _channel_extent(conn, station_id, column)
        if first is None:
            continue
        exclusive_end = (datetime.fromisoformat(last[:10]) + timedelta(days=1)).date().isoformat()
        conn.execute(
            "DELETE FROM regimes WHERE station_id = ? AND column = ?   AND status = 'unconfirmed'",
            (station_id, column),
        )
        conn.execute(
            "INSERT OR REPLACE INTO regimes"
            " (station_id, column, unit, valid_from, valid_to, scale, status,"
            "  detected_by, confidence, notes, evidence)"
            " VALUES (?, ?, ?, ?, ?, ?, 'confirmed', 'manual', 'high', ?, ?)",
            (
                station_id,
                column,
                METRIC_BY_COLUMN[column].unit if column in METRIC_BY_COLUMN else None,
                first,
                exclusive_end,
                scale,
                "collector-confirmed: logged in millivolts as an integer for the whole record",
                json.dumps(
                    {
                        "scale": scale,
                        "source": "collector confirmation, window from channel extent",
                        "confirmed_against_firmware": True,
                    }
                ),
            ),
        )
        written += 1
    conn.commit()
    return written


@dataclass
class RegimeReport:
    station_id: str
    column: str
    scale: float
    valid_from: str
    valid_to: str | None
    confidence: str
    notes: str

    def window(self) -> str:
        return f"{self.valid_from} .. {self.valid_to or 'open'}"


def _windows_for_column(
    conn: sqlite3.Connection, column: str
) -> dict[str, list[tuple[str, str | None, list[float]]]]:
    """Collect per-source-file value windows for one column, grouped by station.

    One source file is one window: the archive is already chunked by the
    collector, so a file boundary is a good enough proxy for a regime boundary
    and keeps the evidence traceable to a file the human can open.
    """
    spans = {
        (row["station_id"], row["min_ts_utc"]): row["max_ts_utc"]
        for row in conn.execute(
            "SELECT station_id, min_ts_utc, max_ts_utc FROM source_files"
            " WHERE min_ts_utc IS NOT NULL"
        )
    }

    rows = conn.execute(
        f"""
        SELECT r.station_id AS station_id,
               f.min_ts_utc AS valid_from,
               r.{column}    AS value
        FROM readings r
        JOIN source_files f ON f.file_id = r.source_file_id
        WHERE r.{column} IS NOT NULL
        """
    ).fetchall()

    buckets: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        if row["valid_from"] is None:
            continue
        buckets.setdefault((row["station_id"], row["valid_from"]), []).append(row["value"])

    grouped: dict[str, list[tuple[str, str | None, list[float]]]] = {}
    for (station_id, valid_from), values in sorted(buckets.items()):
        grouped.setdefault(station_id, []).append(
            (valid_from, spans.get((station_id, valid_from)), values)
        )
    return grouped


#: Two per-file windows count as adjacent when the gap between them is under
#: this many hours.  Chunks in the archive are near-contiguous but not exactly
#: so, because the collector's clock and the sheet split are independent.
ADJACENCY_TOLERANCE_HOURS = 36


def _coalesce(items: list[RegimeReport]) -> list[RegimeReport]:
    """Merge near-adjacent windows that propose the same scale.

    The detector works per source file, which is what makes the evidence
    traceable, but it emits one row per file.  Neighbouring chunks almost always
    agree, so adjacent same-scale windows are merged into a single span.
    """
    ordered = sorted(items, key=lambda r: (r.station_id, r.column, r.valid_from))
    merged: list[RegimeReport] = []
    for item in ordered:
        previous = merged[-1] if merged else None
        adjacent = False
        if (
            previous is not None
            and previous.station_id == item.station_id
            and previous.column == item.column
            and previous.scale == item.scale
            and previous.valid_to
        ):
            gap_hours = (
                datetime.fromisoformat(item.valid_from.replace("Z", "+00:00"))
                - datetime.fromisoformat(previous.valid_to.replace("Z", "+00:00"))
            ).total_seconds() / 3600.0
            adjacent = -ADJACENCY_TOLERANCE_HOURS <= gap_hours <= ADJACENCY_TOLERANCE_HOURS
        if adjacent and previous is not None:
            merged[-1] = RegimeReport(
                previous.station_id,
                previous.column,
                previous.scale,
                previous.valid_from,
                item.valid_to,
                previous.confidence,
                previous.notes,
            )
        else:
            merged.append(item)
    return merged


def detect(conn: sqlite3.Connection, *, verbose: bool = True) -> list[RegimeReport]:
    """Propose scale regimes, then apply the collector-confirmed ones.

    The confirmed rows go in last so they replace the detector's proposal for
    the same channel rather than sitting beside it, and they are windowed to the
    extent of the data instead of to whichever sub-period the detector happened
    to look at.
    """
    conn.execute("DELETE FROM regimes WHERE detected_by = 'range'")
    found: list[RegimeReport] = []

    for column in WATCHED:
        metric = METRIC_BY_COLUMN[column]
        for station_id, windows in sorted(_windows_for_column(conn, column).items()):
            for regime in detect_per_file(
                station_id, column, metric.unit, windows, metric.lo, metric.hi
            ):
                found.append(
                    RegimeReport(
                        regime.station_id,
                        column,
                        regime.scale,
                        regime.valid_from,
                        regime.valid_to,
                        regime.confidence,
                        regime.notes,
                    )
                )

    for item in _coalesce(found):
        conn.execute(
            "INSERT OR REPLACE INTO regimes"
            " (station_id, column, unit, valid_from, valid_to, scale, status,"
            "  detected_by, confidence, notes, evidence)"
            " VALUES (?, ?, ?, ?, ?, ?, 'unconfirmed', 'range', ?, ?, ?)",
            (
                item.station_id,
                item.column,
                METRIC_BY_COLUMN[item.column].unit,
                item.valid_from,
                item.valid_to,
                item.scale,
                item.confidence,
                item.notes,
                json.dumps(
                    {
                        "scale": item.scale,
                        "source": "per-file medians, coalesced",
                        "confirmed_against_firmware": False,
                    }
                ),
            ),
        )

    conn.commit()
    signed_off = apply_confirmed(conn)

    if verbose:
        merged = _coalesce(found)
        remaining = conn.execute(
            "SELECT COUNT(*) FROM regimes WHERE status = 'unconfirmed'"
        ).fetchone()[0]
        print(
            f"  {len(found)} per-file windows -> {len(merged)} proposals;"
            f" {signed_off} channels confirmed against firmware;"
            f" {remaining} windows still unconfirmed"
        )
        for item in merged[:12]:
            print(
                f"    {item.station_id:<14} {item.column:<11} "
                f"x{item.scale:<9g} {item.window()}  [{item.confidence}]"
            )
    return _coalesce(found)

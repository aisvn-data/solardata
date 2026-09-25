"""Detect and record unit-scaling regimes after ingest.

Run *after* :mod:`etl.build_db`, because it needs the readings already in the
database to compute per-file medians.  Every proposal lands in ``regimes`` with
``status='unconfirmed'``: the pipeline flags, a human adjudicates.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

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

#: Regimes the collector has confirmed against the firmware. Keyed by
#: (station_id, column, valid_from date) -> the confirmed scale.
#:
#: The collector reports that these stations log millivolts as integers
#: throughout their records: `aisvn-solar`, `maker-webhooks`, `solar-2020-05`
#: and `test` are all x0.001 and their records are short and self-consistent.
#: These are marked 'confirmed' rather than left as proposals, so a query can
#: trust them; the remaining `aisvn` windows stay 'unconfirmed' because they
#: need to be checked against the firmware one at a time.
CONFIRMED: tuple[tuple[str, str, str, float], ...] = (
    ("aisvn-solar", "solar_v", "2020-05-21", 0.001),
    ("aisvn-solar", "lipo_v", "2020-05-21", 0.001),
    ("maker-webhooks", "solar_v", "2020-05-30", 0.001),
    ("maker-webhooks", "battery_v", "2020-05-30", 0.001),
    ("maker-webhooks", "load_v", "2020-05-30", 0.001),
    ("maker-webhooks", "lipo_v", "2020-05-30", 0.001),
    ("solar-2020-05", "lipo_v", "2020-05-16", 0.001),
    ("test", "solar_v", "2020-06-12", 0.001),
    ("test", "battery_v", "2020-06-12", 0.001),
    ("test", "lipo_v", "2020-06-12", 0.001),
    ("aisvn2", "battery2_v", "2020-06-18", 0.001),
)


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
    """Propose scale regimes and persist them to the ``regimes`` table."""
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
        signed_off = item.scale in _confirmed_scales(item)
        conn.execute(
            "INSERT OR REPLACE INTO regimes"
            " (station_id, column, unit, valid_from, valid_to, scale, status,"
            "  detected_by, confidence, notes, evidence)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.station_id,
                item.column,
                METRIC_BY_COLUMN[item.column].unit,
                item.valid_from,
                item.valid_to,
                item.scale,
                # Only a human confirmation promotes a regime. `detected_by`
                # records which route it took, so a confirmed regime is still
                # distinguishable from a heuristic one that happens to agree.
                "confirmed" if signed_off else "unconfirmed",
                "manual" if signed_off else "range",
                item.confidence,
                item.notes,
                json.dumps(
                    {
                        "scale": item.scale,
                        "source": "per-file medians, coalesced",
                        "confirmed_against_firmware": signed_off,
                    }
                ),
            ),
        )

    conn.commit()
    if verbose:
        if not found:
            print("  no scale anomalies detected")
        else:
            merged = _coalesce(found)
            confirmed = sum(1 for r in merged if r.scale in _confirmed_scales(r))
            print(
                f"  {len(found)} per-file windows -> {len(merged)} regimes; "
                f"{confirmed} confirmed against firmware, "
                f"{len(merged) - confirmed} still unconfirmed:"
            )
            for item in merged[:20]:
                state = "confirmed" if item.scale in _confirmed_scales(item) else item.confidence
                print(
                    f"    {item.station_id:<14} {item.column:<11} "
                    f"x{item.scale:<9g} {item.window()}  [{state}]"
                )
    return _coalesce(found)


def _confirmed_scales(regime: RegimeReport) -> set[float]:
    """Scales a human has signed off for this station/column/period."""
    return {
        scale
        for st, col, date, scale in CONFIRMED
        if st == regime.station_id and col == regime.column and regime.valid_from.startswith(date)
    }

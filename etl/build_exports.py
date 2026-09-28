"""``public/data``: what the website fetches.

Four JSON files and one CSV per station, year and resolution.  The rule that
governs all of it:

**A station's CSV carries only the channels that station collects, in that
station's unit, and nothing else.**

That is why the header is generated per station rather than from a global
column list.  0.8 wrote 32 statistic columns into every CSV for all 16
station-years, so ``phumy2`` -- which has five real channels -- shipped 27
columns of NULL, and the browser had to run ``discoverChannels`` to work out
which of them were real, with a fallback path for a station whose units
overridden the pipeline's.  Here there is nothing to discover: if a column is in
the header, that station logs it, and if it is not in the header the browser
cannot ask for it.  A channel cannot be listed, unreachable, or present-but-
fictionally zero, because a NULL never leaves this stage.

The consequence for the reader is the same as for the pipeline: ``phumy2``
has no ``power_w`` column at all, because the pin is not a measurement, and
``aisvn-solar`` has no ``wind_v`` column, because that input never varies.
Their values are in the database and in the quality report; they are simply not
presented as measurements on a chart.

``stations.json`` carries each station's channel list verbatim from the
``station_channels`` table, so the picker, the unit suffix on the axis and the
band the chart rings a value against all come from the same declaration the
ingest applied to the raw cell.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from . import catalog
from .config import Settings

__all__ = ["build", "csv_header", "station_payload"]

#: ``AVG`` -> ``mean`` in prose, and the empty string for a gap. Never 0: a gap
#: in the readings has to stay a gap or the chart draws a line across an outage.
_GAP = ""


def _fmt(value: float | int | None) -> str:
    if value is None:
        return _GAP
    if isinstance(value, int):
        return str(value)
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(round(value, 6))


def csv_header(station_id: str, hourly: bool) -> tuple[str, ...]:
    """The header for one station's CSV.

    ``ts``, then how many readings the bucket holds, then every published
    channel's statistics, then one ``<channel>_n_oor`` for every banded channel.
    No global column list is consulted, so the header cannot contain a channel
    this station does not have and cannot omit one it does.

    ``n_samples`` is the count of raw readings in the bucket and ``n_hours`` the
    number of hourly buckets the day's rollup was derived from. Both are metadata
    about the bucket rather than measurements, and both are load-bearing: without
    ``n_samples`` the browser cannot tell a day with one reading from a day with
    five hundred, and a bucket with none must not be drawn at all.

    The row-level ``n_out_of_range`` is deliberately *not* a column. It is the sum
    of the per-channel ``_n_oor`` columns, and shipping it as well is a second
    number to keep in step with the first -- which is how 0.8 ended up with a
    per-channel count that had been recomputed after a rescale and a row-level
    count that had not.
    """
    station = catalog.BY_ID[station_id]
    columns = ["ts", "n_samples"] if hourly else ["ts", "n_samples", "n_hours"]
    for ch in station.published:
        for stat in ch.stats:
            columns.append(ch.stat_column(stat))
    for ch in station.published:
        if ch.band and ch.kind != "text":
            columns.append(f"{ch.name}_n_oor")
    return tuple(columns)


def _stat_column_order(station_id: str) -> list[str]:
    """The rollup columns, in the same order as ``csv_header`` after ``ts``."""
    station = catalog.BY_ID[station_id]
    order: list[str] = []
    for ch in station.published:
        order.extend(ch.stat_column(stat) for stat in ch.stats)
    for ch in station.published:
        if ch.band and ch.kind != "text":
            order.append(f"{ch.name}_n_oor")
    return order


def _write_csv(
    path: Path,
    header: tuple[str, ...],
    columns: list[str],
    rows: list[sqlite3.Row],
    time_column: str,
    hourly: bool,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = ["n_samples"] if hourly else ["n_samples", "n_hours"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(
                [row[time_column]]
                + [_fmt(row[c]) for c in counts]
                + [_fmt(row[c]) for c in columns]
            )
    return len(rows)


def station_payload(
    row: sqlite3.Row, channels: list[dict], years: list[str], published: bool
) -> dict:
    """One entry of ``stations.json``.

    ``channels`` is the station's full channel list, each with its unit, band,
    kind and the reason it is there.  A browser that renders a unit suffix, a
    plausible-band ring and a channel picker from this one object cannot disagree
    with the criterion the ingest applied, because it is the same declaration read
    back out of the database.

    ``published`` means "there is something here to draw", not "this is solar
    production".  The two bench stations are published -- a reader told a station
    is a WiFi probe can decide what to do with it -- and the group they are listed
    under says what they are.
    """
    return {
        "station_id": row["station_id"],
        "display_name": row["display_name"],
        "location": row["location"],
        "tz": row["tz"],
        "applet": row["applet"],
        "source_dirs": json.loads(row["source_dirs"]),
        "notes": row["notes"],
        "is_production": bool(row["is_production"]),
        "group": row["published_group"],
        "first_ts_utc": row["first_ts_utc"],
        "last_ts_utc": row["last_ts_utc"],
        "n_readings": row["n_readings"],
        "table": row["table_name"],
        "published": published,
        "granularities": ["hourly", "daily"] if years else [],
        "channels": channels,
        "hidden": [
            {
                "channel": c["channel"],
                "label": c["label"],
                "kind": c["kind"],
                "unit": c["unit"],
                "reason": c["exclude_reason"],
                "note": c["exclude_note"],
                "observed": c["observed"],
            }
            for c in channels
            if not c["published"]
        ],
        "years": years,
    }


def _channel_rows(conn: sqlite3.Connection, station_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT channel, label, kind, description, unit, raw_unit, scale, band_lo, band_hi,"
        " band_note, stats, published, exclude_reason, exclude_note, is_counter, decimals"
        " FROM station_channels WHERE station_id = ? ORDER BY rowid",
        (station_id,),
    ).fetchall()
    out = []
    for row in rows:
        stats = [s for s in row["stats"].split(",") if s]
        out.append(
            {
                "channel": row["channel"],
                "label": row["label"],
                "kind": row["kind"],
                "description": row["description"],
                "unit": row["unit"],
                "raw_unit": row["raw_unit"],
                "scale": row["scale"],
                "band": [row["band_lo"], row["band_hi"]],
                "band_note": row["band_note"],
                "stats": stats,
                "published": bool(row["published"]),
                "exclude_reason": row["exclude_reason"],
                "exclude_note": row["exclude_note"],
                "is_counter": bool(row["is_counter"]),
                "decimals": row["decimals"],
            }
        )
    return out


def _observed(conn: sqlite3.Connection, station_id: str) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT channel, n_values, min, max, mean, p01, p50, p99, n_out_of_range, n_zero"
        " FROM channel_stats WHERE station_id = ?",
        (station_id,),
    ).fetchall()
    return {r["channel"]: dict(r) for r in rows}


def build(conn: sqlite3.Connection, settings: Settings, *, verbose: bool = True) -> dict:
    """Write ``public/data`` and return a small summary for the report."""
    export_dir = Path(settings.export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    stations: list[dict] = []
    csv_files = 0
    csv_rows = 0

    for row in conn.execute("SELECT * FROM stations ORDER BY is_production DESC, station_id"):
        station_id = row["station_id"]
        channels = _channel_rows(conn, station_id)
        observed = _observed(conn, station_id)
        for ch in channels:
            ch["observed"] = observed.get(ch["channel"])

        # Every station in the registry is written, including the two that are
        # not solar production.  A grouping is not a filter: `test` and
        # `voltage-phumy` hold 38,930 readings that are in the database, in the
        # Parquet export and in the report, and withholding them from the rollups
        # meant a station the documentation describes was unreachable on the
        # site.  They carry `published: false` and their own note, and the site
        # lists them under "Not solar production".
        published = [c for c in channels if c["published"]]
        columns = _stat_column_order(station_id)
        for hourly, folder, time_column in (
            (True, "hourly", "ts_utc"),
            (False, "daily", "day"),
        ):
            header = csv_header(station_id, hourly)
            table = "readings_hourly" if hourly else "readings_daily"
            for entry in conn.execute(
                f"SELECT DISTINCT substr({time_column}, 1, 4) AS y FROM {table}"
                f" WHERE station_id = ? ORDER BY y",
                (station_id,),
            ).fetchall():
                year = entry["y"]
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE station_id = ?"
                    f" AND substr({time_column}, 1, 4) = ? ORDER BY {time_column}",
                    (station_id, year),
                ).fetchall()
                csv_rows += _write_csv(
                    export_dir / station_id / folder / f"{year}.csv",
                    header,
                    columns,
                    rows,
                    time_column,
                    hourly,
                )
                csv_files += 1

        # One entry per year, taken from both resolutions, so the manifest
        # cannot list a year the Hour view then 404s on.
        station_years = sorted(
            {
                r["y"]
                for r in conn.execute(
                    "SELECT DISTINCT substr(ts_utc, 1, 4) AS y FROM readings_hourly"
                    " WHERE station_id = ?",
                    (station_id,),
                )
            }
            | {
                r["y"]
                for r in conn.execute(
                    "SELECT DISTINCT substr(day, 1, 4) AS y FROM readings_daily"
                    " WHERE station_id = ?",
                    (station_id,),
                )
            }
        )
        # `years` is a plain list of strings because that is what the year picker
        # is: a list of options. Which resolutions exist is a property of the
        # station, not of the year -- every station with data has both -- so it is
        # a sibling key rather than repeated per year.
        stations.append(station_payload(row, channels, station_years, bool(published)))

    metrics = {
        "note": (
            "Plausibility bands are per (station, channel), not per column name. "
            "Each entry is the declaration the ingest applied to the raw cell, in "
            "the unit that cell is stored in, so the chart rings a value against "
            "the same criterion that flagged it. A channel with band [null, null] "
            "has no band and is never flagged."
        ),
        "channels": {
            f"{s['station_id']}.{c['channel']}": {
                "station_id": s["station_id"],
                "channel": c["channel"],
                "label": c["label"],
                "kind": c["kind"],
                "description": c["description"],
                "unit": c["unit"],
                "band": c["band"],
                "band_note": c["band_note"],
                "published": c["published"],
            }
            for s in stations
            for c in s["channels"]
        },
    }

    (export_dir / "stations.json").write_text(
        json.dumps(stations, indent=2) + "\n", encoding="utf-8"
    )
    (export_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    curation_src = Path(__file__).resolve().parent.parent / "data" / "config" / "curation.json"
    if curation_src.exists():
        (export_dir / "curation.json").write_text(
            curation_src.read_text(encoding="utf-8"), encoding="utf-8"
        )

    normalization_src = (
        Path(__file__).resolve().parent.parent / "data" / "config" / "normalization.json"
    )
    if normalization_src.exists():
        (export_dir / "normalization.json").write_text(
            normalization_src.read_text(encoding="utf-8"), encoding="utf-8"
        )

    raw_db_src = (
        Path(__file__).resolve().parent.parent / "data" / "processed" / "solardata_raw.db"
    )
    if raw_db_src.exists():
        import shutil

        shutil.copy2(raw_db_src, export_dir / "solardata_raw.db")

    summary = {
        "stations": len(stations),
        "csv_files": csv_files,
        "csv_rows": csv_rows,
        "published_stations": sum(1 for s in stations if s["published"]),
    }
    if verbose:
        print(
            f"  public/data: {summary['csv_files']} CSVs, {csv_rows:,} rollup rows, "
            f"{summary['published_stations']}/{summary['stations']} stations with channels"
        )
    return summary

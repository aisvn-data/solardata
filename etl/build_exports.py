"""Export rollups for the website.

The frontend cannot read SQLite or Parquet, and shipping 740k rows to a browser
is not an option.  This writes small CSV files under ``public/data/``, which Vite
serves as static assets, so the site works from a plain clone with no server.

Layout::

    public/data/stations.json                  station metadata + coverage
    public/data/{station}/daily/{year}.csv     ~4 rows/month, tiny
    public/data/quality.json                   the quality report, for the inspector

Non-production stations (``test``, ``voltage-phumy``) are excluded from the
rollups by default: they are bench data and a WiFi probe, not solar production,
and mixing them into a public chart would be wrong.  They still appear in
``quality.json``, which is the internal view.

``quality.json`` is written from the same ``etl.report.collect`` call the
Markdown report uses, so the browser and the committed report cannot drift.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from etl import stations

DAILY_COLUMNS = (
    "day",
    "ts_utc_day",
    "n_samples",
    "n_out_of_range",
    "n_hours",
    "solar_v_avg",
    "solar_v_max",
    "solar2_v_avg",
    "solar2_v_max",
    "battery_v_min",
    "battery_v_max",
    "battery2_v_min",
    "battery2_v_max",
    "power_w_avg",
    "power_w_max",
    "energy_wh",
    "temp_c_min",
    "temp_c_avg",
    "temp_c_max",
    "scaled_channels",
)

HOURLY_COLUMNS = (
    "ts_utc",
    "n_samples",
    "n_out_of_range",
    "solar_v_avg",
    "solar_v_max",
    "solar2_v_avg",
    "solar2_v_max",
    "battery_v_avg",
    "battery_v_min",
    "battery2_v_min",
    "power_w_avg",
    "power_w_max",
    "temp_c_avg",
    "current_a_avg",
    "energy_wh",
    "scaled_channels",
)


def _round(value, places: int = 3):
    """Round floats for a tidy CSV; pass through text, dates and NULLs."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return round(float(value), places)
    return value


def _write_csv(path: Path, columns, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        count = 0
        for row in rows:
            writer.writerow([_round(row[c]) for c in columns])
            count += 1
    return count


def build(
    conn: sqlite3.Connection,
    target: Path,
    *,
    include_hourly: bool = False,
    include_non_production: bool = False,
    verbose: bool = True,
) -> dict[str, int]:
    # Bench data is excluded by default, not because it is low quality but
    # because it is not solar production: `test` is a WiFi/temperature probe
    # mixed with solar channels, and `voltage-phumy` is an ADC calibration
    # sheet.  Publishing either on a public chart would misrepresent the data.
    published = [
        s
        for s in stations.STATIONS
        if include_non_production or s.station_id not in stations.NON_PRODUCTION
    ]
    target.mkdir(parents=True, exist_ok=True)

    written = {"daily": 0, "hourly": 0, "manifest": 0, "quality": 0}

    for station in published:
        for granularity, table, columns in (
            ("daily", "readings_daily", DAILY_COLUMNS),
            ("hourly", "readings_hourly", HOURLY_COLUMNS),
        ):
            if granularity == "hourly" and not include_hourly:
                continue
            # Daily rows are bucketed by local calendar day, hourly by UTC, so
            # the year is taken from a different column in each case.  Getting
            # this wrong splits a year across three files at the offset.
            year_expr = "substr(day, 1, 4)" if granularity == "daily" else "substr(ts_utc, 1, 4)"
            years = conn.execute(
                f"SELECT DISTINCT {year_expr} AS y FROM {table} WHERE station_id = ? ORDER BY y",
                (station.station_id,),
            ).fetchall()
            for row in years:
                rows = conn.execute(
                    f"SELECT {', '.join(columns)} FROM {table}"
                    f" WHERE station_id = ? AND {year_expr} = ?"
                    " ORDER BY 1",
                    (station.station_id, row["y"]),
                ).fetchall()
                if not rows:
                    continue
                path = target / station.station_id / granularity / f"{row['y']}.csv"
                written[granularity] += _write_csv(path, columns, rows)
                if verbose:
                    print(
                        f"  {station.station_id:<14} {granularity:<7} {row['y']}"
                        f"  {len(rows):>6} rows"
                    )

    # The station manifest the UI renders its picker from.  `source_dirs` is a
    # JSON array in the database because three folders make up one station;
    # unpack it here so the browser gets a real array.
    manifest = []
    for row in conn.execute("SELECT * FROM stations ORDER BY is_production DESC, station_id"):
        record = dict(row)
        record["source_dirs"] = json.loads(record["source_dirs"])
        record["published"] = record["station_id"] in {s.station_id for s in published}
        # Years with published rollups, so the UI can build its year selector
        # without probing for 404s.
        record["years"] = [
            r["y"]
            for r in conn.execute(
                "SELECT DISTINCT substr(day, 1, 4) AS y FROM readings_daily"
                " WHERE station_id = ? ORDER BY y",
                (record["station_id"],),
            )
            if record["published"]
        ]
        manifest.append(record)
    path = target / "stations.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    written["manifest"] = len(manifest)

    # The quality report, for the inspector view.  Reusing report.collect() is
    # the point: the JSON the browser reads and the Markdown committed to the
    # repository come from one code path and cannot disagree.
    from etl.report import collect

    quality = target / "quality.json"
    quality.write_text(json.dumps(collect(conn), indent=2, default=str), encoding="utf-8")
    written["quality"] = 1

    if verbose:
        print(
            f"  wrote {written['daily']} daily rows, {written['hourly']} hourly rows, "
            f"{written['manifest']} station records, quality.json"
        )
    return written

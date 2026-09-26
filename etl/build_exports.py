"""Export rollups for the website.

The frontend cannot read SQLite or Parquet, and shipping 740k rows to a browser
is not an option.  This writes small CSV files under ``public/data/``, which Vite
serves as static assets, so the site works from a plain clone with no server.

Layout::

    public/data/stations.json                  station metadata + coverage
    public/data/metrics.json                   the plausibility bands, for the UI
    public/data/{station}/hourly/{year}.csv    ~30 rows/day
    public/data/{station}/daily/{year}.csv     ~4 rows/month, tiny
    public/data/quality.json                   the quality report, for the inspector

Granularity
-----------
Both ``hourly`` and ``daily`` are published by default.  The daily rollup is
derived from the hourly one, so the two cannot disagree, and 1.7 MB of CSV
across 13 station-years buys the site a real intraday curve instead of only
24-hour means.  ``--granularity`` narrows the run if a slim export is wanted.

There is deliberately no raw option.  The archive's native cadence is 119 s,
which is 734,908 rows; a browser cannot be handed that as static files, and the
Parquet export under ``data/processed/parquet`` already is the full-fidelity copy
for anyone who can run a query.

Why ``metrics.json`` exists
---------------------------
The chart has to decide which values to distrust, and the honest source for that
decision is the same plausibility band the pipeline already applies to every raw
cell in ``etl.normalize.metrics``.  Shipping the bands rather than retyping them
in JavaScript keeps one source of truth: if a band is corrected in Python, the
site follows on the next export.  The browser applies the band to the aggregate
it is drawing, which is the same criterion applied one level up -- it never
guesses a threshold of its own, and it never removes a value (``AGENTS.md``
rule 2).

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
from etl.normalize import metrics

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
    "boot_count_max",
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
    "boot_count_min",
    "boot_count_max",
    "scaled_channels",
)

#: ``setting value -> (directory, table, columns, key column)``.
#:
#: The key column differs because the two rollups are bucketed differently:
#: ``readings_daily.day`` is a local calendar day, ``readings_hourly.ts_utc`` a
#: UTC hour.  Taking the year from the wrong one splits a year across two files
#: at the UTC offset, which is a bug that is invisible until someone notices a
#: January reading in the previous year's file.
#: Setting value -> the directories to publish.  ``both`` is the default: the
#: site picks a resolution at runtime, and the daily rollup is derived from the
#: hourly one, so publishing both costs ~1.7 MB and cannot produce a
#: disagreement between the two views.  Narrow it only to keep a slim export.
GRANULARITIES: dict[str, tuple[str, ...]] = {
    "both": ("daily", "hourly"),
    "hour": ("hourly",),
    "day": ("daily",),
}

#: Directory name -> (table, columns, key column).
#:
#: The key column differs because the two rollups are bucketed differently:
#: ``readings_daily.day`` is a local calendar day, ``readings_hourly.ts_utc`` a
#: UTC hour.  Taking the year from the wrong one splits a year across two files
#: at the UTC offset, which is a bug that is invisible until someone notices a
#: January reading in the previous year's file.
ROLLUPS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "hourly": ("readings_hourly", HOURLY_COLUMNS, "ts_utc"),
    "daily": ("readings_daily", DAILY_COLUMNS, "day"),
}


def _write_metrics_manifest(target: Path) -> int:
    """Publish the plausibility bands the chart uses to distrust a value.

    Sourced from ``etl.normalize.metrics`` rather than restated, so the browser
    applies the identical criterion the ingest applied to each raw cell.  A
    channel with no band (``None``/``None``) is still listed, with null bounds,
    because "this one is never flagged" is an answer the UI needs to be able to
    give rather than infer from a missing key.
    """
    payload = {
        "note": (
            "Plausibility bands, verbatim from etl/normalize/metrics.py. A reading "
            "outside its channel's band is flagged out_of_range by the pipeline and "
            "kept; the site marks such a point on the chart and never removes it. "
            "A value is NOT evidence that the sensor is wrong -- the bands are set "
            "from what the hardware produced, so a band that is wrong for the site "
            "it is installed in will flag real readings."
        ),
        "bands": {
            m.column: {
                "unit": m.unit,
                "lo": m.lo,
                "hi": m.hi,
                "kind": m.kind,
                "description": m.description,
            }
            for m in metrics.METRICS
            if m.kind != "text"
        },
    }
    path = target / "metrics.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return len(payload["bands"])


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
    granularity: str = "both",
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
    if granularity not in GRANULARITIES:
        raise ValueError(
            f"unknown granularity {granularity!r}; expected one of {sorted(GRANULARITIES)}"
        )
    selected = GRANULARITIES[granularity]
    target.mkdir(parents=True, exist_ok=True)

    written = {"hourly": 0, "daily": 0, "manifest": 0, "quality": 0, "metrics": 0}

    for station in published:
        for folder in selected:
            table, columns, key = ROLLUPS[folder]
            years = conn.execute(
                f"SELECT DISTINCT substr({key}, 1, 4) AS y FROM {table}"
                " WHERE station_id = ? ORDER BY y",
                (station.station_id,),
            ).fetchall()
            for row in years:
                rows = conn.execute(
                    f"SELECT {', '.join(columns)} FROM {table}"
                    f" WHERE station_id = ? AND substr({key}, 1, 4) = ?"
                    f" ORDER BY {key}",
                    (station.station_id, row["y"]),
                ).fetchall()
                if not rows:
                    continue
                path = target / station.station_id / folder / f"{row['y']}.csv"
                written[folder] += _write_csv(path, columns, rows)
                if verbose:
                    print(f"  {station.station_id:<14} {folder:<7} {row['y']}  {len(rows):>6} rows")

    # The station manifest the UI renders its picker from.  `source_dirs` is a
    # JSON array in the database because three folders make up one station;
    # unpack it here so the browser gets a real array.
    manifest = []
    for row in conn.execute("SELECT * FROM stations ORDER BY is_production DESC, station_id"):
        record = dict(row)
        record["source_dirs"] = json.loads(record["source_dirs"])
        record["published"] = record["station_id"] in {s.station_id for s in published}
        # Which rollups actually exist for this station, so the resolution switch
        # offers only what is on disk instead of 404-ing on a missing file.
        record["granularities"] = [
            folder
            for folder in selected
            if conn.execute(
                f"SELECT 1 FROM {ROLLUPS[folder][0]} WHERE station_id = ? LIMIT 1",
                (record["station_id"],),
            ).fetchone()
        ]
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

    written["metrics"] = _write_metrics_manifest(target)

    # The quality report, for the inspector view.  Reusing report.collect() is
    # the point: the JSON the browser reads and the Markdown committed to the
    # repository come from one code path and cannot disagree.
    from etl.report import collect

    quality = target / "quality.json"
    quality.write_text(json.dumps(collect(conn), indent=2, default=str), encoding="utf-8")
    written["quality"] = 1

    if verbose:
        print(
            f"  wrote {written['hourly']} hourly rows, {written['daily']} daily rows, "
            f"{written['manifest']} station records, "
            f"{written['metrics']} metric bands, quality.json"
        )
    return written

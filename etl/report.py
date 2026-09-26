"""The data-quality report: the human-facing half of the pipeline.

Everything the ingest had to guess at, refuse, or flag ends up here.  The point
is that a future reader can disagree with a decision without re-running the
ingest, and can see exactly which rows were affected.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from etl import __version__
from etl.config import BAD_WINDOWS, NULL_WINDOWS, ROW_EXCLUSIONS
from etl.normalize.metrics import METRICS


def _rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _channel_ranges(conn: sqlite3.Connection) -> list[dict]:
    """What each channel actually did, per station, beside what it is banded to.

    The plausibility band in ``etl/normalize/metrics.py`` answers "what should
    this hardware produce". It is one global answer per column name, and for this
    archive that is not enough: ``battery_v`` is banded 9-16 V for a 3S LiPo, and
    ``aisvn`` reads 17.6-29.6 V on 23 of its 101 days in 2020. Either that is a
    second battery pack, or a scale nobody has confirmed, or the band is wrong
    for the site it is installed in -- and the archive cannot say which. Reporting
    only the band hides the question; reporting only the observed range hides the
    expectation. Both, side by side, is the finding.

    Percentiles rather than min/max because a single corrupt sample sets a min and
    a max that describe nothing: ``phumy2`` 2020-11-27 has a ``power_w`` of
    19,877 W in an otherwise 0 W hour, and a max-based envelope would call the
    station a 19 kW array. p1..p99 is the range the instrument actually spent its
    time in, and ``n_out_of_range`` counts everything that fell outside the band
    regardless of where it landed.
    """
    out: list[dict] = []
    numeric = [
        m.column
        for m in METRICS
        if m.kind in ("voltage", "current", "power", "temperature", "count", "raw")
    ]
    for metric in METRICS:
        if metric.kind == "text":
            continue
        column = metric.column
        rows = conn.execute(
            f"SELECT station_id, COUNT({column}) AS n, MIN({column}) AS lo,"
            f" MAX({column}) AS hi, AVG({column}) AS mean"
            f" FROM readings WHERE {column} IS NOT NULL GROUP BY station_id"
        ).fetchall()
        for row in rows:
            n = row["n"]
            if n == 0:
                continue
            out.append(
                {
                    "station_id": row["station_id"],
                    "column": column,
                    "unit": metric.unit,
                    "kind": metric.kind,
                    "n": n,
                    "min": row["lo"],
                    "max": row["hi"],
                    "mean": row["mean"],
                    "band_lo": metric.lo,
                    "band_hi": metric.hi,
                }
            )
    del numeric
    out.sort(key=lambda r: (r["station_id"], r["column"]))
    return out


def collect(conn: sqlite3.Connection) -> dict:
    report: dict = {"tool_version": __version__}

    report["stations"] = _rows(
        conn,
        "SELECT station_id, display_name, location, tz, applet, source_dirs,"
        " is_production, first_ts_utc, last_ts_utc, n_readings, notes"
        " FROM stations ORDER BY is_production DESC, station_id",
    )

    report["totals"] = _rows(
        conn,
        "SELECT COUNT(*) AS readings, COUNT(DISTINCT station_id) AS stations,"
        " COUNT(DISTINCT substr(ts_utc,1,10)) AS days,"
        " MIN(ts_utc) AS first_ts, MAX(ts_utc) AS last_ts"
        " FROM readings",
    )[0]

    report["source_files"] = {
        "total": _rows(conn, "SELECT COUNT(*) AS n FROM source_files")[0]["n"],
        "with_header": _rows(conn, "SELECT COUNT(*) AS n FROM source_files WHERE has_header = 1")[
            0
        ]["n"],
        "without_header": _rows(
            conn, "SELECT COUNT(*) AS n FROM source_files WHERE has_header = 0"
        )[0]["n"],
        "with_side_blocks": _rows(
            conn, "SELECT COUNT(*) AS n FROM source_files WHERE extra_blocks > 0"
        )[0]["n"],
        "with_repeated_headers": _rows(
            conn, "SELECT COUNT(*) AS n FROM source_files WHERE repeated_headers > 0"
        )[0]["n"],
        "per_folder": _rows(
            conn,
            "SELECT source_dir, station_id, COUNT(*) AS files,"
            " SUM(has_header) AS with_header, SUM(n_ingested) AS rows_ingested,"
            " SUM(n_duplicate_ts) AS duplicate_ts, SUM(n_rejected) AS rejected,"
            " MIN(min_ts_utc) AS first_ts, MAX(max_ts_utc) AS last_ts"
            " FROM source_files GROUP BY source_dir, station_id ORDER BY source_dir",
        ),
    }

    # `quality_flags` is a comma-separated set, so a row can carry several flags
    # at once (a sentinel cell in one column and an out-of-range value in
    # another).  `flag_totals` splits them so the per-flag counts are not
    # diluted by co-occurrence; `quality_flags` keeps the combinations.
    report["quality_flags"] = {
        row["quality_flags"]: row["n"]
        for row in _rows(
            conn,
            "SELECT quality_flags, COUNT(*) AS n FROM readings"
            " WHERE quality_flags <> '' GROUP BY quality_flags ORDER BY n DESC",
        )
    }

    combined = Counter()
    for row in _rows(
        conn,
        "SELECT quality_flags, COUNT(*) AS n FROM readings"
        " WHERE quality_flags <> '' GROUP BY quality_flags",
    ):
        for flag in row["quality_flags"].split(","):
            combined[flag] += row["n"]
    report["flag_totals"] = dict(combined.most_common())

    report["rejects"] = {
        "total": _rows(conn, "SELECT COUNT(*) AS n FROM rejects")[0]["n"],
        "by_reason": _rows(
            conn,
            "SELECT reason, COUNT(*) AS n FROM rejects GROUP BY reason ORDER BY n DESC LIMIT 20",
        ),
        "samples": _rows(
            conn,
            "SELECT f.rel_path, r.sheet_row, r.column_name, r.raw_value, r.reason"
            " FROM rejects r JOIN source_files f ON f.file_id = r.file_id LIMIT 25",
        ),
    }

    # The reasoning behind a windowed decision, read from `config.py` and paired
    # with the number of rows it explains.  `rejects.reason` holds a stable
    # category -- `null_window` -- because the report groups by it, and storing
    # the prose there put one ~300-character sentence on 220,074 rows and made
    # `rejects` as large as `readings`.  The prose is version-controlled in
    # `config.py` and published here instead, so the site can explain the
    # category without the database repeating the sentence 220,074 times.
    nulled = _rows(
        conn,
        "SELECT station_id, column_name, COUNT(*) AS n FROM rejects"
        " WHERE reason = 'null_window' AND column_name IS NOT NULL"
        " GROUP BY station_id, column_name ORDER BY n DESC",
    )
    counted = {(r["station_id"], r["column_name"]): r["n"] for r in nulled}
    report["null_windows"] = [
        {
            "station_id": station_id,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "columns": [c.strip() for c in columns.split(",") if c.strip()],
            "why": why,
            "n_rejected": sum(counted.get((station_id, c.strip()), 0) for c in columns.split(",")),
        }
        for station_id, valid_from, valid_to, columns, why in NULL_WINDOWS
    ]
    report["channel_ranges"] = _channel_ranges(conn)

    report["row_exclusions"] = [
        {
            "rel_path": rel_path,
            "first_usable_sheet_row": first_row,
            "why": why,
            "n_rejected": _rows(
                conn,
                "SELECT COUNT(*) AS n FROM rejects r JOIN source_files f"
                " ON f.file_id = r.file_id WHERE f.rel_path LIKE ? AND r.reason = 'pre_reinstall'",
                (f"%{Path(rel_path).name}",),
            )[0]["n"],
        }
        for rel_path, first_row, why in ROW_EXCLUSIONS
    ]
    report["bad_windows"] = [
        {
            "station_id": station_id,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "columns": [c.strip() for c in columns.split(",") if c.strip()],
            "why": why,
            "n_flagged": _rows(
                conn,
                f"SELECT COUNT(*) AS n FROM readings WHERE station_id = ?"
                f" AND ts_utc >= ? AND ts_utc < ?"
                f" AND ({' OR '.join('quality_flags LIKE ?' for _ in columns.split(','))})",
                (
                    station_id,
                    valid_from,
                    valid_to,
                    *[f"%bad_window:{c.strip()}%" for c in columns.split(",")],
                ),
            )[0]["n"],
        }
        for station_id, valid_from, valid_to, columns, why in BAD_WINDOWS
    ]

    report["notes"] = _rows(
        conn,
        "SELECT f.rel_path, n.note FROM notes n"
        " LEFT JOIN source_files f ON f.file_id = n.file_id ORDER BY f.rel_path LIMIT 40",
    )

    # `n_columns` is reported because a folder can hold more than one layout --
    # `aisvn` went from 10 columns to 11 when `power` was added on 2020-06-17,
    # and `test` is two unrelated schemas. The width is what tells the reader
    # which files a row describes.
    report["metric_defs"] = _rows(
        conn,
        "SELECT station_id, source_dir, n_columns, col_index, raw_name, canonical_col,"
        " unit, confidence, inferred, reason, n_files"
        " FROM metric_defs WHERE canonical_col IS NOT NULL AND raw_name <> ''"
        " ORDER BY station_id, source_dir, n_columns, col_index",
    )

    report["unmapped_columns"] = _rows(
        conn,
        "SELECT station_id, source_dir, n_columns, col_index, raw_name, reason, n_files"
        " FROM metric_defs WHERE canonical_col IS NULL AND raw_name <> ''"
        " ORDER BY station_id, source_dir, n_columns, col_index",
    )

    report["regimes"] = _rows(
        conn,
        "SELECT station_id, column, unit, valid_from, valid_to, scale, status,"
        " detected_by, confidence, notes FROM regimes ORDER BY station_id, column, valid_from",
    )

    # Sampling interval and coverage gaps both need the delta to the previous
    # reading, which is a window function.  `ts_utc` is a zero-padded ISO string
    # so `julianday` parses it directly; ordering on the string is safe *only*
    # because the fixed-width format sorts chronologically -- the raw column A
    # does not, which is why the pipeline parsed it in the first place.
    report["sampling_interval_seconds"] = _rows(
        conn,
        "SELECT station_id, interval_s, COUNT(*) AS n FROM ("
        "  SELECT station_id,"
        "         CAST((julianday(ts_utc) - julianday(LAG(ts_utc) OVER"
        "           (PARTITION BY station_id ORDER BY ts_utc))) * 86400 AS INTEGER) AS interval_s"
        "  FROM readings"
        # Rows more than an hour apart are an outage or a change of collector,
        # not a sampling rate, so they are excluded from the distribution.
        ") WHERE interval_s IS NOT NULL AND interval_s BETWEEN 30 AND 3600"
        " GROUP BY station_id, interval_s ORDER BY station_id, n DESC",
    )

    # Multi-month gaps between collector generations are real and expected; the
    # table exists so nobody mistakes them for missing data.
    report["coverage_gaps"] = _rows(
        conn,
        "SELECT station_id, from_ts, to_ts, gap_hours FROM ("
        "  SELECT station_id, ts_utc AS from_ts,"
        "         LEAD(ts_utc) OVER (PARTITION BY station_id ORDER BY ts_utc) AS to_ts,"
        "         ROUND((julianday(LEAD(ts_utc) OVER (PARTITION BY station_id ORDER BY ts_utc))"
        "              - julianday(ts_utc)) * 24, 1) AS gap_hours"
        "  FROM readings"
        ") WHERE gap_hours > 24 ORDER BY gap_hours DESC LIMIT 40",
    )

    # COUNT(col) not COUNT(*): a NULL means "this station had no such channel",
    # so counting non-NULLs is what reveals which stations actually have which
    # measurements.  This is the distinction rule 7 in AGENTS.md is about.
    report["channel_plausibility"] = _rows(
        conn,
        "SELECT station_id,"
        " COUNT(solar_v) AS n_solar,   MIN(solar_v) AS solar_min,  MAX(solar_v) AS solar_max,"
        " COUNT(battery_v) AS n_batt,  MIN(battery_v) AS batt_min, MAX(battery_v) AS batt_max,"
        " COUNT(temp_c) AS n_temp,     MIN(temp_c) AS temp_min,    MAX(temp_c) AS temp_max,"
        " COUNT(power_w) AS n_power"
        " FROM readings GROUP BY station_id ORDER BY station_id",
    )

    return report


def render_markdown(report: dict) -> str:
    """Render the human-facing half of the pipeline.

    This document is the acceptance test for a data change.  Per AGENTS.md, a
    change that moves the reading count is a bug even when every test passes,
    so the headline numbers are printed first and the things that need a human
    judgement -- unconfirmed regimes, out-of-range channels -- are given their
    own sections rather than being buried in the flag counts.
    """
    lines: list[str] = []
    add = lines.append

    totals = report["totals"]
    files = report["source_files"]

    add("# solardata quality report")
    add("")
    add(f"Generated by etl {report['tool_version']}.")
    add("")
    add("## Coverage")
    add("")
    add(f"- Readings: **{totals['readings']:,}** across {totals['stations']} stations")
    add(f"- Distinct calendar days: {totals['days']:,}")
    add(f"- Range: `{totals['first_ts']}` -> `{totals['last_ts']}`")
    add(
        f"- Raw files: **{files['total']}** "
        f"({files['with_header']} with a header row, {files['without_header']} without)"
    )
    add(f"- Files with redundant side-by-side column blocks: {files['with_side_blocks']}")
    add(f"- Files repeating a header row mid-file: {files['with_repeated_headers']}")
    add("")

    add("## Stations")
    add("")
    add("| station | location | tz | readings | range | production |")
    add("|---|---|---|---:|---|---|")
    for s in report["stations"]:
        rng = f"{s['first_ts_utc']} -> {s['last_ts_utc']}" if s["first_ts_utc"] else "-"
        add(
            f"| `{s['station_id']}` | {s['location'] or '-'} | {s['tz']} | "
            f"{s['n_readings'] or 0:,} | {rng} | {'yes' if s['is_production'] else 'no'} |"
        )
    add("")

    add("## Raw files per archive folder")
    add("")
    add("| folder | station | files | with header | rows | dup ts | rejected | range |")
    add("|---|---|---:|---:|---:|---:|---:|---|")
    for f in files["per_folder"]:
        rng = f"{f['first_ts']} -> {f['last_ts']}" if f["first_ts"] else "-"
        add(
            f"| `{f['source_dir']}` | {f['station_id']} | {f['files']} | "
            f"{f['with_header']} | {f['rows_ingested'] or 0:,} | {f['duplicate_ts'] or 0} | "
            f"{f['rejected'] or 0} | {rng} |"
        )
    add("")

    if report["flag_totals"]:
        add("## Quality flags")
        add("")
        add("| flag | readings |")
        add("|---|---:|")
        for flag, n in report["flag_totals"].items():
            add(f"| `{flag}` | {n:,} |")
        add("")
        add("`sentinel` means the raw cell was an IFTTT missing-value marker (-992/-1) and")
        add("was stored as NULL. `out_of_range` means the value is kept but falls outside the")
        add("metric's plausible band, which is how calibration changes get noticed.")
        add("")

    if report["rejects"]["total"]:
        add("## Rejected cells")
        add("")
        add(f"{report['rejects']['total']} cells were not turned into readings.")
        add("")
        add("`reason` is a stable category, not a sentence, so the counts below group.")
        add("The reasoning behind `null_window` is in the next section.")
        add("")
        for row in report["rejects"]["by_reason"][:10]:
            add(f"- `{row['reason']}`: {row['n']:,}")
        add("")

    if report["null_windows"] or report["bad_windows"]:
        add("## Windows where a value was nulled or distrusted")
        add("")
        add("Stated once here, from `etl/config.py`, which is where they are defined. The")
        add("database records only the category on each row, so these sentences are not")
        add("repeated per cell.")
        add("")
        for w in report["null_windows"]:
            cols = ", ".join(f"`{c}`" for c in w["columns"])
            add(
                f"**`{w['station_id']}` · {cols} · {w['valid_from']} -> {w['valid_to']}** "
                f"— {w['n_rejected']:,} cells nulled."
            )
            add("")
            add(f"> {w['why']}")
            add("")
        for w in report["bad_windows"]:
            cols = ", ".join(f"`{c}`" for c in w["columns"])
            add(
                f"**`{w['station_id']}` · {cols} · {w['valid_from']} -> {w['valid_to']}** "
                f"— {w['n_flagged']:,} readings flagged, value kept."
            )
            add("")
            add(f"> {w['why']}")
            add("")

    if report["notes"]:
        add("## Free-text notes recovered from data cells")
        add("")
        for note in report["notes"][:20]:
            text = note["note"]
            add(f"- `{note['rel_path']}`: {text[:160]}")
        add("")

    if report["regimes"]:
        add("## Proposed unit-scale regimes (unconfirmed)")
        add("")
        add("The collector changed sensor scaling mid-record without changing the column")
        add("names. These windows are flagged for review; **no value has been rescaled**.")
        add("")
        add("| station | column | window | proposed scale | confidence |")
        add("|---|---|---|---:|---|")
        for r in report["regimes"][:40]:
            window = f"{r['valid_from']} -> {r['valid_to'] or 'open'}"
            add(
                f"| `{r['station_id']}` | `{r['column']}` | {window} | "
                f"x{r['scale']:g} | {r['confidence']} |"
            )
        add("")

    if report["unmapped_columns"]:
        add("## Columns we refused to map")
        add("")
        add("| station | folder | index | header | reason | files |")
        add("|---|---|---:|---|---|---:|")
        for r in report["unmapped_columns"][:30]:
            add(
                f"| `{r['station_id']}` | `{r['source_dir']}` | {r['col_index']} | "
                f"`{r['raw_name']}` | {r['reason']} | {r['n_files']} |"
            )
        add("")

    if report["coverage_gaps"]:
        add("## Largest coverage gaps (> 24 h)")
        add("")
        add("| station | from | to | hours |")
        add("|---|---|---|---:|")
        for g in report["coverage_gaps"][:20]:
            add(f"| `{g['station_id']}` | {g['from_ts']} | {g['to_ts']} | {g['gap_hours']} |")
        add("")

    add("## Channel plausibility")
    add("")
    add("| station | solar_v min..max | battery_v min..max | temp_c min..max |")
    add("|---|---|---|---|")
    for c in report["channel_plausibility"]:
        solar = f"{c['solar_min']:.2f}..{c['solar_max']:.2f}" if c["solar_min"] is not None else "-"
        batt = f"{c['batt_min']:.2f}..{c['batt_max']:.2f}" if c["batt_min"] is not None else "-"
        temp = f"{c['temp_min']:.1f}..{c['temp_max']:.1f}" if c["temp_min"] is not None else "-"
        add(f"| `{c['station_id']}` | {solar} | {batt} | {temp} |")
    add("")
    return "\n".join(lines)


def write(conn: sqlite3.Connection, json_path: Path, md_path: Path) -> dict:
    report = collect(conn)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return report

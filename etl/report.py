"""The per-station data-quality report, in Markdown and JSON.

The report is the review artefact: it is committed, so a change to the data
shows up as a readable diff in a pull request rather than as a number somebody
has to go and look for.  Its structure follows the shape of the pipeline.

Per station
    What that station collects, in what unit, banded how, and what it actually
    recorded: start, stop, count, min, max, mean, the 1st/50th/99th percentiles,
    the zeros, the sentinels, the out-of-range count.  Then the channels the
    station records but the site does not show, each with the reason and the
    sentence explaining it -- a reader who notices a missing channel can then
    find out why it is missing rather than guessing.

Per build
    The rejects by category, the windows, the notes, the coverage, and the band
    audit.

The band audit
--------------
For every (station, channel) with a band, the report prints how much of that
channel's own record the band rejects.  That number is the check that 0.8 could
not make: a band that fires on 20% of a channel is not detecting contamination,
it is reporting a unit mismatch, and there were hundreds of thousands of those
in the archive.  ``etl.audit`` turns the same number into a build failure, so it
cannot regress quietly; here it is printed so a human can see it move.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import __version__, build_aggregate, catalog
from .config import (
    ALL_FLAGS,
    BAD_WINDOWS,
    FILE_EXCLUSIONS,
    FLAG_NAMES,
    NULL_WINDOWS,
    REASONS,
    ROW_EXCLUSIONS,
    Settings,
)

__all__ = ["collect", "render_markdown", "write"]


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]


def _fmt(value: object, places: int = 4) -> str:
    """One number for a table cell, or ``--`` for a gap.

    Defensive about a non-number rather than assuming one, because a text channel
    has no statistics and the row it produces should read ``--`` rather than
    raise.  SQLite will return the alphabetically first event name for
    ``MIN(event)``, which is a number-shaped answer to a question nobody asked.
    """
    if value is None:
        return "--"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value != value:  # NaN
            return "--"
        if abs(value) >= 1000:
            return f"{value:,.{max(0, places - 2)}f}"
        return f"{value:.{places}g}"
    return str(value)


def _band(ch: dict) -> str:
    lo, hi = ch.get("band_lo"), ch.get("band_hi")
    if lo is None and hi is None:
        return "--"
    return f"{_fmt(lo, 3)} to {_fmt(hi, 3)}"


def _fire_fraction(ch: dict) -> float:
    observed = ch.get("observed")
    if not observed or not observed["n_values"]:
        return 0.0
    if ch.get("band_lo") is None and ch.get("band_hi") is None:
        return 0.0
    return observed["n_out_of_range"] / observed["n_values"]


def _window_rows(conn: sqlite3.Connection, station_id: str, columns: tuple[str, ...]) -> int:
    """How many cells a null window actually nulled, for the station's channels.

    Counted from the rejects table, keyed on the channel, so the number in the
    report is the number of cells that changed rather than a remembered figure.
    """
    placeholders = ", ".join("?" * len(columns))
    return conn.execute(
        f"SELECT COUNT(*) FROM rejects WHERE station_id = ? AND reason = 'null_window'"
        f" AND column_name IN ({placeholders})",
        (station_id, *columns),
    ).fetchone()[0]


def collect(conn: sqlite3.Connection) -> dict:
    """The whole report, as a JSON-serialisable dict."""
    stations = []
    for row in conn.execute("SELECT * FROM stations ORDER BY is_production DESC, station_id"):
        sid = row["station_id"]
        channels = _rows(
            conn,
            "SELECT * FROM station_channels WHERE station_id = ? ORDER BY rowid",
            (sid,),
        )
        observed = {
            r["channel"]: r
            for r in _rows(
                conn,
                "SELECT * FROM channel_stats WHERE station_id = ?",
                (sid,),
            )
        }
        for ch in channels:
            ch["observed"] = observed.get(ch["channel"])
            ch["fire_fraction"] = _fire_fraction(ch)
        rejects = _rows(
            conn,
            "SELECT reason, COUNT(*) AS n FROM rejects WHERE station_id = ?"
            " GROUP BY reason ORDER BY n DESC",
            (sid,),
        )
        flag_rows = _rows(
            conn,
            f"SELECT flags, COUNT(*) AS n FROM {catalog.BY_ID[sid].table}"
            " WHERE flags <> '' GROUP BY flags ORDER BY n DESC",
        )
        stations.append(
            {
                "station_id": sid,
                "display_name": row["display_name"],
                "location": row["location"],
                "tz": row["tz"],
                "applet": row["applet"],
                "source_dirs": json.loads(row["source_dirs"]),
                "is_production": bool(row["is_production"]),
                "group": row["published_group"],
                "notes": row["notes"],
                "first_ts_utc": row["first_ts_utc"],
                "last_ts_utc": row["last_ts_utc"],
                "n_readings": row["n_readings"],
                "table": row["table_name"],
                "channels": channels,
                "published_channels": [c["channel"] for c in channels if c["published"]],
                "hidden_channels": [
                    {
                        "channel": c["channel"],
                        "label": c["label"],
                        "reason": c["exclude_reason"],
                        "note": c["exclude_note"],
                        "n_values": (c["observed"] or {}).get("n_values"),
                        "min": (c["observed"] or {}).get("min"),
                        "max": (c["observed"] or {}).get("max"),
                    }
                    for c in channels
                    if not c["published"]
                ],
                "rejects": rejects,
                "flag_totals": _flag_totals(flag_rows),
                "open_questions": list(catalog.BY_ID[sid].open_questions),
            }
        )

    return {
        "tool_version": __version__,
        "totals": {
            "readings": sum(s["n_readings"] or 0 for s in stations),
            "stations": len(stations),
            "files": conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0],
            "rejects": conn.execute("SELECT COUNT(*) FROM rejects").fetchone()[0],
            "notes": conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
            "excluded_files": conn.execute(
                "SELECT COUNT(*) FROM source_files WHERE excluded IS NOT NULL"
            ).fetchone()[0],
            "hourly_buckets": conn.execute("SELECT COUNT(*) FROM readings_hourly").fetchone()[0],
            "daily_buckets": conn.execute("SELECT COUNT(*) FROM readings_daily").fetchone()[0],
        },
        "stations": stations,
        "rejects_by_reason": _rows(
            conn, "SELECT reason, COUNT(*) AS n FROM rejects GROUP BY reason ORDER BY n DESC"
        ),
        "flag_totals": _global_flag_totals(conn),
        "notes": _rows(
            conn,
            "SELECT station_id, ts_utc, column_name, note FROM notes ORDER BY station_id, note_id",
        ),
        "source_files": _rows(
            conn,
            "SELECT source_dir, station_id, COUNT(*) AS files,"
            " SUM(has_header) AS with_header, SUM(n_ingested) AS n_ingested,"
            " SUM(n_duplicate_ts) AS n_duplicate_ts, MIN(min_ts_utc) AS min_ts_utc,"
            " MAX(max_ts_utc) AS max_ts_utc, SUM(extra_blocks) AS extra_blocks"
            " FROM source_files GROUP BY source_dir, station_id ORDER BY source_dir",
        ),
        "reject_vocabulary": list(REASONS),
        "flag_vocabulary": list(ALL_FLAGS),
        "flag_names": FLAG_NAMES,
        "windows": {
            "null": [
                {
                    "station_id": s,
                    "valid_from": a,
                    "valid_to": b,
                    "columns": list(c),
                    "why": w,
                    "n_rows": _window_rows(conn, s, c),
                }
                for s, a, b, c, w in NULL_WINDOWS
            ],
            "bad": [
                {
                    "station_id": s,
                    "valid_from": a,
                    "valid_to": b,
                    "columns": list(c),
                    "why": w,
                }
                for s, a, b, c, w in BAD_WINDOWS
            ],
        },
        "exclusions": {
            "files": [{"path": p, "why": w} for p, w in FILE_EXCLUSIONS],
            "rows": [{"path": p, "from_row": r, "why": w} for p, r, w in ROW_EXCLUSIONS],
        },
        "coverage": build_aggregate.coverage(conn),
        "band_audit": _band_audit(conn),
    }


def _flag_totals(flag_rows: list[dict]) -> dict[str, int]:
    """Split a comma-joined flag set into per-flag totals.

    Done by reading the set as comma-separated text, which is what the column is.
    The parameterised families are counted whole and keep their ``:channel``
    suffix, so ``no_signal:solar2_v`` is one line rather than a column's worth.
    """
    totals: dict[str, int] = {}
    for row in flag_rows:
        for flag in row["flags"].split(","):
            if flag:
                totals[flag] = totals.get(flag, 0) + row["n"]
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


def _global_flag_totals(conn: sqlite3.Connection) -> dict[str, int]:
    """Per-flag totals across every station.

    A station's table is queried for its own rows, so there is no cross-station
    count to get wrong and a rename in the catalog moves the count with it.
    """
    totals: dict[str, int] = {}
    for station in catalog.STATIONS:
        for flag, n in conn.execute(
            f"SELECT flags, COUNT(*) FROM {station.table} WHERE flags <> '' GROUP BY flags"
        ):
            for one in flag.split(","):
                if one:
                    totals[one] = totals.get(one, 0) + n
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


def _first_sentence(text: str, limit: int = 160) -> str:
    """The opening of a note, for a table cell.

    Split on ``". "`` rather than ``"."``: a note's first sentence contains its
    own decimals -- "Fires on 1,717 of 77,526 readings (2.2%)" -- and splitting on
    a bare full stop truncates it mid-number, which is the one place in this
    report a reader is looking for a figure.
    """
    if not text:
        return ""
    head = text.split(". ", 1)[0].strip()
    if len(head) < limit:
        return head
    return head[: limit - 1].rstrip() + "…"


def _band_audit(conn: sqlite3.Connection) -> dict:
    """How much of each channel's own record its own band rejects."""
    rows = []
    for station in catalog.STATIONS:
        for ch in station.channels:
            if not ch.band:
                continue
            stat = conn.execute(
                "SELECT n_values, n_out_of_range FROM channel_stats"
                " WHERE station_id = ? AND channel = ?",
                (station.station_id, ch.name),
            ).fetchone()
            n_values = stat["n_values"] if stat else 0
            n_oor = stat["n_out_of_range"] if stat else 0
            fraction = (n_oor / n_values) if n_values else 0.0
            rows.append(
                {
                    "station_id": station.station_id,
                    "channel": ch.name,
                    "unit": ch.unit,
                    "band": [ch.band_lo, ch.band_hi],
                    "n_values": n_values,
                    "n_out_of_range": n_oor,
                    "fraction": round(fraction, 6),
                    "over_threshold": fraction > catalog.BAND_FIRE_FRACTION,
                    "justified": bool(ch.band_note.strip()),
                    "note": ch.band_note,
                }
            )
    rows.sort(key=lambda r: -r["fraction"])
    return {
        "threshold": catalog.BAND_FIRE_FRACTION,
        "rows": rows,
        "unjustified": [r for r in rows if r["over_threshold"] and not r["justified"]],
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def render_markdown(report: dict) -> str:
    t = report["totals"]
    out: list[str] = []
    add = out.append

    add(f"# Data quality report -- solardata {report['tool_version']}")
    add("")
    add(
        f"**{t['readings']:,} readings** from {t['files']} raw files across "
        f"{t['stations']} stations. {t['hourly_buckets']:,} hourly and "
        f"{t['daily_buckets']:,} daily rollup buckets. {t['rejects']:,} rejected "
        f"cells, {t['notes']} recovered notes, {t['excluded_files']} file"
        f"{'' if t['excluded_files'] == 1 else 's'} excluded whole."
    )
    add("")
    add(
        "Every station is one table in `solardata.db`, holding only the channels "
        "that station collects. Bands belong to a (station, channel) pair, not to a "
        "column name, and are tested in the unit the value is stored in."
    )
    add("")

    add("## Stations")
    add("")
    add("| Station | Table | Readings | Coverage (UTC) | Channels | Published |")
    add("|---|---|---:|---|---:|---:|")
    for s in report["stations"]:
        add(
            f"| {s['display_name']} | `{s['table']}` | {s['n_readings'] or 0:,} | "
            f"{(s['first_ts_utc'] or '')[:10]} to {(s['last_ts_utc'] or '')[:10]} | "
            f"{len(s['channels'])} | {len(s['published_channels'])} |"
        )
    add("")

    for s in report["stations"]:
        add(f"## {s['display_name']} (`{s['station_id']}`)")
        add("")
        add(f"{s['notes']}")
        add("")
        add(
            f"{s['n_readings'] or 0:,} readings in `{s['table']}`, "
            f"{(s['first_ts_utc'] or '')[:19]}Z to {(s['last_ts_utc'] or '')[:19]}Z, "
            f"timezone {s['tz']}, applet `{s['applet']}`."
        )
        add("")
        add("### What it collects")
        add("")
        add(
            "| Channel | Unit | Band | n | min | p01 | median | mean | p99 | max | "
            "zeros | out of range | shown |"
        )
        add("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|")
        for ch in s["channels"]:
            o = ch["observed"] or {}
            add(
                f"| `{ch['channel']}` | {ch['unit'] or '--'} | {_band(ch)} | "
                f"{o.get('n_values') or 0:,} | {_fmt(o.get('min'))} | "
                f"{_fmt(o.get('p01'))} | {_fmt(o.get('p50'))} | {_fmt(o.get('mean'))} | "
                f"{_fmt(o.get('p99'))} | {_fmt(o.get('max'))} | "
                f"{o.get('n_zero') or 0:,} | {o.get('n_out_of_range') or 0:,} | "
                f"{'yes' if ch['published'] else '**no**'} |"
            )
        add("")

        if s["hidden_channels"]:
            add("### Recorded but not shown")
            add("")
            for h in s["hidden_channels"]:
                add(f"- **`{h['channel']}`** ({h['label']}) -- *{h['reason']}*. {h['note']}")
            add("")

        if s["flag_totals"]:
            add("### Flags")
            add("")
            add("| Flag | Rows |")
            add("|---|---:|")
            for flag, n in s["flag_totals"].items():
                add(f"| `{flag}` | {n:,} |")
            add("")

        if s["rejects"]:
            add("### Rejected cells")
            add("")
            add("| Reason | Cells |")
            add("|---|---:|")
            for r in s["rejects"]:
                add(f"| `{r['reason']}` | {r['n']:,} |")
            add("")

        if s["open_questions"]:
            add("### Open questions")
            add("")
            for q in s["open_questions"]:
                add(f"- {q}")
            add("")

    audit = report["band_audit"]
    add("## Band audit")
    add("")
    add(
        "How much of each channel's own record its own band rejects. A band that "
        f"fires on more than {audit['threshold']:.1%} of a channel is not detecting "
        "contamination, it is reporting a unit mismatch; every one above the line "
        "carries a note saying why the fire is the finding."
    )
    add("")
    add("| Station | Channel | Unit | Band | n | Out of range | Share | Note |")
    add("|---|---|---|---|---:|---:|---:|---|")
    for r in audit["rows"]:
        mark = " **!**" if r["over_threshold"] and not r["justified"] else ""
        add(
            f"| `{r['station_id']}` | `{r['channel']}` | {r['unit'] or '--'} | "
            f"{_fmt(r['band'][0], 3)} to {_fmt(r['band'][1], 3)} | {r['n_values']:,} | "
            f"{r['n_out_of_range']:,} | {r['fraction']:.3%} | {_first_sentence(r['note'])}{mark} |"
        )
    add("")

    add("## Rejected cells by reason")
    add("")
    add("`reason` is a stable category, never a sentence. The prose for each window")
    add("is published once, above and in `config.py`.")
    add("")
    add("| Reason | Cells |")
    add("|---|---:|")
    for r in report["rejects_by_reason"]:
        add(f"| `{r['reason']}` | {r['n']:,} |")
    add("")

    for key, title in (
        ("null", "Windows whose values were nulled"),
        ("bad", "Windows whose values were kept and flagged"),
    ):
        entries = report["windows"][key]
        if not entries:
            continue
        add(f"## {title}")
        add("")
        for w in entries:
            cols = ", ".join(f"`{c}`" for c in w["columns"])
            span = f"{w['valid_from']} to {w['valid_to']}"
            if key == "null":
                add(f"- **{w['station_id']}** {cols}, {span}, {w['n_rows']:,} cells. {w['why']}")
            else:
                add(f"- **{w['station_id']}** {cols}, {span}. {w['why']}")
        add("")

    ex = report["exclusions"]
    if ex["files"] or ex["rows"]:
        add("## Exclusions")
        add("")
        for f in ex["files"]:
            add(f"- **file `{f['path']}`** -- {f['why']}")
        for r in ex["rows"]:
            add(f"- **rows before {r['from_row']} of `{r['path']}`** -- {r['why']}")
        add("")

    add("## Sampling interval")
    add("")
    add("| Station | Readings | Median gap | p90 gap |")
    add("|---|---:|---:|---:|")
    for c in report["coverage"]:
        add(
            f"| `{c['station_id']}` | {c['n_readings']:,} | "
            f"{_fmt(c['median_gap_seconds'], 6)} s | {_fmt(c['p90_gap_seconds'], 6)} s |"
        )
    add("")
    return "\n".join(out) + "\n"


def write(conn: sqlite3.Connection, settings: Settings, *, verbose: bool = True) -> dict:
    report = collect(conn)
    Path(settings.report_md).write_text(render_markdown(report), encoding="utf-8")
    Path(settings.report_json).write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    # The site fetches the same report, so the inspector tab and the review
    # artefact cannot describe different builds.
    Path(settings.export_dir).mkdir(parents=True, exist_ok=True)
    (Path(settings.export_dir) / "quality.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    if verbose:
        print(f"  {settings.report_md}")
        print(f"  {settings.report_json}")
    return report

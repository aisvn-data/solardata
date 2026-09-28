"""Build-time checks on the data itself, which is where the guards belong.

Every check here is a claim about the *archive*, and every one of them can only
be made by running the pipeline over ``data/raw``.  None of them is a unit test,
which is exactly the point: 0.8 had 147 tests, none of which could notice that
a band was applied to the wrong unit, and a 50-second test that read all 364
files to check the headers agreed -- which they did, and which said nothing about
whether the values behind them were flagged correctly.

Four checks, each of which would have caught a specific 0.8 failure:

``the catalog covers the archive``
    Every (station, width) in the raw files resolves to a declared layout, and no
    station has two layouts of the same width -- so the width lookup is total and
    a headerless file cannot silently lose its column meanings. 0.8 discovered
    that by accident, through a donor search that ordered on string timestamps.

``the ingest's flags and the bands agree``
    A row is flagged ``out_of_range`` exactly when one of its banded channels is
    outside its band, in both directions.  0.8 stored ``phumy2.current2_a``'s
    whole record with the flag and applied no band, so the flag and the criterion
    had nothing to do with each other.

``a band that fires on most of a record says so``
    A band is a claim about a sensor, and a flag that fires on 20% of a channel
    cannot mark a contaminated aggregate.  Anything above
    ``BAND_FIRE_FRACTION`` must carry a note saying why the fire is the finding.
    0.8 had four channels above 20% and no way to know that was wrong.

``an excluded channel is still excluded for the stated reason``
    ``constant`` must still be constant and ``unresolved_unit`` must still have
    no unit, so an exclusion cannot quietly become stale.  Nothing asserts the
    ``not_measurement`` ones -- their whole point is that nobody knows -- but
    their min and max are printed so a change is visible.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from . import build_aggregate, catalog
from .config import FLAG_OUT_OF_RANGE
from .readers import xlsx

__all__ = ["Check", "check_bands", "check_exclusions", "check_flag_agreement", "run"]


@dataclass
class Check:
    name: str
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)


def check_bands(conn: sqlite3.Connection) -> Check:
    """Every band is either quiet or explained."""
    check = Check("bands")
    rows = conn.execute(
        "SELECT c.station_id, c.channel, c.band_lo, c.band_hi, c.band_note,"
        " s.n_values, s.n_out_of_range"
        " FROM station_channels c JOIN channel_stats s"
        " ON s.station_id = c.station_id AND s.channel = c.channel"
        " WHERE c.band_lo IS NOT NULL OR c.band_hi IS NOT NULL"
    ).fetchall()
    for row in rows:
        if not row["n_values"]:
            continue
        fraction = row["n_out_of_range"] / row["n_values"]
        if fraction > catalog.BAND_FIRE_FRACTION and not (row["band_note"] or "").strip():
            check.fail(
                f"{row['station_id']}.{row['channel']}: band "
                f"[{row['band_lo']}, {row['band_hi']}] rejects {row['n_out_of_range']:,} of "
                f"{row['n_values']:,} readings ({fraction:.1%}), which is above the "
                f"{catalog.BAND_FIRE_FRACTION:.1%} threshold, and it has no band_note "
                "explaining why the fire is the finding"
            )
    check.note(f"{len(rows)} banded channels checked")
    return check


def check_flag_agreement(conn: sqlite3.Connection) -> Check:
    """Every out-of-band value carries the flag, and every flagged row has one.

    Both directions, and both are needed.  The tempting check -- compare the
    number of flagged *rows* with the sum of the per-channel out-of-range counts
    -- is wrong, and was wrong in the first version of this function: a row with
    two out-of-range channels is counted once in ``flags`` and twice in the
    channel counts, so the totals legitimately differ and comparing them reports
    a failure that is not there.

    What must hold is the equivalence, per row: this row is flagged exactly when
    at least one of its banded channels is outside its band.  Checked as two
    one-directional counts, because a single query cannot express "no banded
    column of this row is out of band" without naming every channel, and naming
    them in two places is how they drift.
    """
    check = Check("flag agreement")
    for station in catalog.STATIONS:
        banded = [ch for ch in station.channels if ch.band]
        if not banded:
            continue

        # Direction 1: out of band but not flagged.
        for ch in banded:
            band = _band_test(ch)
            if band is None:
                continue
            missing = conn.execute(
                f"SELECT COUNT(*) FROM {station.table} WHERE {ch.name} IS NOT NULL"
                f" AND ({band[0]}) AND flags NOT LIKE '%{FLAG_OUT_OF_RANGE}%'",
                band[1],
            ).fetchone()[0]
            if missing:
                check.fail(
                    f"{station.station_id}.{ch.name}: {missing:,} values are outside "
                    f"[{ch.band_lo}, {ch.band_hi}] but their row carries no "
                    f"{FLAG_OUT_OF_RANGE} flag"
                )

        # Direction 2: flagged but nothing out of band.
        any_out = " OR ".join(f"({ch.name} IS NOT NULL AND ({_band_test(ch)[0]}))" for ch in banded)
        params = [p for ch in banded for p in _band_test(ch)[1]]
        spurious = conn.execute(
            f"SELECT COUNT(*) FROM {station.table}"
            f" WHERE flags LIKE '%{FLAG_OUT_OF_RANGE}%' AND NOT ({any_out})",
            params,
        ).fetchone()[0]
        if spurious:
            check.fail(
                f"{station.station_id}: {spurious:,} rows carry {FLAG_OUT_OF_RANGE} but "
                "no banded channel of theirs is out of range"
            )
    check.note(f"{len(catalog.STATIONS)} stations checked, both directions")
    return check


def _band_test(ch: catalog.Channel) -> tuple[str, list[float]]:
    test, params = [], []
    if ch.band_lo is not None:
        test.append(f"{ch.name} < ?")
        params.append(float(ch.band_lo))
    if ch.band_hi is not None:
        test.append(f"{ch.name} > ?")
        params.append(float(ch.band_hi))
    return (" OR ".join(test) or "0"), params


def check_exclusions(conn: sqlite3.Connection) -> Check:
    """An excluded channel is still excluded for the reason it was excluded."""
    check = Check("exclusions")
    for station in catalog.STATIONS:
        for ch in station.channels:
            stat = conn.execute(
                "SELECT n_values, min, max FROM channel_stats WHERE station_id = ? AND channel = ?",
                (station.station_id, ch.name),
            ).fetchone()
            if stat is None or not stat["n_values"]:
                continue
            if ch.exclude == "constant" and stat["min"] != stat["max"]:
                check.fail(
                    f"{station.station_id}.{ch.name} is excluded as constant but its range "
                    f"is {stat['min']} to {stat['max']}"
                )
            if ch.exclude == "unresolved_unit" and ch.unit:
                check.fail(
                    f"{station.station_id}.{ch.name} is excluded for an unresolved unit but "
                    f"declares unit {ch.unit!r}"
                )
            if ch.exclude == "not_measurement":
                check.note(
                    f"{station.station_id}.{ch.name} excluded as not a measurement; "
                    f"recorded range {stat['min']} to {stat['max']}"
                )
    return check


def check_catalog(raw_dir) -> Check:
    """Every (station, width) in the archive resolves, and widths are unique."""
    check = Check("catalog")

    for station in catalog.STATIONS:
        widths = [layout.n_columns for layout in station.layouts]
        if len(widths) != len(set(widths)):
            check.fail(
                f"{station.station_id}: two layouts share a width, so width alone is ambiguous"
            )

    if not raw_dir.is_dir():
        check.note("no data/raw; skipped the archive scan")
        return check

    xlsx.clear_read_cache()
    seen: dict[tuple[str, int], int] = {}
    for folder in sorted(p for p in raw_dir.iterdir() if p.is_dir()):
        station = catalog.BY_SOURCE_DIR.get(folder.name)
        if station is None:
            check.note(f"folder {folder.name}/ is not in the station registry; ignored")
            continue
        for path in sorted(folder.glob("*.xlsx"), key=lambda p: p.name.lower()):
            block = xlsx.detect_block(path)
            key = (station.station_id, block.n_columns)
            seen[key] = seen.get(key, 0) + 1
            if station.layout_for(block.n_columns) is None:
                check.fail(
                    f"{folder.name}/{path.name}: {block.n_columns} columns, and "
                    f"{station.station_id} declares no layout of that width"
                )
    xlsx.clear_read_cache()
    check.note(
        f"{len(seen)} (station, width) pairs over "
        f"{sum(seen.values())} files; the catalog declares "
        f"{sum(len(s.layouts) for s in catalog.STATIONS)}"
    )
    return check


def run(conn: sqlite3.Connection, raw_dir) -> list[Check]:
    """Every check, in the order that fails fastest."""
    return [
        check_catalog(raw_dir),
        check_bands(conn),
        check_flag_agreement(conn),
        check_exclusions(conn),
    ]


def coverage_note(conn: sqlite3.Connection) -> list[dict]:
    return build_aggregate.coverage(conn)

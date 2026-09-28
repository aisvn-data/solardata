"""Rollups, and the measurement of what each channel actually recorded.

Two tables, and one that is the point of the whole exercise.

``readings_hourly`` and ``readings_daily``
    One row per (station, bucket), carrying the union over stations of the
    published channels' statistics.  ``readings_daily`` is derived from
    ``readings_hourly``, so the two cannot disagree: there is no second
    aggregation that could reach a different answer.

``channel_stats``
    One row per (station, channel): start, stop, count, min, max, mean, the
    1st/50th/99th percentiles that make a bimodal channel visible, how many
    readings are exactly zero, and how many its own band rejected.  This is the
    per-station documentation the archive needs, and it is *measured* rather
    than asserted, so a band that starts firing on a third of a record shows up
    as a number that moved.

Out-of-range counts are per channel
-----------------------------------
The row-level ``n_out_of_range`` cannot say *which* channel broke, and 0.8's
worked example was ``phumy2``: every sample of every hour was flagged, so
30-of-30 carried no information at all.  So each banded channel gets its own
``<channel>_n_oor`` counter, which is what makes "this hour is contaminated, and
it is the temperature probe" answerable.  A channel with no band gets no
counter: it would only ever ship zeros, and a zero that means "not measured" is
worse than no column.

Nothing is multiplied by a cadence
----------------------------------
0.8 computed ``energy_wh`` as ``avg_power * n_samples * 2 / 3600`` for every
station.  That asserts a 2-minute cadence, and multiplies it by a power channel
that six of the eight stations do not have -- and for ``phumy2`` by a channel
that is not a measurement at all.  It is gone.  731,885 readings have a
timestamp, and that is all they have; a reader who wants energy can integrate
the real sample spacing over the real power channel, and only ``aisvn`` has one.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from itertools import pairwise

from . import catalog, db
from .config import FLAG_OUT_OF_RANGE

__all__ = ["build", "coverage"]


# ---------------------------------------------------------------------------
# The read side: all eight station tables as one relation
# ---------------------------------------------------------------------------


def _from_clause() -> str:
    """Every station table, unioned, as ``(station_id, ts_utc, flags, <union>)``.

    A plain ``UNION ALL`` of the eight tables rather than eight joins.  Each
    station's table has only its own channels, so the branches have different
    widths and are unioned positionally against the union of all channel names;
    a channel a station does not have contributes a typed NULL.  Every column is
    aliased explicitly because a UNION ALL takes its column names from the first
    branch, and one station's naming must not decide the schema for the other
    seven.

    Only *published* channels are carried across, because those are the only ones
    any rollup column refers to.  ``wind_v``, ``load1_v``, ``load2_v`` and the
    rest stay in their station's table and never reach a rollup, which is the
    whole point of excluding them -- and not carrying them here is what keeps
    that true rather than merely intended.
    """
    all_channels: list[str] = []
    for station in catalog.STATIONS:
        for ch in station.published:
            if ch.name not in all_channels:
                all_channels.append(ch.name)

    branches = []
    for station in catalog.STATIONS:
        present = {ch.name for ch in station.channels}
        selected = ", ".join(
            f"{station.table}.{name}" if name in present else f"NULL AS {name}"
            for name in all_channels
        )
        branches.append(
            f"SELECT '{station.station_id}' AS station_id, ts_utc, flags, {selected}"
            f" FROM {station.table}"
        )
    return "(" + " UNION ALL ".join(branches) + ")"


def _value_exprs() -> list[str]:
    """``AVG(col)``/``MIN(col)``/``MAX(col)`` for every published statistic."""
    return [f"{stat.upper()}({ch}) AS {col}" for col, ch, stat in db.rollup_columns()]


def _banded_published() -> list[catalog.Channel]:
    """Banded published channels, deduplicated, in catalog order.

    A channel can be published by several stations, and two stations can band it
    differently.  The rollup table is shared, so it gets one counter and one
    test; the per-channel counters that are actually *published* to a browser
    live in each station's own CSV, computed from that station's own band.  The
    counter here is the archive-wide one and is documented as such.
    """
    seen: dict[str, catalog.Channel] = {}
    for station in catalog.STATIONS:
        for ch in station.published:
            if ch.band and ch.kind != "text" and ch.name not in seen:
                seen[ch.name] = ch
    return list(seen.values())


def _oor_exprs() -> list[str]:
    exprs = []
    for ch in _banded_published():
        test = _band_test(ch)
        if test is None:
            continue
        exprs.append(
            f"SUM(CASE WHEN {ch.name} IS NOT NULL AND ({test}) THEN 1 ELSE 0 END)"
            f" AS {ch.name}_n_oor"
        )
    return exprs


def _oor_params() -> list[float]:
    params: list[float] = []
    for ch in _banded_published():
        if _band_test(ch) is None:
            continue
        for bound in (ch.band_lo, ch.band_hi):
            if bound is not None:
                params.append(float(bound))
    return params


def _band_test(ch: catalog.Channel) -> str | None:
    """A SQL range test for a channel's band, or None if it has no band.

    The bounds are bound as parameters and never interpolated.  The band is the
    one from ``etl.catalog`` in the one unit that channel is stored in, because
    the confirmed scale was applied at ingest -- so this is the same criterion
    the ingest applied to the raw cell, and there is no second one to keep in
    step and no count to recompute after the fact.
    """
    if not ch.band:
        return None
    test = []
    if ch.band_lo is not None:
        test.append(f"{ch.name} < ?")
    if ch.band_hi is not None:
        test.append(f"{ch.name} > ?")
    return " OR ".join(test) or None


_COUNTER_COLUMNS = ("boot_count_min", "boot_count_max")


def _hourly_target() -> str:
    return ", ".join(
        [
            "station_id",
            "ts_utc",
            "n_samples",
            "n_out_of_range",
            *(c for c, _, _ in db.rollup_columns()),
            *db.oor_columns(),
        ]
    )


def _build_hourly(conn: sqlite3.Connection, *, verbose: bool) -> int:
    conn.execute("DELETE FROM readings_hourly")
    values = ", ".join(_value_exprs())
    oor = ", ".join(_oor_exprs()) or "0"
    sql = f"""
        INSERT INTO readings_hourly ({_hourly_target()})
        SELECT
            station_id,
            substr(ts_utc, 1, 13) || ':00:00Z',
            COUNT(*),
            SUM(CASE WHEN flags LIKE '%{FLAG_OUT_OF_RANGE}%' THEN 1 ELSE 0 END),
            {values},
            {oor}
        FROM {_from_clause()}
        GROUP BY station_id, substr(ts_utc, 1, 13)
    """
    count = conn.execute(sql, _oor_params()).rowcount or 0
    if verbose:
        print(f"  readings_hourly: {count:,} buckets")
    return count


def _build_daily(conn: sqlite3.Connection, *, verbose: bool) -> int:
    """The daily rollup, derived from the hourly one.

    A day's minimum is the minimum of the hourly minima and its maximum the
    maximum of the hourly maxima, which is the answer a direct aggregation of the
    readings would give, and the out-of-range counters are sums, so a day
    accounts for exactly the samples its hours do.  Deriving it rather than
    recomputing it is what makes "every day and every sample count matches across
    the pair" a structural property instead of a test that has to be written.

    ``day`` is the **local** calendar day and ``ts_utc_day`` the UTC midnight of
    it.  They are not the same instant and both are provided, because a reader
    asking for a day means the day they experienced.
    """
    conn.execute("DELETE FROM readings_daily")
    agg = []
    for column, _channel, stat in db.rollup_columns():
        outer = "avg" if stat == "avg" else stat
        agg.append(f"{outer.upper()}({column}) AS {column}")
    oor = ", ".join(f"SUM({c}) AS {c}" for c in db.oor_columns()) or "0"

    target = [
        "station_id",
        "day",
        "ts_utc_day",
        "n_hours",
        "n_samples",
        "n_out_of_range",
        *(c for c, _, _ in db.rollup_columns()),
        *db.oor_columns(),
    ]

    sql = f"""
        INSERT INTO readings_daily ({", ".join(target)})
        SELECT
            station_id,
            substr(ts_utc, 1, 10),
            substr(ts_utc, 1, 11) || '00:00:00Z',
            COUNT(*),
            SUM(n_samples),
            SUM(n_out_of_range),
            {", ".join(agg)},
            {oor}
        FROM readings_hourly
        GROUP BY station_id, substr(ts_utc, 1, 10)
    """
    count = conn.execute(sql).rowcount or 0
    if verbose:
        print(f"  readings_daily: {count:,} buckets")
    return count


# ---------------------------------------------------------------------------
# channel_stats -- the per-station documentation
# ---------------------------------------------------------------------------


def _band_test_for(ch: catalog.Channel) -> tuple[str, list[float]] | None:
    if not ch.band:
        return None
    test, params = [], []
    if ch.band_lo is not None:
        test.append(f"{ch.name} < ?")
        params.append(float(ch.band_lo))
    if ch.band_hi is not None:
        test.append(f"{ch.name} > ?")
        params.append(float(ch.band_hi))
    return (" OR ".join(test), params) if test else None


def _build_channel_stats(conn: sqlite3.Connection, *, verbose: bool) -> int:
    """Measure every channel of every station, and write it down.

    One query per channel, against that station's own table, which is what the
    eight-table design is for: there is no ``station_id`` predicate and no
    chance of reading one station's channel from another's row.

    The out-of-range count here is recomputed from the values against the band
    rather than read from the row flags.  Those are two independent routes to the
    same number, and ``etl.audit`` compares them, so a drift between what the
    ingest flagged and what the band rejects is a build failure rather than a
    discovery somebody makes later.

    The percentiles are a correlated ``ORDER BY ... LIMIT 1 OFFSET n`` per
    statistic, which SQLite answers with a sort rather than an index -- so this
    is the slowest part of the aggregate, a few seconds over the archive.  It is
    worth it: these rows are the documentation *and* the evidence for every
    band.
    """
    conn.execute("DELETE FROM channel_stats")
    total = 0
    for station in catalog.STATIONS:
        for ch in station.channels:
            if ch.kind == "text":
                # A text column has no min, max, mean or percentile, and SQLite
                # would happily return the alphabetically first event name for
                # MIN() -- a number-shaped answer to a question nobody asked.
                # Only the count and the extent are meaningful.
                n_values, n_rows, first, last = conn.execute(
                    f"SELECT COUNT({ch.name}), COUNT(*), MIN(ts_utc), MAX(ts_utc)"
                    f" FROM {station.table}"
                ).fetchone()
                conn.execute(
                    "INSERT OR REPLACE INTO channel_stats (station_id, channel, n_values,"
                    " n_nulls, first_ts_utc, last_ts_utc, min, max, mean, p01, p50, p99,"
                    " n_zero, n_sentinel, n_null_window, n_out_of_range, n_constant)"
                    " VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL,"
                    " 0, 0, 0, 0, 0)",
                    (
                        station.station_id,
                        ch.name,
                        int(n_values),
                        int(n_rows) - int(n_values),
                        first,
                        last,
                    ),
                )
                total += 1
                continue

            n_values, n_rows, first, last, lo, hi, mean = conn.execute(
                f"SELECT COUNT({ch.name}), COUNT(*), MIN(ts_utc), MAX(ts_utc),"
                f" MIN({ch.name}), MAX({ch.name}), AVG({ch.name}) FROM {station.table}"
            ).fetchone()

            band = _band_test_for(ch)
            n_oor = 0
            if band is not None and n_values:
                n_oor = conn.execute(
                    f"SELECT COUNT(*) FROM {station.table}"
                    f" WHERE {ch.name} IS NOT NULL AND ({band[0]})",
                    band[1],
                ).fetchone()[0]

            n_zero, n_sentinel, n_no_signal = conn.execute(
                f"SELECT"
                f" SUM(CASE WHEN {ch.name} = 0 THEN 1 ELSE 0 END),"
                f" SUM(CASE WHEN {ch.name} IS NULL AND flags LIKE '%sentinel%' THEN 1 ELSE 0 END),"
                f" SUM(CASE WHEN {ch.name} IS NULL AND flags LIKE"
                f" '%no_signal:{ch.name}%' THEN 1 ELSE 0 END)"
                f" FROM {station.table}"
            ).fetchone()

            conn.execute(
                "INSERT OR REPLACE INTO channel_stats (station_id, channel, n_values,"
                " n_nulls, first_ts_utc, last_ts_utc, min, max, mean, p01, p50, p99,"
                " n_zero, n_sentinel, n_null_window, n_out_of_range, n_constant)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    station.station_id,
                    ch.name,
                    int(n_values),
                    int(n_rows) - int(n_values),
                    first,
                    last,
                    lo,
                    hi,
                    mean,
                    _percentile(conn, station, ch, 0.01, int(n_values)),
                    _percentile(conn, station, ch, 0.50, int(n_values)),
                    _percentile(conn, station, ch, 0.99, int(n_values)),
                    int(n_zero or 0),
                    int(n_sentinel or 0),
                    int(n_no_signal or 0),
                    int(n_oor or 0),
                    1 if n_values and lo == hi else 0,
                ),
            )
            total += 1
    if verbose:
        print(f"  channel_stats: {total} rows")
    return total


def _percentile(
    conn: sqlite3.Connection,
    station: catalog.Station,
    ch: catalog.Channel,
    p: float,
    n_values: int,
) -> float | None:
    """Nearest-rank percentile of a channel, or None if it has no values.

    The offset is a fraction of ``n_values``, not a number of hundredths: the
    first version passed ``int(p * 100)``, which put p99 at the 99th smallest
    value rather than the 99th percentile and reported a median of 0 V for a
    channel whose mean is 8 V.  Nearest-rank is the right estimator besides --
    an interpolated percentile over a channel that is 99% one value invents a
    value the channel never recorded.
    """
    if not n_values:
        return None
    offset = min(n_values - 1, max(0, int(p * n_values)))
    row = conn.execute(
        f"SELECT {ch.name} FROM {station.table} WHERE {ch.name} IS NOT NULL"
        f" ORDER BY {ch.name} LIMIT 1 OFFSET ?",
        (offset,),
    ).fetchone()
    return row[0] if row else None


def build(conn: sqlite3.Connection, *, verbose: bool = True) -> tuple[int, int, int]:
    """Return ``(hourly_rows, daily_rows, channel_stats_rows)``."""
    hourly = _build_hourly(conn, verbose=verbose)
    daily = _build_daily(conn, verbose=verbose)
    stats = _build_channel_stats(conn, verbose=verbose)
    conn.commit()
    return hourly, daily, stats


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def _seconds_between(earlier: str, later: str) -> int:
    a = datetime.strptime(earlier, "%Y-%m-%dT%H:%M:%SZ")
    b = datetime.strptime(later, "%Y-%m-%dT%H:%M:%SZ")
    return int((b - a).total_seconds())


def coverage(conn: sqlite3.Connection) -> list[dict]:
    """The observed sampling interval per station.

    Measured, not assumed.  0.8 asserted 2 minutes in a comment and multiplied
    it into ``energy_wh``; the median gap below is what the archive actually
    does, and it is not the same number at every station.
    """
    out = []
    for station in catalog.STATIONS:
        rows = [r[0] for r in conn.execute(f"SELECT ts_utc FROM {station.table} ORDER BY ts_utc")]
        deltas = sorted(_seconds_between(a, b) for a, b in pairwise(rows))
        out.append(
            {
                "station_id": station.station_id,
                "n_readings": len(rows),
                "median_gap_seconds": deltas[len(deltas) // 2] if deltas else None,
                "p90_gap_seconds": deltas[int(len(deltas) * 0.9)] if deltas else None,
            }
        )
    return out

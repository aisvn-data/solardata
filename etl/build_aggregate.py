"""Build the hourly and daily rollups, applying collector-confirmed scales.

This is a separate stage from the ingest, and it has to be: applying a scale
needs the ``regimes`` table, and the ingest runs *before* the regime detector.
Rolling up inside the ingest would mean the rollups were built against whatever
regimes happened to be in the database, which on a clean build is none.

Why scale here at all
---------------------
``readings`` stays raw and is never modified. It is the canonical record, and
rewriting it would lose the ability to disagree with a correction. The rollups
are the presentation layer -- the thing the website reads -- and a rollup that
mixes volts and millivolts is worse than useless: `aisvn-solar`'s solar axis
read 4570 instead of 4.6 V.

Every day that had a scale applied records which columns and which regime, in
``scaled_channels`` and ``regime_ids``, so a reader can tell a scaled value from
a raw one without re-deriving it.

Unconfirmed regimes are never applied. A proposal is a question for a human; see
``AGENTS.md`` rule 3.

Which columns exist, and which statistic each holds, is declared once in
``etl.rollup_schema`` and read from here, so the aggregate and the exporter
cannot drift apart.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from etl.config import CHANNEL_UNITS
from etl.normalize.metrics import METRIC_BY_COLUMN
from etl.rollup_schema import CHANNELS, COUNTED, channel_column, oor_columns

#: Which rollup columns each canonical channel feeds, so one scale decision can
#: be applied to every aggregate of that channel.
#:
#: Kept for the *scaling* pass only. The column list itself lives in
#: ``etl.rollup_schema``; this map says which of them a scale touches, and
#: ``_scale_rows`` intersects it with the table's actual columns so a mapping that
#: names a column that is not there fails loudly in a test rather than silently in
#: SQL.
CHANNEL_COLUMNS: dict[str, tuple[str, ...]] = {
    "solar_v": ("solar_v_avg", "solar_v_max", "solar_v_min"),
    "solar2_v": ("solar2_v_avg", "solar2_v_max"),
    "solar3_v": ("solar3_v_avg", "solar3_v_max"),
    "battery_v": ("battery_v_avg", "battery_v_min", "battery_v_max"),
    "battery2_v": ("battery2_v_min", "battery2_v_max"),
    "lipo_v": ("lipo_v_avg", "lipo_v_min", "lipo_v_max"),
    "lipo2_v": ("lipo2_v_avg", "lipo2_v_min", "lipo2_v_max"),
    "load_v": ("load_v_avg",),
    # phumy2's current, confirmed by the collector as milliamps. It was an empty
    # tuple here, which read as "this channel has nothing to scale" -- and so a
    # confirmed regime for it was written to the table, matched by the lookup, and
    # then dropped for want of a column to multiply. A 415,117-reading
    # out_of_range flag survived the confirmation, which is the flag doing its job
    # on a column the pipeline had been told the unit of.
    "current2_a": ("current2_a_avg",),
}

#: ``wind_v``, ``current_a*`` and ``temp_c`` are absent above: no confirmed regime
#: applies a scale to them, so there is nothing to multiply. They were the
#: millivolt channels of the `aisvn` recompile, and those rows now carry a scale of
#: 1.0 -- the boundary is still recorded, the conversion is not applied.


def _value_exprs() -> list[str]:
    """The per-channel aggregate expressions for the hourly rollup."""
    exprs: list[str] = []
    for channel, stats in CHANNELS:
        for stat in stats:
            fn = {"avg": "AVG", "min": "MIN", "max": "MAX"}[stat]
            exprs.append(f"{fn}({channel})")
    return exprs


def _oor_exprs() -> tuple[list[str], list[float]]:
    """Per-metric out-of-range counts, with the bands bound as parameters.

    The bounds come from ``METRIC_BY_COLUMN`` and are passed as SQL parameters
    rather than interpolated, so the plausibility table has exactly one home --
    ``etl/normalize/metrics.py``, which is also what ships to the browser in
    ``public/data/metrics.json``. Writing the numbers into the SQL as literals
    would be a second copy that drifts silently, and the whole reason the site
    uses these bands rather than its own threshold is that there is only one.

    A station whose declared unit differs from the column's default gets its own
    band, chosen with a CASE on ``station_id``. ``test`` logs temperature in
    hundredths of a degree where every other station uses tenths, so a single
    range would count all 33,377 of its readings as implausible -- and the count
    is what the site uses to decide whether an aggregate is contaminated, so a
    wrong count marks a whole station.
    """
    overrides: dict[str, list[tuple[str, float, float]]] = {}
    for station, column, _unit, lo, hi, _why in CHANNEL_UNITS:
        overrides.setdefault(column, []).append((station, lo, hi))

    exprs: list[str] = []
    params: list[float] = []
    for channel in COUNTED:
        metric = METRIC_BY_COLUMN[channel]
        default = (float(metric.lo), float(metric.hi))
        station_bands = overrides.get(channel)
        if station_bands:
            # CASE station_id WHEN ... THEN <in range> ... ELSE <default> END
            branches = []
            for station, lo, hi in station_bands:
                branches.append(f"WHEN '{station}' THEN ({channel} < ? OR {channel} > ?)")
                params.extend([lo, hi])
            branches.append(f"ELSE ({channel} < ? OR {channel} > ?)")
            params.extend([default[0], default[1]])
            test = "CASE station_id " + " ".join(branches) + " END"
        else:
            test = f"({channel} < ? OR {channel} > ?)"
            params.extend([default[0], default[1]])
        exprs.append(f"SUM(CASE WHEN {channel} IS NOT NULL AND {test} THEN 1 ELSE 0 END)")
    return exprs, params


def build(conn: sqlite3.Connection, *, verbose: bool = True) -> tuple[int, int, int]:
    """Build both rollups. Returns ``(hourly, daily, scaled_rows)``."""
    lookup = RegimeLookup.from_db(conn)

    value_cols = [channel_column(c, s) for c, stats in CHANNELS for s in stats]
    oor_cols = list(oor_columns())
    oor_sql, oor_params = _oor_exprs()

    hourly_columns = [
        "station_id",
        "ts_utc",
        "n_samples",
        "n_out_of_range",
        *value_cols,
        *oor_cols,
        "boot_count_min",
        "boot_count_max",
        "energy_wh",
        "scaled_channels",
        "regime_ids",
    ]
    hourly_sql = f"""
        INSERT INTO readings_hourly ({", ".join(hourly_columns)})
        SELECT
            station_id,
            substr(ts_utc, 1, 13) || ':00:00Z' AS hour,
            COUNT(*),
            SUM(quality_flags LIKE '%out_of_range%'),
            {", ".join(_value_exprs())},
            {", ".join(oor_sql)},
            MIN(boot_count), MAX(boot_count),
            AVG(power_w) * (COUNT(*) * 2.0 / 3600.0),   -- 2-minute nominal cadence
            '', ''
        FROM readings
        GROUP BY station_id, hour
    """

    conn.execute("DELETE FROM readings_hourly")
    hourly = conn.execute(hourly_sql, oor_params).rowcount

    # The daily rollup is derived from the hourly one, so the two cannot disagree.
    # Each statistic aggregates the same way it was built: an average of averages
    # for `avg`, the smallest of the hourly minima for `min`, the largest of the
    # hourly maxima for `max`, and a *sum* for the per-metric out-of-range counts
    # so a day's count is the number of samples in the day, not of hours.
    def daily_value_expr(channel: str, stat: str) -> str:
        col = channel_column(channel, stat)
        return {"avg": f"AVG(h.{col})", "min": f"MIN(h.{col})", "max": f"MAX(h.{col})"}[stat]

    daily_columns = [
        "station_id",
        "day",
        "ts_utc_day",
        "n_samples",
        "n_out_of_range",
        "n_hours",
        *value_cols,
        *oor_cols,
        "energy_wh",
        "boot_count_min",
        "boot_count_max",
        "scaled_channels",
        "regime_ids",
    ]
    daily_value_sql = ", ".join(daily_value_expr(c, s) for c, stats in CHANNELS for s in stats)
    daily_oor_sql = ", ".join(f"SUM(h.{c})" for c in oor_cols)
    daily_sql = f"""
        INSERT INTO readings_daily ({", ".join(daily_columns)})
        SELECT
            h.station_id,
            substr(h.ts_utc, 1, 10)                       AS day,
            substr(h.ts_utc, 1, 11) || '00:00:00Z'        AS day_start,
            SUM(h.n_samples),
            SUM(h.n_out_of_range),
            COUNT(*),
            {daily_value_sql},
            {daily_oor_sql},
            SUM(h.energy_wh),
            -- A value of 1 means the logger had just booted, so a day whose
            -- minimum is 1 restarted; a day whose minimum is high simply did not.
            MIN(h.boot_count_min), MAX(h.boot_count_max),
            '',
            ''
        FROM readings_hourly h
        GROUP BY h.station_id, day
    """

    # Order matters here and it is not incidental. The daily rollup is a `SUM` of
    # the hourly out-of-range counts, and the hourly counts themselves have to be
    # recounted against the *scaled* values. So: scale and recount the hourly rows
    # first, and only then derive the daily rows from them. Deriving first and
    # recounting after leaves the daily table holding the pre-scale counts, which is
    # the exact bug `_rescale_oor_counts` exists to fix.
    scaled_hourly = _scale_rows(conn, "readings_hourly", "ts_utc", lookup)
    _rescale_oor_counts(conn, "readings_hourly", "ts_utc", lookup)

    conn.execute("DELETE FROM readings_daily")
    daily = conn.execute(daily_sql).rowcount

    scaled_daily = _scale_rows(conn, "readings_daily", "day", lookup, multiply=False)

    conn.commit()
    if verbose and (scaled_hourly or scaled_daily):
        print(f"  applied confirmed scales to {scaled_hourly} hourly and {scaled_daily} daily rows")
    return hourly, daily, scaled_hourly + scaled_daily


@dataclass
class RegimeLookup:
    """Confirmed scales, indexed by station and channel, for O(1) lookups."""

    windows: dict[tuple[str, str], list[tuple[str, str | None, float, int]]] = field(
        default_factory=dict
    )

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> RegimeLookup:
        lookup = cls()
        rows = conn.execute(
            "SELECT regime_id, station_id, column, valid_from, valid_to, scale"
            " FROM regimes WHERE status = 'confirmed' AND scale <> 1.0"
        ).fetchall()
        for row in rows:
            key = (row["station_id"], row["column"])
            lookup.windows.setdefault(key, []).append(
                (
                    row["valid_from"],
                    row["valid_to"],
                    row["scale"],
                    row["regime_id"],
                )
            )
        for spans in lookup.windows.values():
            spans.sort()
        return lookup

    @staticmethod
    def _instant(value: str) -> datetime:
        """Parse a stored boundary, which may be a date or a full instant."""
        text = value.replace("Z", "")
        if len(text) <= 10:
            return datetime.fromisoformat(text)
        return datetime.fromisoformat(text)

    def for_span(
        self, station_id: str, column: str, start: datetime, end: datetime
    ) -> tuple[float, int | None]:
        """The scale for a half-open ``[start, end)`` bucket, or ``(1.0, None)``.

        A bucket is scaled only when it lies **entirely** inside one confirmed
        window. A bucket that straddles the boundary is left alone rather than
        scaled by whichever side it mostly falls on.

        That is not a nicety. The `aisvn` recompile is at 08:20:00Z, so the
        08:00 hourly bucket holds 20 minutes of millivolts and 40 of volts, and
        the 2020-06-17 daily bucket holds both too. Scaling either would publish
        a number that no single unit describes. Leaving them raw keeps them
        visibly wrong, which is the honest outcome, and the hourly buckets either
        side of the boundary are scaled correctly.
        """
        spans = self.windows.get((station_id, column))
        if not spans:
            return 1.0, None
        matching = [
            (scale, regime_id)
            for valid_from, valid_to, scale, regime_id in spans
            if self._instant(valid_from) <= start
            and (valid_to is None or end <= self._instant(valid_to))
        ]
        if len(matching) == 1:
            return matching[0]
        return 1.0, None

    def for_day(self, station_id: str, column: str, day: str):
        """``(scale, regime_id)`` for a calendar day, or ``(1.0, None)``.

        A day is one bucket, so this is :meth:`for_span` over the whole day and
        inherits the same refusal to scale a straddling day.
        """
        start = datetime.fromisoformat(day[:10])
        return self.for_span(station_id, column, start, start + timedelta(days=1))


def _scale_rows(
    conn: sqlite3.Connection,
    table: str,
    key: str,
    lookup: RegimeLookup,
    *,
    multiply: bool = True,
) -> int:
    """Apply confirmed scales to the rollup rows in place.

    ``key`` is the column holding the instant: ``ts_utc`` for hourly, ``day`` for
    daily.  Both rollup tables are ``WITHOUT ROWID``, so there is no ``rowid``
    to address rows by and the update keys on the primary key instead.

    ``multiply=False`` records the regime on the row -- ``scaled_channels`` and
    ``regime_ids`` -- without touching the value.  That is what the daily table
    needs, and the reason is the build order: the daily rows are derived from the
    hourly table *after* the hourly values have been scaled, so each hour is
    already correct.  Multiplying the day's mean again applies the same regime
    twice, and `phumy2`'s daily `current2_a` came out at 0.00023 A -- a 0.23 A
    reading divided by a thousand, published by the site, for as long as the
    rollups were built in that order.
    """
    if not lookup.windows:
        return 0

    # The rollup tables carry different column sets, so only touch the ones this
    # table actually has.  A shared mapping that names a missing column would
    # otherwise fail with a bare "no such column".
    present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    stations_with_regimes = {station for station, _ in lookup.windows}
    # The extent of the *data* in each bucket, not the bucket's nominal edges.
    #
    # A rollup bucket is a fixed-width slot, but the readings inside it are not:
    # `aisvn`'s first day starts at 06:10Z, so the 2020-06-15 daily bucket
    # nominally spans 24 hours while every reading in it falls after the start of
    # the confirmed millivolt window. Testing the bucket's edges would refuse to
    # scale a day that is entirely inside the window.
    #
    # This is what makes the straddle test correct rather than merely strict: the
    # question is never "does the slot cross the boundary", it is "is any reading
    # in this bucket on the wrong side of it".
    extent_sql = (
        f"SELECT station_id, substr(ts_utc, 1, {13 if key == 'ts_utc' else 10}) AS bucket,"
        f" MIN(ts_utc) AS lo, MAX(ts_utc) AS hi FROM readings GROUP BY station_id, bucket"
    )
    extents = {
        (r["station_id"], r["bucket"]): (
            datetime.fromisoformat(r["lo"].replace("Z", "")),
            # +1s so a reading landing exactly on an inclusive bound is inside.
            datetime.fromisoformat(r["hi"].replace("Z", "")) + timedelta(seconds=1),
        )
        for r in conn.execute(extent_sql)
    }

    rows = conn.execute(f"SELECT station_id, {key} AS day FROM {table}").fetchall()
    touched = 0
    for row in rows:
        if row["station_id"] not in stations_with_regimes:
            continue
        stamp = row["day"].replace("Z", "")
        bucket = stamp[:13] if key == "ts_utc" else stamp[:10]
        span = extents.get((row["station_id"], bucket))
        if span is None:
            continue
        start, end = span
        assignments: list[str] = []
        params: list = []
        scaled_channels: list[str] = []
        regime_ids: list[str] = []
        for (station_id, column), _ in lookup.windows.items():
            if station_id != row["station_id"]:
                continue
            scale, regime_id = lookup.for_span(station_id, column, start, end)
            if scale == 1.0 or regime_id is None:
                continue
            touched_any = False
            for name in CHANNEL_COLUMNS.get(column, ()):
                if name not in present:
                    continue
                if multiply:
                    assignments.append(f"{name} = {name} * ?")
                    params.append(scale)
                touched_any = True
            if not touched_any:
                # Nothing to scale for this channel in this table.
                continue
            scaled_channels.append(column)
            regime_ids.append(str(regime_id))
        if not scaled_channels:
            continue
        # The value assignments are absent on the metadata-only pass, so the SET
        # list is built from whatever there is rather than assuming both.
        sets = [*assignments, "scaled_channels = ?", "regime_ids = ?"]
        conn.execute(
            f"UPDATE {table} SET {', '.join(sets)} WHERE station_id = ? AND {key} = ?",
            [
                *params,
                ",".join(scaled_channels),
                ",".join(regime_ids),
                row["station_id"],
                row["day"],
            ],
        )
        touched += 1
    conn.commit()
    return touched


def _band_for(channel: str, station_id: str) -> tuple[float, float] | None:
    """The plausibility band for one channel at one station, in the stored unit.

    Pulled out of :func:`_oor_exprs` so the SQL aggregate and the Python recount
    below cannot drift apart. There is one home for the numbers --
    ``etl/normalize/metrics.py`` -- and a per-station override in
    ``config.CHANNEL_UNITS``; this is the join of the two.
    """
    metric = METRIC_BY_COLUMN[channel]
    if metric.lo is None or metric.hi is None:
        return None
    for station, column, _unit, lo, hi, _why in CHANNEL_UNITS:
        if station == station_id and column == channel:
            return (lo, hi)
    return (float(metric.lo), float(metric.hi))


def _rescale_oor_counts(
    conn: sqlite3.Connection, table: str, key: str, lookup: RegimeLookup
) -> int:
    """Recount out-of-range samples for channels whose values were rescaled.

    ``_oor_exprs`` counts a sample out of band by testing the value in
    ``readings`` -- the *stored* value, in the unit the column documents.
    ``_scale_rows`` then multiplies the bucket's aggregates by a confirmed regime.
    The two therefore disagree for every bucket a scale touched: ``phumy2`` 2020
    reports 89,062 ``current2_a`` samples out of range while the value it publishes
    is 0.155-2.0 A on a +/-50 A band, so the row is marked contaminated by a
    conversion the collector has since confirmed. The site uses these counts to
    decide whether an aggregate can be trusted, so a stale count is not cosmetic.

    The count is a count of *samples*, and a scale is applied to a *bucket*, so it
    cannot be fixed by rescaling the number already stored -- the samples behind it
    have to be re-tested. This walks the readings for the affected
    ``(station, channel)`` pairs, applies the same span test ``_scale_rows`` uses
    so a straddling bucket is still left alone, and rewrites the count.

    Only channels with a confirmed non-1.0 regime are touched. Everything else
    keeps the count the SQL gave it, which is already right.
    """
    targets: dict[str, set[str]] = {}
    for station_id, column in lookup.windows:
        if column not in COUNTED or column not in CHANNEL_COLUMNS:
            continue
        if _band_for(column, station_id) is None:
            continue
        targets.setdefault(station_id, set()).add(column)
    if not targets:
        return 0

    width = 13 if key == "ts_utc" else 10
    present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    # The extent of the data in each bucket, the same straddle test _scale_rows uses.
    # Built once per station, in one grouped query, because the scale is a property
    # of the bucket rather than of the reading.
    extents: dict[tuple[str, str], tuple[datetime, datetime]] = {}
    for row in conn.execute(
        f"SELECT station_id, substr(ts_utc, 1, {width}) AS bucket,"
        f" MIN(ts_utc) AS lo, MAX(ts_utc) AS hi FROM readings"
        f" WHERE station_id IN ({','.join('?' * len(targets))})"
        f" GROUP BY station_id, bucket",
        sorted(targets),
    ):
        extents[(row["station_id"], row["bucket"])] = (
            datetime.fromisoformat(row["lo"].replace("Z", "")),
            datetime.fromisoformat(row["hi"].replace("Z", "")) + timedelta(seconds=1),
        )

    # Which scale applies to which bucket, per channel. Decided here in Python from
    # the extents above, then joined against `readings` so the recount is one scan
    # per channel. Doing it the obvious way instead -- a COUNT per bucket -- is
    # O(buckets x rows) with no index on `substr(ts_utc, 1, 13)`, and phumy2's
    # 416,088 readings across 25,566 buckets do not finish in half an hour.
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS oor_scale ("
        " station_id TEXT, channel TEXT, bucket TEXT, scale REAL,"
        " PRIMARY KEY (station_id, channel, bucket))"
    )
    conn.execute("DELETE FROM oor_scale")
    for station_id, channels in targets.items():
        rows_for: list[tuple[str, str, str, float]] = []
        for channel in sorted(channels):
            for (sid, bucket), (start, end) in extents.items():
                if sid != station_id:
                    continue
                scale, regime_id = lookup.for_span(sid, channel, start, end)
                if scale == 1.0 or regime_id is None:
                    # No conversion here, so the SQL count is already the truth.
                    continue
                rows_for.append((sid, channel, bucket, scale))
        if rows_for:
            conn.executemany("INSERT INTO oor_scale VALUES (?, ?, ?, ?)", rows_for)
    conn.commit()

    total = 0
    for channel in sorted({c for cs in targets.values() for c in cs}):
        column = f"{channel}_n_oor"
        if column not in present:
            continue
        for station_id in sorted(targets):
            band = _band_for(channel, station_id)
            if band is None:
                continue
            lo, hi = band
            # One grouped pass over the station's readings, with the per-bucket
            # scale fetched as a correlated lookup into `oor_scale` -- whose primary
            # key makes that an index hit rather than a scan.
            #
            # Both shapes of this query are wrong at O(buckets x rows) without an
            # index on `substr(ts_utc, 1, 13)`: joining the scale map onto the
            # readings re-scans the table once per bucket, and phumy2's 416,088
            # readings across 14,686 buckets do not finish in half an hour. A single
            # `GROUP BY` is what the SQL out-of-range aggregate already does, so this
            # costs the same as the count it is correcting.
            #
            # Where a bucket has no scale the subquery yields NULL, the comparison is
            # NULL, and the sample is not counted -- those buckets keep the value the
            # original aggregate gave them and are filtered out below.
            scale_of = (
                f"(SELECT s.scale FROM oor_scale s WHERE s.station_id = ?"
                f" AND s.channel = ? AND s.bucket = substr(readings.ts_utc, 1, {width}))"
            )
            # Positional note, because getting it wrong is silent: `scale_of` is
            # spliced in *twice*, so the parameters are station, channel, lo for the
            # first copy and station, channel, hi for the second -- not
            # station, channel, station, channel, lo, hi. Shifting them by two puts
            # the band in the subquery's `channel` slot, where SQLite coerces it to
            # 0.0, and the test degenerates into "value > 0", which is true of
            # every current reading and reports all 416,088 of them out of band.
            counted = conn.execute(
                f"SELECT substr(ts_utc, 1, {width}) AS bucket,"
                f" SUM(CASE WHEN {channel} IS NOT NULL"
                f"  AND ({channel} * {scale_of} < ? OR {channel} * {scale_of} > ?)"
                f" THEN 1 ELSE 0 END) AS n"
                f" FROM readings WHERE station_id = ? GROUP BY bucket",
                (station_id, channel, lo, station_id, channel, hi, station_id),
            ).fetchall()
            if not counted:
                continue
            scaled = {
                r[0]
                for r in conn.execute(
                    "SELECT bucket FROM oor_scale WHERE station_id = ? AND channel = ?",
                    (station_id, channel),
                )
            }
            updates = []
            for r in counted:
                if r["bucket"] not in scaled:
                    continue
                label = f"{r['bucket']}:00:00Z" if key == "ts_utc" else r["bucket"]
                updates.append((r["n"] or 0, station_id, label))
            if not updates:
                continue
            conn.executemany(
                f"UPDATE {table} SET {column} = ? WHERE station_id = ? AND {key} = ?",
                updates,
            )
            total += len(updates)
    conn.execute("DROP TABLE oor_scale")
    conn.commit()
    return total

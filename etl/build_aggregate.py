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
    "lipo_v_unused": (),
    "current2_a": (),
}

#: ``wind_v``, ``current_a*`` and ``temp_c`` have no confirmed millivolt regime
#: in the archive, so they are absent above deliberately rather than by accident.


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
    """
    exprs: list[str] = []
    params: list[float] = []
    for channel in COUNTED:
        metric = METRIC_BY_COLUMN[channel]
        exprs.append(
            f"SUM(CASE WHEN {channel} IS NOT NULL"
            f" AND ({channel} < ? OR {channel} > ?) THEN 1 ELSE 0 END)"
        )
        params.extend([float(metric.lo), float(metric.hi)])
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

    conn.execute("DELETE FROM readings_daily")
    daily = conn.execute(daily_sql).rowcount

    scaled_hourly = _scale_rows(conn, "readings_hourly", "ts_utc", lookup)
    scaled_daily = _scale_rows(conn, "readings_daily", "day", lookup)

    conn.commit()
    if verbose and (scaled_hourly or scaled_daily):
        print(f"  applied confirmed scales to {scaled_hourly} hourly and {scaled_daily} daily rows")
    return hourly, daily, scaled_hourly + scaled_daily


@dataclass
class RegimeLookup:
    """Confirmed scales, indexed by station and channel, for O(1) day lookups."""

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
                    row["valid_from"][:10],
                    row["valid_to"][:10] if row["valid_to"] else None,
                    row["scale"],
                    row["regime_id"],
                )
            )
        for spans in lookup.windows.values():
            spans.sort()
        return lookup

    def for_day(self, station_id: str, column: str, day: str):
        """``(scale, regime_id)`` for a day, or ``(1.0, None)`` if none applies.

        A day straddling the end of one confirmed window and the start of
        another returns ``(1.0, None)``: the day's mean is a mixture, and
        scaling a mixture by either scale would invent a number.
        """
        spans = self.windows.get((station_id, column))
        if not spans:
            return 1.0, None
        matching = [
            (scale, regime_id)
            for start, end, scale, regime_id in spans
            if start <= day and (end is None or day < end)
        ]
        if len(matching) == 1:
            return matching[0]
        return 1.0, None


def _scale_rows(conn: sqlite3.Connection, table: str, key: str, lookup: RegimeLookup) -> int:
    """Apply confirmed scales to the rollup rows in place.

    ``key`` is the column holding the instant: ``ts_utc`` for hourly, ``day`` for
    daily.  Both rollup tables are ``WITHOUT ROWID``, so there is no ``rowid``
    to address rows by and the update keys on the primary key instead.
    """
    if not lookup.windows:
        return 0

    # The rollup tables carry different column sets, so only touch the ones this
    # table actually has.  A shared mapping that names a missing column would
    # otherwise fail with a bare "no such column".
    present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    stations_with_regimes = {station for station, _ in lookup.windows}
    rows = conn.execute(f"SELECT station_id, {key} AS day FROM {table}").fetchall()
    touched = 0
    for row in rows:
        if row["station_id"] not in stations_with_regimes:
            continue
        day = row["day"][:10]
        assignments: list[str] = []
        params: list = []
        scaled_channels: list[str] = []
        regime_ids: list[str] = []
        for (station_id, column), _ in lookup.windows.items():
            if station_id != row["station_id"]:
                continue
            scale, regime_id = lookup.for_day(station_id, column, day)
            if scale == 1.0 or regime_id is None:
                continue
            touched_any = False
            for name in CHANNEL_COLUMNS.get(column, ()):
                if name not in present:
                    continue
                assignments.append(f"{name} = {name} * ?")
                params.append(scale)
                touched_any = True
            if not touched_any:
                # Nothing to scale for this channel in this table.
                continue
            scaled_channels.append(column)
            regime_ids.append(str(regime_id))
        if not assignments:
            continue
        conn.execute(
            f"UPDATE {table} SET {', '.join(assignments)},"
            " scaled_channels = ?, regime_ids = ?"
            f" WHERE station_id = ? AND {key} = ?",
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

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
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

#: Which rollup columns each canonical channel feeds, so one scale decision can
#: be applied to every aggregate of that channel.  Channels with no rollup
#: columns are listed anyway: it documents that the channel exists in
#: `readings` but is not aggregated.
#:
#: The two rollup tables do not carry the same set -- `readings_hourly` has
#: `battery_v_avg` and `readings_daily` does not, because a daily mean of a
#: minimum is not a useful number.  `_scale_rows` intersects with the table's
#: actual columns so a shared mapping cannot reference a column that is not
#: there.
CHANNEL_COLUMNS: dict[str, tuple[str, ...]] = {
    "solar_v": ("solar_v_avg", "solar_v_max", "solar_v_min"),
    "solar2_v": ("solar2_v_avg", "solar2_v_max"),
    "solar3_v": (),
    "battery_v": ("battery_v_avg", "battery_v_min", "battery_v_max"),
    "battery2_v": ("battery2_v_min", "battery2_v_max"),
    "load_v": ("load_v_avg",),
    "lipo_v": (),
    "lipo2_v": (),
    "current2_a": (),
}


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

    # The two rollup tables carry different column sets, so only touch the ones
    # this table actually has.  A shared mapping that names a missing column
    # would otherwise fail with a bare "no such column".
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
            for name in CHANNEL_COLUMNS.get(column, ()):
                if name not in present:
                    continue
                assignments.append(f"{name} = {name} * ?")
                params.append(scale)
            if not any(a.startswith(tuple(CHANNEL_COLUMNS[column])) for a in assignments):
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


def build(conn: sqlite3.Connection, *, verbose: bool = True) -> tuple[int, int, int]:
    """Build both rollups. Returns ``(hourly, daily, scaled_rows)``."""
    lookup = RegimeLookup.from_db(conn)

    conn.execute("DELETE FROM readings_hourly")
    hourly = conn.execute(
        """
        INSERT INTO readings_hourly
            (station_id, ts_utc, n_samples, n_out_of_range,
             solar_v_avg, solar_v_max, solar_v_min,
             solar2_v_avg, solar2_v_max,
             battery_v_avg, battery_v_min, battery_v_max,
             battery2_v_min, battery2_v_max,
             power_w_avg, power_w_max,
             temp_c_avg, temp_c_min, temp_c_max,
             current_a_avg, energy_wh,
             scaled_channels, regime_ids)
        SELECT
            station_id,
            substr(ts_utc, 1, 13) || ':00:00Z' AS hour,
            COUNT(*),
            SUM(quality_flags LIKE '%out_of_range%'),
            AVG(solar_v),   MAX(solar_v),   MIN(solar_v),
            AVG(solar2_v),  MAX(solar2_v),
            AVG(battery_v), MIN(battery_v), MAX(battery_v),
            MIN(battery2_v), MAX(battery2_v),
            AVG(power_w),   MAX(power_w),
            AVG(temp_c),    MIN(temp_c),    MAX(temp_c),
            AVG(current_a),
            AVG(power_w) * (COUNT(*) * 2.0 / 3600.0),   -- 2-minute nominal cadence
            '', ''
        FROM readings
        GROUP BY station_id, hour
        """
    ).rowcount

    conn.execute("DELETE FROM readings_daily")
    daily = conn.execute(
        """
        INSERT INTO readings_daily
            (station_id, day, ts_utc_day, n_samples, n_out_of_range, n_hours,
             solar_v_avg, solar_v_max,
             solar2_v_avg, solar2_v_max,
             battery_v_min, battery_v_max,
             battery2_v_min, battery2_v_max,
             power_w_avg, power_w_max, energy_wh,
             temp_c_min, temp_c_avg, temp_c_max,
             scaled_channels, regime_ids)
        SELECT
            h.station_id,
            substr(h.ts_utc, 1, 10)                       AS day,
            substr(h.ts_utc, 1, 11) || '00:00:00Z'        AS day_start,
            SUM(h.n_samples),
            SUM(h.n_out_of_range),
            COUNT(*),
            AVG(h.solar_v_avg),   MAX(h.solar_v_max),
            AVG(h.solar2_v_avg),  MAX(h.solar2_v_max),
            MIN(h.battery_v_min), MAX(h.battery_v_max),
            MIN(h.battery2_v_min), MAX(h.battery2_v_max),
            AVG(h.power_w_avg),   MAX(h.power_w_max),
            SUM(h.energy_wh),
            MIN(h.temp_c_min),    AVG(h.temp_c_avg),    MAX(h.temp_c_max),
            '',
            ''
        FROM readings_hourly h
        GROUP BY h.station_id, day
        """
    ).rowcount

    scaled_hourly = _scale_rows(conn, "readings_hourly", "ts_utc", lookup)
    scaled_daily = _scale_rows(conn, "readings_daily", "day", lookup)

    conn.commit()
    if verbose and (scaled_hourly or scaled_daily):
        print(f"  applied confirmed scales to {scaled_hourly} hourly and {scaled_daily} daily rows")
    return hourly, daily, scaled_hourly + scaled_daily

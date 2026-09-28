"""Rollups, the per-station measurements, and the exported CSVs.

The three properties worth guarding are all about *shape*: that the daily rollup
agrees with the hourly one it is derived from, that each channel's statistics are
the ones its `stats` say they are, and that a station's CSV carries only that
station's channels.  A value being wrong is caught by the band audit on the real
archive; a column being *present* when it should not be is caught here.
"""

from __future__ import annotations

import csv
import unittest

from etl import build_aggregate, build_exports, db
from etl.catalog import BY_ID

from tests.support import TempArchiveCase

AISVN_HEADER = [
    "time",
    "solar",
    "battery",
    "current",
    "power",
    "load",
    "wind",
    "temp",
    "solar2",
    "LiPo",
    "boot",
]


def aisvn_fixture(count: int = 4) -> list[list[object]]:
    """``count`` readings two minutes apart, all inside every band.

    The default is four because 08:52 to 08:58 *local* is 01:52 to 01:58 UTC and
    therefore one hourly bucket, while six would cross 02:00 UTC and produce two.
    The bucket boundary is UTC, not local, and a fixture that forgets that gets a
    second hour that looks empty.
    """
    from tests.test_ingest import aisvn_times

    times = aisvn_times(count)
    rows: list[list[object]] = []
    for index, stamp in enumerate(times):
        # Every tenth reading is out of the battery band, so a per-hour counter has
        # something to count and the "partial" case is reachable in a fixture.
        battery = 29.8 if index % 10 == 9 else 12.5 + index * 0.01
        rows.append(
            [
                stamp,
                14.0 + index * 0.1,
                battery,
                1.0,
                17.0,
                0,
                0,
                32.0 + index * 0.01,
                12.0,
                4.0,
                100 + index,
            ]
        )
    return rows


class AggregateCase(TempArchiveCase):
    """A built archive: ingested, aggregated and ready to query."""

    def build_all(self, rows: list[list[object]] | None = None, header=AISVN_HEADER) -> None:
        self.build({"aisvn": rows if rows is not None else aisvn_fixture()}, {"aisvn": header})
        conn = self.connect()
        self.addCleanup(conn.close)
        build_aggregate.build(conn, verbose=False)
        self.conn = conn


class TestRollupShape(AggregateCase):
    def test_the_hourly_bucket_is_the_hour(self) -> None:
        self.build_all()
        rows = self.conn.execute("SELECT ts_utc, n_samples FROM readings_hourly").fetchall()
        self.assertEqual(len(rows), 1, "four readings two minutes apart is one UTC hour")
        self.assertEqual(rows[0]["ts_utc"], "2020-06-18T01:00:00Z")
        self.assertEqual(rows[0]["n_samples"], 4)

    def test_the_daily_row_is_derived_from_the_hourly_ones(self) -> None:
        # Structural, not a test that happens to pass: there is no second
        # aggregation that could reach a different answer, which is what makes
        # "every day and every sample count matches across the pair" true by
        # construction rather than by assertion.
        self.build_all()
        hourly = self.conn.execute(
            "SELECT COALESCE(SUM(n_samples), 0) AS n, COUNT(*) AS h FROM readings_hourly"
        ).fetchone()
        daily = self.conn.execute(
            "SELECT SUM(n_samples) AS n, SUM(n_hours) AS h FROM readings_daily"
        ).fetchone()
        self.assertEqual(hourly["n"], daily["n"])
        self.assertEqual(hourly["h"], daily["h"])

    def test_a_days_minimum_is_the_minimum_of_its_hours(self) -> None:
        self.build_all(aisvn_fixture(180))  # six hours
        daily = self.conn.execute(
            "SELECT battery_v_min, battery_v_max, battery_v_avg FROM readings_daily"
        ).fetchone()
        bounds = self.conn.execute(
            "SELECT MIN(battery_v_min), MAX(battery_v_max) FROM readings_hourly"
        ).fetchone()
        self.assertAlmostEqual(daily["battery_v_min"], bounds[0])
        self.assertAlmostEqual(daily["battery_v_max"], bounds[1])
        self.assertIsNotNone(daily["battery_v_avg"])

    def test_a_day_keeps_a_different_statistic_from_an_hour(self) -> None:
        # A LiPo pack's health is its lowest reading and a power spike is a peak,
        # so the rollup carries min, max and mean per channel rather than one
        # aggregate and a label.
        self.build_all()
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(readings_hourly)")}
        for channel in BY_ID["aisvn"].published:
            if channel.kind == "text":
                continue
            for stat in channel.stats:
                self.assertIn(channel.stat_column(stat), columns, channel.name)

    def test_a_counter_is_min_and_max_and_never_a_mean(self) -> None:
        # A mean across a reboot averages two boot sessions into a number that
        # never happened.
        self.build_all()
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(readings_hourly)")}
        self.assertIn("boot_count_min", columns)
        self.assertIn("boot_count_max", columns)
        self.assertNotIn("boot_count_avg", columns)

    def test_there_is_no_energy_column(self) -> None:
        self.build_all()
        # 0.8 computed `energy_wh` as `avg_power * n_samples * 2 / 3600` for
        # every station, which asserts a 2-minute cadence and multiplies it by a
        # power channel six of the eight stations do not have -- and for phumy2 by
        # a channel that is not a measurement. A reading has a timestamp and
        # nothing else; a reader who wants energy can integrate the real spacing.
        for table in ("readings_hourly", "readings_daily"):
            columns = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            self.assertNotIn("energy_wh", columns, table)
            self.assertFalse(
                [c for c in columns if c.startswith("energy")], f"{table} has an energy column"
            )


class TestPerChannelCounters(AggregateCase):
    def test_a_channel_out_of_band_is_counted_on_its_own(self) -> None:
        # The row-level count cannot say which channel broke. 0.8's worked example
        # was phumy2, where every sample of every hour was flagged and 30-of-30
        # carried no information at all.
        rows = aisvn_fixture(4)
        rows[0][2] = 29.8  # battery_v out of the 9-16 V band
        self.build_all(rows)
        row = self.conn.execute(
            "SELECT n_samples, n_out_of_range, battery_v_n_oor, solar_v_n_oor,"
            " temp_c_n_oor, lipo_v_n_oor FROM readings_hourly"
        ).fetchone()
        self.assertEqual(row["n_samples"], 4)
        self.assertEqual(row["battery_v_n_oor"], 1)
        self.assertEqual(row["n_out_of_range"], 1, "one sample, one flagged row")
        for channel in ("solar_v", "temp_c", "lipo_v"):
            self.assertEqual(row[f"{channel}_n_oor"], 0, channel)

    def test_a_channel_with_no_band_gets_no_counter(self) -> None:
        # A counter that can only ever be zero is worse than no column: a zero
        # that means "not measured" reads as a measurement.
        self.build_all()
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(readings_hourly)")}
        self.assertNotIn("wind_v_n_oor", columns, "wind_v has no band")
        self.assertIn("battery_v_n_oor", columns)

    def test_the_daily_counter_is_the_sum_of_the_hourly_ones(self) -> None:
        rows = aisvn_fixture(4)
        rows[0][2] = 29.8
        rows[2][2] = 30.1
        self.build_all(rows)
        hourly = self.conn.execute(
            "SELECT SUM(battery_v_n_oor) AS n FROM readings_hourly"
        ).fetchone()[0]
        daily = self.conn.execute("SELECT battery_v_n_oor FROM readings_daily").fetchone()[0]
        self.assertEqual(hourly, daily)
        self.assertEqual(daily, 2)


class TestChannelStats(AggregateCase):
    def test_every_channel_of_every_station_is_measured(self) -> None:
        # This table is the per-station documentation the project exists to
        # produce: start, stop, count, min, max, mean, the percentiles that make a
        # bimodal channel visible, and how many the band rejected.
        self.build_all()
        measured = {
            (r["station_id"], r["channel"])
            for r in self.conn.execute("SELECT station_id, channel FROM channel_stats")
        }
        for station in BY_ID.values():
            for channel in station.channels:
                self.assertIn((station.station_id, channel.name), measured, channel.name)

    def test_start_stop_min_max_and_average(self) -> None:
        self.build_all()
        row = self.conn.execute(
            "SELECT * FROM channel_stats WHERE station_id = 'aisvn' AND channel = 'battery_v'"
        ).fetchone()
        self.assertEqual(row["n_values"], 4)
        self.assertEqual(row["first_ts_utc"], "2020-06-18T01:52:00Z")
        self.assertEqual(row["last_ts_utc"], "2020-06-18T01:58:00Z")
        self.assertAlmostEqual(row["min"], 12.5, places=3)
        self.assertAlmostEqual(row["max"], 12.53, places=3)
        self.assertAlmostEqual(row["mean"], 12.515, places=3)
        self.assertEqual(row["n_out_of_range"], 0)

    def test_the_percentiles_are_fractions_of_the_record(self) -> None:
        # The first version of this passed `int(p * 100)` as the offset, which put
        # p99 at the 99th smallest value rather than the 99th percentile, and
        # reported a median of 0 V for a channel whose mean was 8.
        self.build_all(aisvn_fixture(100))
        row = self.conn.execute(
            "SELECT min, p01, p50, p99, max FROM channel_stats"
            " WHERE station_id = 'aisvn' AND channel = 'solar_v'"
        ).fetchone()
        self.assertLessEqual(row["min"], row["p01"])
        self.assertLessEqual(row["p01"], row["p50"])
        self.assertLessEqual(row["p50"], row["p99"])
        self.assertLessEqual(row["p99"], row["max"])
        # 100 rising values: the median is the 50th, not the 2nd.
        self.assertGreater(row["p50"], row["min"] + 0.3)

    def test_zeros_are_counted_separately_from_gaps(self) -> None:
        # 0 W at midnight is a measurement and an empty cell is a hole. Counting
        # them together is how a genuine night reads as a sensor outage.
        self.build_all(aisvn_fixture(4))
        row = self.conn.execute(
            "SELECT n_values, n_nulls, n_zero FROM channel_stats"
            " WHERE station_id = 'aisvn' AND channel = 'wind_v'"
        ).fetchone()
        self.assertEqual(row["n_values"], 4)
        self.assertEqual(row["n_nulls"], 0)
        self.assertEqual(row["n_zero"], 4, "four genuine zeros, not four gaps")

    def test_a_constant_channel_is_marked(self) -> None:
        # `etl.audit` uses this to keep a `constant` exclusion from going stale.
        self.build_all(aisvn_fixture(3))
        wind = self.conn.execute(
            "SELECT n_constant FROM channel_stats WHERE station_id = 'aisvn' AND channel = 'wind_v'"
        ).fetchone()
        solar = self.conn.execute(
            "SELECT n_constant FROM channel_stats WHERE station_id = 'aisvn'"
            " AND channel = 'solar_v'"
        ).fetchone()
        self.assertEqual(wind["n_constant"], 1)
        self.assertEqual(solar["n_constant"], 0)

    def test_a_text_channel_has_no_statistics(self) -> None:
        # SQLite will happily return the alphabetically first event name for
        # MIN(event), which is a number-shaped answer to a question nobody asked.
        self.write(
            "Solar_2020-05-16",
            [
                ["May 16, 2020 at 10:52PM", "solar_reading", 100, 450, 3784],
                ["May 16, 2020 at 10:54PM", "solar_reading", 200, 460, 3780],
            ],
            ["time", "event", "digital", "voltage", "LiPo"],
        )
        self.build({"Solar_2020-05-16": []})
        conn = self.connect()
        self.addCleanup(conn.close)
        build_aggregate.build(conn, verbose=False)
        row = conn.execute(
            "SELECT n_values, min, max, mean, p50 FROM channel_stats"
            " WHERE station_id = 'solar-2020-05' AND channel = 'event'"
        ).fetchone()
        self.assertEqual(row["n_values"], 2)
        for key in ("min", "max", "mean", "p50"):
            self.assertIsNone(row[key], key)


class TestExports(TempArchiveCase):
    def setUp(self) -> None:
        super().setUp()
        self.rebuild(
            {
                "aisvn": aisvn_fixture(8),
                "phumy2": [
                    ["June 18, 2020 at 08:52AM", 3000, 232, 0, 32.0, 4000, 1],
                    ["June 18, 2020 at 08:54AM", 3100, 233, 0, 32.1, 4001, 2],
                ],
            },
            {
                "aisvn": AISVN_HEADER,
                "phumy2": ["time", "solar2", "current2", "power", "temp", "LiPo2", "boot"],
            },
        )

    def rebuild(self, stations, headers=None) -> None:
        """Close any open connection, rebuild from scratch, aggregate, export.

        The ingest deletes the database, so an earlier connection has to be closed
        first. On Windows that is a ``PermissionError`` rather than a silent
        surprise, which is at least honest about it.
        """
        for connection in getattr(self, "_open", []):
            connection.close()
        self._open = []
        self.build(stations, headers)
        conn = self.connect()
        self._open.append(conn)
        build_aggregate.build(conn, verbose=False)
        build_exports.build(conn, self.settings, verbose=False)
        self.conn = conn
        self.addCleanup(conn.close)

    def header(self, station: str, folder: str = "hourly") -> list[str]:
        path = self.settings.export_dir / station / folder / "2020.csv"
        with path.open(encoding="utf-8") as handle:
            return next(csv.reader(handle))

    def test_a_csv_carries_only_its_own_stations_channels(self) -> None:
        aisvn = self.header("aisvn")
        phumy2 = self.header("phumy2")
        self.assertTrue(any(c.startswith("battery_v") for c in aisvn))
        self.assertFalse(
            any(c.startswith("battery_v") for c in phumy2),
            "phumy2 has no battery channel, so it has no battery column",
        )
        self.assertTrue(any(c.startswith("current2_a") for c in phumy2))
        self.assertFalse(any(c.startswith("current2_a") for c in aisvn), "aisvn has no current2")
        self.assertFalse(
            any(c.startswith("power_w") for c in phumy2),
            "phumy2's power pin is not a measurement, so it has no column",
        )
        self.assertTrue(
            any(c.startswith("power_w") for c in aisvn), "aisvn's power channel is real"
        )
        self.assertFalse(
            any(c.startswith("wind_v") for c in aisvn), "aisvn's wind input is not charted"
        )

    def test_the_csv_starts_with_the_bucket_metadata(self) -> None:
        # n_samples is how a day with one reading is told from a day with five
        # hundred, and a bucket with none must break the line rather than sit at
        # zero. 0.8's CSV carried it; so does this one.
        self.assertEqual(self.header("aisvn")[:2], ["ts", "n_samples"])
        self.assertEqual(self.header("aisvn", "daily")[:3], ["ts", "n_samples", "n_hours"])
        self.assertNotIn("n_hours", self.header("aisvn"), "an hour is one hour wide")

    def test_a_gap_is_an_empty_cell(self) -> None:
        rows = aisvn_fixture(4)
        rows[1][1] = ""  # solar_v
        self.rebuild({"aisvn": rows}, {"aisvn": AISVN_HEADER})
        path = self.settings.export_dir / "aisvn" / "hourly" / "2020.csv"
        with path.open(encoding="utf-8") as handle:
            data = list(csv.reader(handle))
        header = data[0]
        # The hour still has samples, and the mean is over the three that have a
        # value -- not a mean that counted the gap as a zero. 0.8's
        # `energy_wh` did exactly that, which is where its 662.57 W came from.
        self.assertEqual(data[1][header.index("n_samples")], "4")
        expected = sum(float(r[1]) for r in rows if r[1] != "") / 3
        self.assertAlmostEqual(float(data[1][header.index("solar_v_avg")]), expected, places=3)

    def test_stations_json_carries_the_channel_declaration(self) -> None:
        import json

        payload = json.loads(
            (self.settings.export_dir / "stations.json").read_text(encoding="utf-8")
        )
        station = next(s for s in payload if s["station_id"] == "phumy2")
        power = next(c for c in station["channels"] if c["channel"] == "power_w")
        self.assertFalse(power["published"])
        self.assertEqual(power["exclude_reason"], "not_measurement")
        self.assertGreater(len(power["exclude_note"]), 40)
        self.assertIsNone(power["band"][0])
        self.assertIsNone(power["band"][1])
        # And the reason is repeated in `hidden`, which is what the site renders.
        self.assertIn("power_w", [h["channel"] for h in station["hidden"]])
        self.assertEqual(station["table"], "s_phumy2")

    def test_metrics_json_is_keyed_by_station_and_channel(self) -> None:
        import json

        payload = json.loads(
            (self.settings.export_dir / "metrics.json").read_text(encoding="utf-8")
        )
        self.assertIn("phumy2.current2_a", payload["channels"])
        self.assertIn("aisvn.battery_v", payload["channels"])
        entry = payload["channels"]["phumy2.current2_a"]
        self.assertEqual(entry["unit"], "A")
        self.assertEqual(entry["band"], [-5.0, 5.0])

    def test_a_station_with_nothing_still_lists_its_channels(self) -> None:
        # Every station in the registry is written, including the two that are not
        # solar production. A grouping is not a filter.
        import json

        payload = json.loads(
            (self.settings.export_dir / "stations.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(payload), 8)
        for station in payload:
            self.assertTrue(station["channels"], station["station_id"])
        bench = [s["station_id"] for s in payload if not s["is_production"]]
        self.assertEqual(bench, ["test", "voltage-phumy"])


class TestGeneratedDdl(unittest.TestCase):
    def test_a_station_table_declares_its_channels_and_nothing_else(self) -> None:
        for station in BY_ID.values():
            sql = db.station_ddl(station)
            body = sql.split("(", 1)[1]
            for channel in station.channels:
                self.assertIn(channel.name, body, f"{station.station_id}.{channel.name}")
            other = {c.name for s in BY_ID.values() if s is not station for c in s.channels}
            for name in other - {c.name for c in station.channels}:
                self.assertNotIn(f" {name} ", body, f"{station.table} leaked {name}")

    def test_a_counter_column_is_an_integer(self) -> None:
        # `boot_count_min` as REAL would be 1.9999999999999998 and AVG/MIN/MAX would
        # all behave slightly wrongly.
        sql = db.station_ddl(BY_ID["aisvn"])
        self.assertIn("boot_count       INTEGER", sql)
        self.assertIn("solar_v          REAL", sql)

    def test_the_primary_key_is_the_instant_alone(self) -> None:
        # The table is already scoped to one station, so the key does not need to
        # repeat it -- and WITHOUT ROWID stores the rows in key order, which is
        # both smaller than a rowid table plus an index and free to range-scan.
        sql = db.station_ddl(BY_ID["aisvn"])
        self.assertIn("ts_utc     TEXT NOT NULL PRIMARY KEY", sql)
        self.assertIn("WITHOUT ROWID", sql)
        self.assertNotIn("station_id", sql)

    def test_the_rollup_columns_are_the_union_of_the_published_ones(self) -> None:
        columns = {c for c, _ch, _stat in db.rollup_columns()}
        expected = {
            channel.stat_column(stat)
            for station in BY_ID.values()
            for channel in station.published
            for stat in channel.stats
        }
        self.assertEqual(columns, expected)
        # And no excluded channel reaches a rollup, which is the whole point of
        # excluding it.
        for name in ("wind_v", "power_w", "load1_v", "dump_adc", "event"):
            if name == "power_w":
                continue  # aisvn publishes it, so the column legitimately exists
            self.assertNotIn(name, columns, name)

    def test_the_rollup_ddl_matches_the_columns_the_aggregate_inserts(self) -> None:
        # The columns are generated in two places -- `db.rollup_ddl` and
        # `build_aggregate`'s SELECT -- and a mismatch is a confusing SQLite error
        # rather than a build failure with a useful message. So count both.
        n_values = len(db.rollup_columns())
        n_oor = len(db.oor_columns())
        for hourly, metadata in ((True, 4), (False, 6)):
            sql = db.rollup_ddl(hourly)
            body = sql.split("(", 1)[1].rsplit(")", 1)[0]
            declared = [
                line.strip().rstrip(",")
                for line in body.splitlines()
                if line.strip() and not line.strip().startswith(("PRIMARY KEY", "--"))
            ]
            self.assertEqual(
                len(declared),
                n_values + n_oor + metadata,
                f"{'hourly' if hourly else 'daily'} declares a different number of columns",
            )
            # And the generated target list is the same set.
            self.assertEqual(len(declared), len(set(declared)), "a column is declared twice")
        self.assertEqual(len(db.rollup_columns()), len(set(c for c, _, _ in db.rollup_columns())))
        self.assertEqual(len(db.oor_columns()), len(set(db.oor_columns())))


if __name__ == "__main__":
    unittest.main()

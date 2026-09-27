"""The export's publication policy.

One question is tested here, and it is a policy question rather than a mechanical
one: *which stations does the site show?*

Until 0.7.2 the answer was "the production ones", and `test` and `voltage-phumy`
were written nowhere -- no rollup, no year in the manifest, nothing the picker
could offer. 38,930 readings that were in the database, in the Parquet export and
in the quality report had no route to the one place a reader goes to look. They
are still not solar production, and they are still grouped separately on the site;
what changed is that the data is published and the distinction is stated rather
than enforced by absence.

The fixture is a real SQLite database built from `etl/schema.sql` with one
reading and one rollup bucket per station, so the test exercises the same export
code path as the real build rather than a reimplementation of it.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from etl.build_exports import build
from etl.db import init_schema
from etl.rollup_schema import oor_columns, value_columns
from etl.stations import NON_PRODUCTION, STATIONS


def _insert_rollup(
    conn: sqlite3.Connection,
    table: str,
    key: str,
    station_id: str,
    instant: str,
    temp: float,
) -> None:
    """One rollup bucket, with only `temp_c` populated.

    NULL everywhere else on purpose: the export must carry a gap through to the
    CSV rather than invent a zero for a channel the fixture did not measure. The
    NOT NULL columns the schema insists on -- the counters, the scale lists and
    `created_at` -- are given their real empty values, because a bucket that
    recorded nothing is not the same as a bucket that does not exist.
    """
    counters = {"n_samples": 30, "n_out_of_range": 0}
    columns = list(counters)
    values: dict[str, object] = {
        "temp_deci_c_avg": temp,
        **{name: 0 for name in oor_columns()},
        **counters,
    }
    if table == "readings_daily":
        # A daily bucket is keyed on the local day and carries the UTC day the
        # day's samples start in; the two differ by the +07:00 offset.
        counters["n_hours"] = 24
        columns.append("n_hours")
        columns.append("ts_utc_day")
        values["ts_utc_day"] = "2020-07-04"
    for name in ("scaled_channels", "regime_ids", "artefact"):
        values[name] = ""
    columns = [*columns, *value_columns(), *oor_columns()]
    conn.execute(
        f"INSERT INTO {table} (station_id, {key}, {', '.join(columns)})"
        f" VALUES (?, ?, {', '.join('?' for _ in columns)})",
        [station_id, instant, *(values.get(name) for name in columns)],
    )


def _fixture_db() -> sqlite3.Connection:
    """A database with the real schema and one day of readings per station."""
    conn = sqlite3.connect(":memory:")
    # `etl.db.connect` sets this, and the export reads rows by name.
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    for station in STATIONS:
        # Every station gets a row, including the two that are not production:
        # that is the condition this file is about.
        conn.execute(
            "INSERT INTO stations (station_id, display_name, location, tz, applet,"
            " source_dirs, is_production) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                station.station_id,
                station.display_name,
                station.location,
                station.tz,
                station.applet,
                json.dumps(list(station.source_dirs)),
                int(station.station_id not in NON_PRODUCTION),
            ),
        )
        # 2,807 hundredths of a degree: `test`'s unit, deliberately wrong for
        # every other station, which is what makes the override observable.
        _insert_rollup(conn, "readings_daily", "day", station.station_id, "2020-07-05", 2807.0)
        _insert_rollup(
            conn,
            "readings_hourly",
            "ts_utc",
            station.station_id,
            "2020-07-05T03:00:00Z",
            2807.0,
        )
        # The export reads the rollups and the station registry, not `readings`,
        # so the fixture does not populate it. `quality.json` is written from the
        # same call either way and has to cope with an empty one.
    conn.commit()
    return conn


class TestExportPublicationPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.target = Path(cls._tmp.name)
        cls.conn = _fixture_db()
        cls.written = build(cls.conn, cls.target, verbose=False)
        cls.manifest = json.loads((cls.target / "stations.json").read_text(encoding="utf-8"))
        cls.by_id = {record["station_id"]: record for record in cls.manifest}

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls._tmp.cleanup()

    def test_every_station_in_the_registry_is_published(self):
        # The whole point: a station with data is reachable, whatever it is.
        self.assertEqual(len(self.manifest), len(STATIONS))
        for station in STATIONS:
            record = self.by_id.get(station.station_id)
            self.assertIsNotNone(record, f"{station.station_id} is missing from stations.json")
            self.assertEqual(
                record["years"],
                ["2020"],
                f"{station.station_id} has a reading but no published year",
            )
            for folder in ("daily", "hourly"):
                self.assertTrue(
                    (self.target / station.station_id / folder / "2020.csv").exists(),
                    f"{station.station_id} {folder} 2020.csv was not written",
                )

    def test_bench_stations_are_marked_not_published_rather_than_omitted(self):
        # `published` is "counts as solar production", so the site can group them.
        for station_id in sorted(NON_PRODUCTION):
            self.assertFalse(
                self.by_id[station_id]["published"],
                f"{station_id} is bench data and must not be marked as production",
            )
        production = [r for r in self.manifest if r["published"]]
        self.assertEqual(len(production), len(STATIONS) - len(NON_PRODUCTION))

    def test_all_stations_puts_them_in_the_production_group(self):
        # The flag now changes the grouping, not whether the CSVs are written,
        # because withholding them was the part that was wrong.
        with tempfile.TemporaryDirectory() as other:
            build(self.conn, Path(other), include_non_production=True, verbose=False)
            manifest = json.loads((Path(other) / "stations.json").read_text(encoding="utf-8"))
            for record in manifest:
                self.assertTrue(record["published"], f"{record['station_id']} stayed unpublished")
            for station_id in sorted(NON_PRODUCTION):
                self.assertTrue(
                    (Path(other) / station_id / "daily" / "2020.csv").exists(),
                    f"{station_id} lost its rollup under --all-stations",
                )

    def test_a_stations_own_unit_travels_with_the_manifest(self):
        # A channel whose unit belongs to a (station, column) pair rather than to
        # a column name. Three stations need it: `test` logs hundredths of a
        # degree, `aisvn-solar` logs millivolts through a 50/50 divider, and
        # `aisvn2`'s `lipo2_v` pin is in millivolts for a 2S pack where the
        # column default describes a single cell. All three were flagged on every
        # reading, which is the flag saying the band describes other hardware
        # rather than that the data is wrong.
        expected = {
            "test": {"temp_c": {"unit": "0.01 degC", "lo": 2149.0, "hi": 3131.0}},
            "aisvn-solar": {"battery_v": {"unit": "mV", "lo": 0.0, "hi": 5100.0}},
            "aisvn2": {"lipo2_v": {"unit": "mV", "lo": 0.0, "hi": 8000.0}},
        }
        for station_id, units in expected.items():
            self.assertEqual(
                self.by_id[station_id]["channel_units"],
                units,
                f"{station_id}'s per-station units are {self.by_id[station_id]['channel_units']}",
            )
        for record in self.manifest:
            if record["station_id"] not in expected:
                self.assertEqual(
                    record["channel_units"],
                    {},
                    f"{record['station_id']} has a unit override it should not have",
                )

    def test_the_bench_stations_are_still_counted_where_they_are(self):
        # Publishing them is not a promotion: they are not in the production
        # count, and the CSVs carry the same columns as everyone else's.
        self.assertEqual(self.written["daily"], len(STATIONS))
        self.assertEqual(self.written["hourly"], len(STATIONS))
        with (self.target / "test" / "daily" / "2020.csv").open(encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        self.assertIn("temp_deci_c_avg", header)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

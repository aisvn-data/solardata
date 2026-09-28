"""The ingest: XLSX in, eight tables out, from scratch every time.

Real XLSX fixtures written with openpyxl, because that is the only way to catch
a reader that has stopped understanding the file format.  The real archive is
*not* read here -- ``etl.audit`` reads it, in the build, where a minute of
reading is a minute of CI rather than a stalled test run.  That split is the
reason this suite finishes in seconds where 0.8's took a minute and stalled.
"""

from __future__ import annotations

import unittest

from etl import catalog
from etl.config import SENTINELS
from etl.readers import xlsx

from tests.support import TempArchiveCase, write_xlsx

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


def aisvn_times(count: int, start_minute: int = 0) -> list[str]:
    """``count`` distinct, well-formed timestamps, two minutes apart.

    Built from the hour and minute fields rather than interpolated into the
    string, because ``strptime`` and the archive's regex are strict about a
    two-digit minute and a fixture that is 3 minutes malformed tests nothing.
    """
    from datetime import datetime, timedelta

    base = datetime(2020, 6, 18, 8, 52) + timedelta(minutes=start_minute)
    return [
        (base + timedelta(minutes=2 * i)).strftime("%B %d, %Y at %I:%M%p") for i in range(count)
    ]


def aisvn_rows() -> list[list[object]]:
    """Six readings inside every band, with one of each interesting thing."""
    times = aisvn_times(6)
    return [
        [times[0], 14.45, 12.80, 1.21, 17.56, 0, 0, 32.5, 12.12, 4.12, 448],
        [times[1], 14.50, 12.81, 1.19, 17.60, 0, 0, 32.6, 12.10, 4.11, 449],
        # Out of the 9-16 V battery band: kept, and flagged.
        [times[2], 14.52, 29.80, 1.18, 17.62, 0, 0, 32.7, 12.08, 4.10, 450],
        # -992 is the collector's placeholder, not a reading.
        [times[3], 14.60, -992, 1.10, 17.70, 0, 0, 32.8, 12.05, 4.09, 451],
        # Blank cells are gaps, not zeros.
        [times[4], "", "", "", "", "", "", "", "", "", ""],
        # Above the 60 degC ambient band: kept, and flagged.
        [times[5], 14.10, 12.70, 1.30, 17.40, 0, 0, 63.3, 12.20, 4.20, 453],
    ]


class TestPerStationTables(TempArchiveCase):
    def test_one_table_per_station_holding_only_its_channels(self) -> None:
        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)

        columns = {r["name"] for r in conn.execute("PRAGMA table_info(s_aisvn)")}
        expected = {ch.name for ch in catalog.BY_ID["aisvn"].channels}
        self.assertEqual(
            columns - {"ts_utc", "ts_local", "flags", "source_file_id", "sheet_row"}, expected
        )
        # And no table exists for a channel another station has.
        self.assertNotIn("battery2_v", columns)
        self.assertNotIn("solar3_v", columns)

    def test_every_station_gets_a_table_even_with_no_readings(self) -> None:
        # Eight tables, always. A station whose files are all excluded still has
        # its table, empty, so a query does not have to know in advance which
        # stations are in this build.
        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        names = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for station in catalog.STATIONS:
            self.assertIn(station.table, names, station.station_id)

    def test_the_catalog_lands_in_the_database(self) -> None:
        # So a query can answer "what does this station collect" without
        # importing the Python package, and the site reads the same table the
        # report does rather than a second copy of the declaration.
        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        rows = conn.execute(
            "SELECT channel, unit, scale, band_lo, band_hi FROM station_channels"
            " WHERE station_id = 'aisvn' ORDER BY channel"
        ).fetchall()
        self.assertEqual(len(rows), len(catalog.BY_ID["aisvn"].channels))
        battery = next(r for r in rows if r["channel"] == "battery_v")
        self.assertEqual(
            (battery["unit"], battery["band_lo"], battery["band_hi"]), ("V", 9.0, 16.0)
        )
        layouts = conn.execute(
            "SELECT n_columns, channels FROM station_layouts WHERE station_id = 'aisvn'"
        ).fetchall()
        self.assertEqual(len(layouts), 1)
        self.assertEqual(layouts[0]["n_columns"], 11)


class TestHeaderlessFiles(TempArchiveCase):
    def test_a_headerless_file_gets_its_meanings_from_the_catalog(self) -> None:
        # 305 of the 364 real files have no header row. 0.8 found their schema by
        # borrowing the header of the nearest preceding sibling of the same width,
        # ordered on the parsed timestamp, and getting that wrong discarded 90% of
        # the archive's measurements while every timestamp still ingested and
        # nothing reported a problem. 0.9 looks the layout up by (station, width).
        summary = self.build({"aisvn": aisvn_rows()})
        self.assertEqual(summary.rows_ingested, 6)
        conn = self.connect()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM s_aisvn").fetchone()[0], 6)
        row = conn.execute("SELECT solar_v, battery_v, temp_c FROM s_aisvn").fetchone()
        # Column 1 is `solar`, column 2 is `battery`, column 7 is `temp`. If the
        # donor machinery had shifted the order these would be transposed.
        self.assertAlmostEqual(row["solar_v"], 14.45)
        self.assertAlmostEqual(row["battery_v"], 12.80)
        self.assertAlmostEqual(row["temp_c"], 32.5)

    def test_a_headered_file_agrees_with_a_headerless_one(self) -> None:
        # Same station, same width, two files: one with a header row and one
        # without. They must land in the same columns, which is the property the
        # layout lookup guarantees and 0.8's donor search had to earn.
        self.raw.mkdir(parents=True, exist_ok=True)
        write_xlsx(self.raw / "aisvn" / "a.xlsx", AISVN_HEADER, aisvn_rows())
        write_xlsx(self.raw / "aisvn" / "b.xlsx", None, aisvn_rows())
        xlsx.clear_read_cache()
        from etl import build_db

        build_db.ingest(self.settings, verbose=False)
        conn = self.connect()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM s_aisvn").fetchone()[0], 6)
        self.assertAlmostEqual(
            conn.execute("SELECT solar_v FROM s_aisvn ORDER BY ts_utc LIMIT 1").fetchone()[0],
            14.45,
        )

    def test_an_undeclared_width_fails_loudly(self) -> None:
        # The failure mode this replaces: silently borrowing a neighbour's schema
        # and ingesting every timestamp with the wrong columns. Here the build
        # stops, with a message naming the widths that are known.
        self.raw.mkdir(parents=True, exist_ok=True)
        write_xlsx(
            self.raw / "aisvn" / "wide.xlsx",
            [
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
                "something_new",
            ],
            aisvn_rows(),
        )
        xlsx.clear_read_cache()
        from etl import build_db

        with self.assertRaises(ValueError) as caught:
            build_db.ingest(self.settings, verbose=False)
        message = str(caught.exception)
        self.assertIn("no layout for a 12-column sheet", message)
        self.assertIn("Known widths: 11", message)
        self.assertIn("etl.catalog", message)

    def test_a_header_row_is_detected_and_skipped(self) -> None:
        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        row = conn.execute(
            "SELECT has_header, n_rows FROM source_files WHERE rel_path = ?",
            ("aisvn/IFTTT_test.xlsx",),
        ).fetchone()
        self.assertEqual(row["has_header"], 1)
        self.assertEqual(row["n_rows"], 6, "the header is not a data row")


class TestValuesAndFlags(TempArchiveCase):
    def test_a_blank_cell_is_a_gap_not_a_zero(self) -> None:
        # Rule: `0 W` at midnight is a measurement and an empty cell is a hole in
        # the sheet. Coercing the second to the first is how a sensor outage
        # becomes a reading.
        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        row = conn.execute("SELECT * FROM s_aisvn WHERE sheet_row = 6").fetchone()
        for ch in catalog.BY_ID["aisvn"].channels:
            self.assertIsNone(row[ch.name], f"{ch.name} should be NULL, not 0")
        self.assertEqual(row["flags"], "", "a gap carries no flag")

    def test_a_genuine_zero_survives_as_zero(self) -> None:
        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        row = conn.execute(
            "SELECT wind_v, load_v, flags FROM s_aisvn WHERE sheet_row = 2"
        ).fetchone()
        self.assertEqual(row["wind_v"], 0.0)
        self.assertEqual(row["load_v"], 0.0)
        self.assertNotIn("out_of_range", row["flags"], "0 is inside a 0-60 V band")

    def test_a_sentinel_becomes_null_with_a_flag_and_a_rejects_row(self) -> None:
        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        row = conn.execute("SELECT battery_v, flags FROM s_aisvn WHERE sheet_row = 5").fetchone()
        self.assertIsNone(row["battery_v"], "-992 is a hole in the sheet, not a voltage")
        self.assertIn("sentinel", row["flags"])
        rejects = conn.execute(
            "SELECT raw_value, reason, column_name FROM rejects WHERE reason = 'sentinel'"
        ).fetchall()
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0]["raw_value"], "-992")
        self.assertEqual(rejects[0]["column_name"], "battery_v")

    def test_an_out_of_range_value_is_kept_and_flagged(self) -> None:
        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        row = conn.execute("SELECT battery_v, flags FROM s_aisvn WHERE sheet_row = 4").fetchone()
        self.assertAlmostEqual(row["battery_v"], 29.80, msg="the value is kept")
        self.assertIn("out_of_range", row["flags"])

    def test_the_band_is_aisvn_batterys_not_a_global_one(self) -> None:
        # 9-16 V is a claim about a 12 V lead-acid pack. 0.8 held one band per
        # *column name* and tested raw millivolt cells against it, which is how
        # 232 mA ended up inside a +/-50 A band and 416,088 readings were flagged.
        station = catalog.BY_ID["aisvn"].channel("battery_v")
        self.assertEqual(station.band, (9.0, 16.0))
        phumy2 = catalog.BY_ID["phumy2"].channel("current2_a")
        self.assertEqual(phumy2.scale, 0.001, "milliamps, confirmed against the hardware")
        self.assertEqual(phumy2.unit, "A")
        self.assertEqual(phumy2.band, (-5.0, 5.0), "and the band is in amps")

    def test_a_scale_is_applied_once_and_the_band_tests_the_scaled_value(self) -> None:
        # phumy2.current2_a: a 232 mA cell becomes 0.232 A at ingest, and the
        # band is applied to 0.232. Testing 232 against -5..5 is the 0.8 bug.
        from etl import build_db
        from etl.catalog import BY_ID

        station = BY_ID["phumy2"]
        channel = station.channel("current2_a")
        value, flags = build_db._coerce("232", channel, [])
        self.assertAlmostEqual(value, 0.232, msg="scaled once")
        self.assertEqual(flags, (), "and 0.232 A is inside -5..5 A")

    def test_a_sentinel_is_checked_before_the_scale(self) -> None:
        # -992 is -992 whether the channel publishes volts or milliamps. Scaling
        # first would give -0.992, which is a plausible-looking number and not a
        # placeholder at all.
        from etl import build_db
        from etl.catalog import BY_ID

        for sentinel in SENTINELS:
            for name in ("current2_a", "solar2_v"):
                value, flags = build_db._coerce(str(sentinel), BY_ID["phumy2"].channel(name), [])
                self.assertIsNone(value, f"{sentinel} on {name}")
                self.assertEqual(flags, ("sentinel",))

    def test_every_sentinel_becomes_null(self) -> None:
        for sentinel in SENTINELS:
            value, flags = build_db_value(sentinel)
            self.assertIsNone(value, f"{sentinel} should be NULL")
            self.assertEqual(flags, ("sentinel",))


def build_db_value(sentinel: float):
    from etl import build_db
    from etl.catalog import BY_ID

    return build_db._coerce(str(sentinel), BY_ID["aisvn"].channel("solar_v"), [])


class TestDuplicates(TempArchiveCase):
    def test_a_repeated_timestamp_is_absorbed_and_recorded(self) -> None:
        # Overlapping 2000-row chunk boundaries and IFTTT re-sends. The primary
        # key keeps the first copy; the absorbed row goes to `rejects` so it stays
        # inspectable rather than vanishing.
        rows = [*aisvn_rows(), aisvn_rows()[0]]
        summary = self.build({"aisvn": rows}, {"aisvn": AISVN_HEADER})
        self.assertEqual(summary.rows_ingested, 6)
        self.assertEqual(summary.rows_duplicate, 1)
        conn = self.connect()
        self.addCleanup(conn.close)
        reject = conn.execute(
            "SELECT reason, raw_value, column_name FROM rejects WHERE reason = 'duplicate_ts'"
        ).fetchone()
        self.assertEqual(reject["raw_value"], "June 18, 2020 at 08:52AM")
        self.assertEqual(reject["column_name"], "time")

    def test_the_same_instant_at_two_stations_is_not_a_duplicate(self) -> None:
        # ~121,000 timestamps are shared *between* stations: separate instruments
        # sampling the same instant, and both readings are real.
        self.build(
            {
                "aisvn": aisvn_rows(),
                "aisvn2": [["June 18, 2020 at 08:52AM", 3000, 12000, 5, 5, 7000, 0, 500]],
            },
            {
                "aisvn": AISVN_HEADER,
                "aisvn2": [
                    "time",
                    "solar3",
                    "battery2",
                    "currentA",
                    "currentB",
                    "LiPo2",
                    "load",
                    "boot",
                ],
            },
        )
        conn = self.connect()
        self.addCleanup(conn.close)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM rejects WHERE reason = 'duplicate_ts'").fetchone()[
                0
            ],
            0,
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM s_aisvn2").fetchone()[0], 1)


class TestWindowsAndExclusions(TempArchiveCase):
    def test_an_excluded_file_records_every_row_and_ingests_none(self) -> None:
        # 0.8's `test/IFTTT_test (1).xlsx` exclusion: 4,121 rows, each with its
        # sheet row and timestamp, so the decision is inspectable one cell at a
        # time rather than taken on trust from a prose exclusion.
        from etl import build_db

        rows = [
            ["July 1, 2020 at 06:18PM", 96, 29.47, 5495],
            ["July 1, 2020 at 06:20PM", 95, 29.40, 5400],
        ]
        write_xlsx(
            self.raw / "test" / "IFTTT_test (1).xlsx", ["time", "nix", "temp_c", "wifi_tx_ms"], rows
        )
        xlsx.clear_read_cache()
        summary = build_db.ingest(self.settings, verbose=False)
        self.assertEqual(summary.rows_ingested, 0)
        self.assertEqual(summary.rows_rejected, 2)
        conn = self.connect()
        self.addCleanup(conn.close)
        rejects = conn.execute(
            "SELECT sheet_row, raw_value, reason FROM rejects ORDER BY sheet_row"
        ).fetchall()
        self.assertEqual([r["reason"] for r in rejects], ["station_setup"] * 2)
        self.assertEqual(
            rejects[0]["sheet_row"], 2, "sheet rows are 1-based as openpyxl reports them"
        )
        self.assertEqual(rejects[0]["raw_value"], "July 1, 2020 at 06:18PM")
        note = conn.execute("SELECT note FROM notes").fetchone()
        self.assertIn("not measurements", note["note"])
        self.assertIn("2020-07-01", note["note"], "the prose is the collector's, kept verbatim")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0], 1)

    def test_a_null_window_only_corrects_a_cell_that_had_a_value(self) -> None:
        # The 0.9 correction to 0.8: a blank cell is already a gap and needs
        # nothing. Flagging it would assert that the collector said the input was
        # disconnected when the sheet says nothing at all, which is a different
        # claim. 1,359 of aisvn's rows in the temp window are blank at source.
        from etl import build_db

        in_window = "June 15, 2020 at 01:00PM"  # 08:00 UTC, inside the window
        rows = [
            [in_window, 14.0, 12.5, 0, 0, 0, 0, "", 0, 0, 1],  # temp is blank
            ["June 15, 2020 at 01:02PM", 14.0, 12.5, 0, 0, 0, 0, 32.0, 0, 0, 2],  # real
        ]
        write_xlsx(self.raw / "aisvn" / "IFTTT_test.xlsx", AISVN_HEADER, rows)
        xlsx.clear_read_cache()
        build_db.ingest(self.settings, verbose=False)
        conn = self.connect()
        self.addCleanup(conn.close)
        blank = conn.execute("SELECT temp_c, flags FROM s_aisvn WHERE sheet_row = 2").fetchone()
        self.assertIsNone(blank["temp_c"])
        self.assertEqual(blank["flags"], "", "a blank cell is a gap and carries no flag")
        real = conn.execute("SELECT temp_c, flags FROM s_aisvn WHERE sheet_row = 3").fetchone()
        self.assertAlmostEqual(real["temp_c"], 32.0)
        self.assertEqual(real["flags"], "")

    def test_a_bad_window_keeps_the_value_and_flags_it(self) -> None:
        from etl import build_db

        rows = [["October 25, 2020 at 12:00PM", 14.0, 12.5, 0, 0, 0, 0, 32.0, 0, 0, 1]]
        write_xlsx(self.raw / "aisvn" / "IFTTT_test.xlsx", AISVN_HEADER, rows)
        xlsx.clear_read_cache()
        build_db.ingest(self.settings, verbose=False)
        conn = self.connect()
        self.addCleanup(conn.close)
        row = conn.execute("SELECT solar_v, battery_v, temp_c, flags FROM s_aisvn").fetchone()
        self.assertAlmostEqual(row["solar_v"], 14.0, msg="kept")
        self.assertIn("bad_window:solar_v", row["flags"])
        self.assertIn("bad_window:battery_v", row["flags"])
        self.assertIn("bad_window:temp_c", row["flags"])
        self.assertNotIn("bad_window:wind_v", row["flags"], "only the named channels")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM rejects WHERE reason = 'null_window'").fetchone()[0],
            0,
            "a bad window flags; it does not null",
        )

    def test_a_row_floor_rejects_earlier_rows(self) -> None:
        from etl import build_db

        # 103 body rows, because the floor is at sheet row 102 and the fixture has
        # to straddle it to prove anything.
        times = aisvn_times(103)
        rows = [
            [times[index - 1], 14.0, 12.5, 0, 0, 0, 0, 32.0, 0, 0, index] for index in range(1, 104)
        ]
        write_xlsx(self.raw / "aisvn" / "IFTTT_aisvn (25).xlsx", AISVN_HEADER, rows)
        xlsx.clear_read_cache()
        build_db.ingest(self.settings, verbose=False)
        conn = self.connect()
        self.addCleanup(conn.close)
        # Rows 2..101 are below the floor: 100 of them, which is the archive's
        # recorded pre_reinstall count exactly.
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM s_aisvn").fetchone()[0], 3)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM rejects WHERE reason = 'pre_reinstall'").fetchone()[
                0
            ],
            100,
        )
        first = conn.execute(
            "SELECT MIN(sheet_row), MAX(sheet_row) FROM rejects WHERE reason = 'pre_reinstall'"
        ).fetchone()
        self.assertEqual((first[0], first[1]), (2, 101), "the floor is at sheet row 102")

    def test_a_repeated_header_inside_a_file_is_rejected(self) -> None:
        rows = [
            ["June 18, 2020 at 08:52AM", 14.0, 12.5, 0, 0, 0, 0, 32.0, 0, 0, 1],
            ["time", 14.0, 12.5, 0, 0, 0, 0, 32.0, 0, 0, 2],
        ]
        self.build({"aisvn": rows})
        conn = self.connect()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM s_aisvn").fetchone()[0], 1)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM rejects WHERE reason = 'repeated_header'"
            ).fetchone()[0],
            1,
        )


class TestRebuildFromScratch(TempArchiveCase):
    def test_a_second_ingest_replaces_rather_than_appends(self) -> None:
        # No incremental path, and that is the point: an incremental update has to
        # be right about which rows changed and what a partial failure left behind,
        # and every one of those is a way to end up with a database that looks
        # complete and is not. The build is a minute and the archive is immutable.
        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        first = self.connect()
        first.close()
        self.build({"aisvn": aisvn_rows()[:2]}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM s_aisvn").fetchone()[0],
            2,
            "the second build replaced the first rather than adding to it",
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ingest_runs").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0], 1)

    def test_provenance_records_the_sha256_of_every_file(self) -> None:
        import hashlib

        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        row = conn.execute("SELECT sha256, bytes, rel_path FROM source_files").fetchone()
        path = self.raw / "aisvn" / "IFTTT_test.xlsx"
        self.assertEqual(row["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(row["bytes"], path.stat().st_size)
        self.assertEqual(row["rel_path"], "aisvn/IFTTT_test.xlsx")

    def test_sheet_row_is_the_row_a_person_would_count(self) -> None:
        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        rows = conn.execute("SELECT sheet_row FROM s_aisvn ORDER BY ts_utc").fetchall()
        self.assertEqual([r["sheet_row"] for r in rows], [2, 3, 4, 5, 6, 7])


class TestMultipleStations(TempArchiveCase):
    def test_three_archive_folders_are_one_station(self) -> None:
        # `phumy2` is split across phumy2/, phumy2a/ and phumy2b/ because Google
        # Sheets split the sheet at 2000 rows. Three folders, one instrument, one
        # table -- and the coverage is continuous across all three.
        header = ["time", "solar2", "current2", "power", "temp", "LiPo2", "boot"]
        self.build(
            {
                "phumy2": [["June 18, 2020 at 08:52AM", 3000, 232, 0, 32.0, 4000, 1]],
                "phumy2a": [["June 18, 2020 at 08:54AM", 3100, 233, 0, 32.1, 4001, 2]],
                "phumy2b": [["June 18, 2020 at 08:56AM", 3200, 234, 0, 32.2, 4002, 3]],
            },
            {"phumy2": header, "phumy2a": header, "phumy2b": header},
        )
        conn = self.connect()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM s_phumy2").fetchone()[0], 3)
        # And the millivolt/milliamp channels are in their published units.
        row = conn.execute(
            "SELECT solar2_v, current2_a, temp_c, lipo2_v FROM s_phumy2 ORDER BY ts_utc LIMIT 1"
        ).fetchone()
        self.assertAlmostEqual(row["solar2_v"], 3.0)
        self.assertAlmostEqual(row["current2_a"], 0.232)
        self.assertAlmostEqual(row["temp_c"], 32.0)
        self.assertAlmostEqual(row["lipo2_v"], 4.0)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM rejects WHERE reason = 'out_of_range'").fetchone()[
                0
            ],
            0,
        )

    def test_an_unknown_folder_is_reported_not_ingested(self) -> None:
        self.build({"not_a_station": aisvn_rows()}, {"not_a_station": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0], 0)
        run = conn.execute("SELECT notes FROM ingest_runs").fetchone()
        self.assertIn("not_a_station", run["notes"])

    def test_coverage_is_filled_in_per_station_table(self) -> None:
        self.build(
            {
                "aisvn": aisvn_rows(),
                "aisvn2": [["June 18, 2020 at 08:52AM", 3000, 12000, 5, 5, 7000, 0, 500]],
            },
            {
                "aisvn": AISVN_HEADER,
                "aisvn2": [
                    "time",
                    "solar3",
                    "battery2",
                    "currentA",
                    "currentB",
                    "LiPo2",
                    "load",
                    "boot",
                ],
            },
        )
        conn = self.connect()
        self.addCleanup(conn.close)
        rows = conn.execute(
            "SELECT station_id, first_ts_utc, last_ts_utc, n_readings FROM stations"
            " WHERE n_readings > 0 ORDER BY station_id"
        ).fetchall()
        self.assertEqual([r["station_id"] for r in rows], ["aisvn", "aisvn2"])
        self.assertEqual(rows[0]["n_readings"], 6)
        self.assertEqual(rows[1]["n_readings"], 1)
        self.assertEqual(rows[0]["first_ts_utc"], "2020-06-18T01:52:00Z")
        self.assertEqual(rows[0]["last_ts_utc"], "2020-06-18T02:02:00Z")


class TestReaderCache(TempArchiveCase):
    def test_a_sheet_is_parsed_once_per_build(self) -> None:
        # Reading a sheet is 86% of the whole build, and 0.8 parsed each file
        # three times: once to scan it, once in detect_block and once in
        # iter_cells. 1,820 reads for 364 files. The cache is the whole reason a
        # build is a minute rather than three.
        self.build({"aisvn": aisvn_rows()}, {"aisvn": AISVN_HEADER})
        after = xlsx.cached_read_count()
        path = self.raw / "aisvn" / "IFTTT_test.xlsx"
        for _ in range(20):
            xlsx.detect_block(path)
            list(xlsx.iter_cells(path, xlsx.detect_block(path)))
        self.assertEqual(xlsx.cached_read_count(), after, "twenty more reads, no more parses")

    def test_the_cache_notices_a_file_rewritten_in_place(self) -> None:
        # The test suite writes a fixture, reads it, writes it again. A cache keyed
        # on the path alone would hand back the first version's rows, and the
        # second ingest would silently read stale data.
        path = self.raw / "aisvn" / "IFTTT_test.xlsx"
        write_xlsx(path, AISVN_HEADER, aisvn_rows()[:1])
        self.assertEqual(xlsx.cached_read_count(), 0)
        first = xlsx.detect_block(path)
        write_xlsx(path, AISVN_HEADER, aisvn_rows()[:2])
        second = xlsx.detect_block(path)
        self.assertNotEqual(first.n_rows, second.n_rows)

    def test_clear_read_cache_bounds_the_memory(self) -> None:
        path = self.raw / "aisvn" / "IFTTT_test.xlsx"
        write_xlsx(path, AISVN_HEADER, aisvn_rows())
        xlsx.detect_block(path)
        self.assertEqual(xlsx.cached_read_count(), 1)
        xlsx.clear_read_cache()
        self.assertEqual(xlsx.cached_read_count(), 0)


class TestSideBlocks(TempArchiveCase):
    def test_prose_in_a_side_block_becomes_a_note(self) -> None:
        # Voltage_phumy carries a hand-made discharge summary and the lab's own
        # annotations to the right of the primary block. `iter_all_cells` exists
        # solely to recover that prose.
        write_xlsx(
            self.raw / "Voltage_phumy" / "IFTTT_test.xlsx",
            ["time", "raw", "voltage", "millis()"],
            [
                ["July 12, 2020 at 10:02AM", 2500, 2200, 1000, "STROMAUSFALL!!"],
                ["July 12, 2020 at 10:04AM", 2501, 2201, 2000, "leave home"],
            ],
        )
        xlsx.clear_read_cache()
        from etl import build_db

        build_db.ingest(self.settings, verbose=False)
        conn = self.connect()
        self.addCleanup(conn.close)
        notes = conn.execute("SELECT note, ts_utc, column_name FROM notes ORDER BY note").fetchall()
        self.assertEqual(
            [n["note"] for n in notes],
            ["STROMAUSFALL!!", "leave home"],
            "short prose is recovered; a length threshold would have dropped it",
        )
        self.assertEqual(notes[0]["column_name"], "E")
        self.assertIsNotNone(notes[0]["ts_utc"], "and it is anchored to an instant")

    def test_a_side_block_header_is_not_prose(self) -> None:
        # Without this, maker-webhooks contributes twenty-two notes that are all
        # just the words "boot", "solar" and "battery".
        write_xlsx(
            self.raw / "Maker_Webhooks_Events" / "IFTTT_test.xlsx",
            ["time", "solar", "battery", "curA", "curB", "load", "wind", "dump", "LiPo", "boot"],
            [
                [
                    "June 1, 2020 at 10:00AM",
                    1000,
                    12000,
                    900,
                    900,
                    3000,
                    500,
                    600,
                    4000,
                    1,
                    "solar",
                    "battery",
                    "load",
                    "wind",
                    "LiPo",
                    "boot",
                ]
            ],
        )
        xlsx.clear_read_cache()
        from etl import build_db

        build_db.ingest(self.settings, verbose=False)
        conn = self.connect()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

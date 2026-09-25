"""End-to-end ingest against a synthetic archive that mimics the real problems.

The fixtures are written as real XLSX files so the tests exercise the same
openpyxl path as the production ingest.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import openpyxl
from etl.build_db import ingest
from etl.config import Settings
from etl.db import connect
from etl.readers.xlsx import detect_block, iter_cells
from etl.stations import station_for_dir


def write_xlsx(path: Path, rows: list[list], sheet: str = "Sheet1") -> None:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    for row in rows:
        worksheet.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


HEADER_ROW = [
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


def data_row(minute: int, *, day: int = 14, month: str = "July", year: int = 2020, **over):
    base = {
        "time": f"{month} {day}, {year} at 10:{minute:02d}AM",
        "solar": 20.0,
        "battery": 14.4,
        "current": -6.4,
        "power": -124.0,
        "load": 13.8,
        "wind": 0.0,
        "temp": 34.0,
        "solar2": 11.5,
        "LiPo": 4.1,
        "boot": 100 + minute,
    }
    base.update(over)
    return [base[k] for k in HEADER_ROW]


class TestBlockDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_header_file_block_ends_at_the_empty_separator(self):
        path = self.tmp / "with_side_block.xlsx"
        write_xlsx(
            path,
            [
                [*HEADER_ROW, None, "time", "solar", "battery"],
                [*data_row(10), None, "July 14, 2020 at 10:10AM", 20.0, 14.4],
            ],
        )
        block = detect_block(path)
        self.assertIsNotNone(block.header)
        self.assertEqual(block.n_columns, len(HEADER_ROW))
        self.assertEqual(block.extra_blocks, 1)
        # The side block must not leak into the primary block.
        cells = next(iter_cells(path, block))[1]
        self.assertEqual(len(cells), len(HEADER_ROW))
        self.assertEqual(float(cells[1]), 20.0)

    def test_headerless_file_uses_full_width(self):
        path = self.tmp / "headerless.xlsx"
        write_xlsx(path, [data_row(10), data_row(12)])
        block = detect_block(path)
        self.assertIsNone(block.header)
        self.assertEqual(block.n_columns, len(HEADER_ROW))
        self.assertEqual(block.n_rows, 2)

    def test_repeated_mid_file_header_is_counted(self):
        path = self.tmp / "repeated.xlsx"
        write_xlsx(path, [HEADER_ROW, data_row(10), HEADER_ROW, data_row(12)])
        block = detect_block(path)
        self.assertEqual(block.repeated_headers, [2])

    def test_three_block_voltage_sheet(self):
        # Mirrors data/raw/Voltage_phumy/Voltage_phumy.xlsx
        path = self.tmp / "three_blocks.xlsx"
        write_xlsx(
            path,
            [
                ["time", "raw", "voltage", "millis()", None, "time", "time", "voltage"],
                ["July 5, 2020 at 04:08AM", 2303, 1980, 2204, None, "04:08AM", "04:08:00", 11.286],
            ],
        )
        block = detect_block(path)
        self.assertEqual(block.n_columns, 4)
        self.assertEqual(block.extra_blocks, 2)

    def test_empty_file(self):
        path = self.tmp / "empty.xlsx"
        write_xlsx(path, [])
        block = detect_block(path)
        self.assertIsNone(block.header)
        self.assertEqual(block.n_rows, 0)


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        raw = self.tmp / "raw"
        out = self.tmp / "out"
        exports = self.tmp / "exports"
        self.settings = Settings(
            raw_dir=raw,
            out_dir=out,
            db_path=out / "solardata.db",
            parquet_dir=out / "parquet",
            export_dir=exports,
            report_json=out / "q.json",
            report_md=out / "q.md",
        )

        aisvn = raw / "aisvn"
        # 1. a file WITH a header
        write_xlsx(aisvn / "IFTTT_aisvn (1).xlsx", [HEADER_ROW, data_row(10), data_row(12)])
        # 2. a file WITHOUT a header, same layout
        write_xlsx(aisvn / "IFTTT_aisvn (2).xlsx", [data_row(14), data_row(16)])
        # 3. an overlapping chunk that repeats a timestamp and adds a new one
        write_xlsx(
            aisvn / "IFTTT_aisvn (3).xlsx",
            [data_row(16), data_row(18), data_row(20, power=-992.0, wind=-992.0)],
        )
        # 4. a file with a junk row and a repeated header
        write_xlsx(
            aisvn / "IFTTT_aisvn (4).xlsx",
            [
                HEADER_ROW,
                data_row(22),
                HEADER_ROW,
                ["not a time", 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                data_row(24),
            ],
        )
        # 5. a file with an embedded lab note
        write_xlsx(
            aisvn / "IFTTT_aisvn (5).xlsx",
            [
                HEADER_ROW,
                data_row(26, power="discharge 7.5 Ah with 0.3A - 25 hours, starting at 18:00"),
                data_row(28),
            ],
        )

    def test_ingest_counts(self):
        summary = ingest(self.settings, verbose=False)
        self.assertEqual(summary.files, 5)
        # Body rows: 2 + 2 + 3 + 4 + 2 = 13.
        #   -1  duplicate timestamp at 10:16 shared between files 2 and 3
        #   -2  rejected rows: the repeated header and the unparseable timestamp
        # The duplicate is itself recorded in `rejects`, so it counts there too.
        self.assertEqual(summary.rows_ingested, 10)
        self.assertEqual(summary.rows_duplicate, 1)
        self.assertEqual(summary.rows_rejected, 3)
        self.assertEqual(summary.notes, 1)
        self.assertEqual(summary.failed, 0)

    def test_header_presence_is_recorded(self):
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        rows = conn.execute(
            "SELECT filename, has_header, extra_blocks FROM source_files ORDER BY filename"
        ).fetchall()
        conn.close()
        by_name = {r["filename"]: r for r in rows}
        self.assertEqual(by_name["IFTTT_aisvn (1).xlsx"]["has_header"], 1)
        self.assertEqual(by_name["IFTTT_aisvn (2).xlsx"]["has_header"], 0)
        self.assertEqual(by_name["IFTTT_aisvn (1).xlsx"]["extra_blocks"], 0)

    def test_readings_are_typed_and_sentinel_safe(self):
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        row = conn.execute("SELECT * FROM readings WHERE power_w = -124.0 LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["station_id"], "aisvn")
        self.assertIsInstance(row["battery_v"], float)
        self.assertEqual(row["ts_utc"], "2020-07-14T03:10:00Z")
        self.assertEqual(row["ts_local"], "2020-07-14T10:10:00")
        self.assertEqual(row["tz"], "Asia/Ho_Chi_Minh")

        # The -992 row is stored as NULL, not -992 and not 0.
        nulled = conn.execute(
            "SELECT power_w, wind_v, quality_flags FROM readings WHERE ts_utc = ?",
            ("2020-07-14T03:20:00Z",),
        ).fetchone()
        self.assertIsNone(nulled["power_w"])
        self.assertIsNone(nulled["wind_v"])
        self.assertIn("sentinel", nulled["quality_flags"])

        # Zero is preserved as a real measurement.
        zero = conn.execute(
            "SELECT wind_v FROM readings WHERE ts_utc = ?", ("2020-07-14T03:10:00Z",)
        ).fetchone()
        self.assertEqual(zero["wind_v"], 0.0)
        conn.close()

    def test_provenance_is_traceable(self):
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        row = conn.execute(
            "SELECT r.sheet_row, f.rel_path FROM readings r"
            " JOIN source_files f ON f.file_id = r.source_file_id"
            " WHERE r.ts_utc = ?",
            ("2020-07-14T03:10:00Z",),
        ).fetchone()
        self.assertEqual(row["rel_path"], "aisvn/IFTTT_aisvn (1).xlsx")
        self.assertEqual(row["sheet_row"], 2)
        conn.close()

    def test_metric_defs_marks_inferred_columns(self):
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        rows = conn.execute(
            "SELECT col_index, raw_name, canonical_col, inferred, n_files FROM metric_defs"
            " WHERE source_dir = 'aisvn' ORDER BY col_index"
        ).fetchall()
        conn.close()
        # 5 files, 1 with a header -> the header-derived name sticks.
        self.assertEqual(len(rows), len(HEADER_ROW) - 1)
        self.assertEqual(rows[0]["canonical_col"], "solar_v")
        self.assertEqual(rows[0]["raw_name"], "solar")
        self.assertEqual(rows[0]["n_files"], 5)

    def test_notes_and_rejects_are_recorded(self):
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        note = conn.execute("SELECT note FROM notes").fetchone()
        self.assertIn("discharge 7.5 Ah", note["note"])
        reasons = {r["reason"] for r in conn.execute("SELECT reason FROM rejects")}
        self.assertTrue(any("repeated header" in r for r in reasons))
        self.assertTrue(any("unparseable" in r for r in reasons))
        conn.close()

    def test_duplicate_timestamps_are_traceable_not_just_counted(self):
        # The (station_id, ts_utc) primary key absorbs duplicates, but rule 2 in
        # AGENTS.md says a dropped row must still be traceable.  The 4,403
        # duplicates in the real archive have to be inspectable individually.
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        rows = conn.execute(
            "SELECT f.rel_path, r.sheet_row, r.raw_value, r.reason"
            " FROM rejects r JOIN source_files f ON f.file_id = r.file_id"
            " WHERE r.reason = 'duplicate_ts'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rel_path"], "aisvn/IFTTT_aisvn (3).xlsx")
        self.assertEqual(rows[0]["raw_value"], "July 14, 2020 at 10:16AM")
        # `reason` stays a stable category so the report can group by it; the
        # instant lives in raw_value and the station in its own column.
        self.assertEqual(rows[0]["reason"], "duplicate_ts")
        # And the reading itself is still present exactly once.
        count = conn.execute(
            "SELECT COUNT(*) FROM readings WHERE station_id='aisvn' AND ts_utc=?",
            ("2020-07-14T03:16:00Z",),
        ).fetchone()[0]
        self.assertEqual(count, 1)
        conn.close()

    def test_aggregates_are_built(self):
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        hourly = conn.execute("SELECT * FROM readings_hourly").fetchone()
        daily = conn.execute("SELECT * FROM readings_daily").fetchone()
        self.assertEqual(hourly["n_samples"], 10)
        self.assertEqual(daily["n_samples"], 10)
        self.assertEqual(daily["day"], "2020-07-14")
        self.assertAlmostEqual(daily["energy_wh"], hourly["energy_wh"], places=6)
        conn.close()

    def test_station_coverage_is_filled_in(self):
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        station = conn.execute(
            "SELECT first_ts_utc, last_ts_utc, n_readings FROM stations WHERE station_id = 'aisvn'"
        ).fetchone()
        conn.close()
        self.assertEqual(station["first_ts_utc"], "2020-07-14T03:10:00Z")
        self.assertEqual(station["last_ts_utc"], "2020-07-14T03:28:00Z")
        self.assertEqual(station["n_readings"], 10)

    def test_unknown_folders_are_skipped_not_crashed(self):
        stray = self.settings.raw_dir / "not_a_station"
        write_xlsx(stray / "x.xlsx", [HEADER_ROW, data_row(30)])
        summary = ingest(self.settings, verbose=False)
        self.assertEqual(summary.unknown_dirs, ["not_a_station"])
        self.assertEqual(summary.rows_ingested, 10)  # unchanged


class TestHeaderlessSchemaInheritance(unittest.TestCase):
    """The regression that mattered: headerless files must not lose their data.

    328 of the 364 raw files have no header row.  If the pipeline does not give
    them a schema, it ingests timestamps and silently discards every
    measurement -- which is 90% of the archive.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = Settings(
            raw_dir=self.tmp / "raw",
            out_dir=self.tmp / "out",
            db_path=self.tmp / "out" / "solardata.db",
            parquet_dir=self.tmp / "out" / "parquet",
            export_dir=self.tmp / "exports",
            report_json=self.tmp / "out" / "q.json",
            report_md=self.tmp / "out" / "q.md",
        )
        folder = self.settings.raw_dir / "aisvn"
        # Only the first file carries a header; the next three do not.
        write_xlsx(folder / "IFTTT_aisvn (1).xlsx", [HEADER_ROW, data_row(10), data_row(12)])
        write_xlsx(folder / "IFTTT_aisvn (2).xlsx", [data_row(14), data_row(16)])
        write_xlsx(folder / "IFTTT_aisvn (3).xlsx", [data_row(18), data_row(20)])

    def test_headerless_rows_get_measurements_not_just_timestamps(self):
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        row = conn.execute(
            "SELECT solar_v, battery_v, temp_c, boot_count FROM readings WHERE ts_utc = ?",
            ("2020-07-14T03:14:00Z",),  # from the headerless file (2)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["solar_v"], 20.0)
        self.assertEqual(row["battery_v"], 14.4)
        self.assertEqual(row["temp_c"], 34.0)
        self.assertEqual(row["boot_count"], 114)

    def test_donor_is_recorded_for_audit(self):
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        rows = conn.execute(
            "SELECT filename, has_header, schema_donor, inferred FROM source_files"
            " ORDER BY filename"
        ).fetchall()
        conn.close()
        by_name = {r["filename"]: r for r in rows}
        self.assertIsNone(by_name["IFTTT_aisvn (1).xlsx"]["schema_donor"])
        self.assertEqual(by_name["IFTTT_aisvn (1).xlsx"]["inferred"], 0)
        for name in ("IFTTT_aisvn (2).xlsx", "IFTTT_aisvn (3).xlsx"):
            self.assertEqual(by_name[name]["has_header"], 0)
            self.assertEqual(by_name[name]["inferred"], 1)
            self.assertEqual(by_name[name]["schema_donor"], "aisvn/IFTTT_aisvn (1).xlsx")

    def test_donor_is_the_nearest_preceding_file_not_the_first(self):
        # A second, later header-bearing file must take over as donor.
        folder = self.settings.raw_dir / "aisvn"
        late_header = [
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
        write_xlsx(
            folder / "IFTTT_aisvn (4).xlsx",
            [late_header, data_row(22, day=20, month="August")],
        )
        write_xlsx(
            folder / "IFTTT_aisvn (5).xlsx",
            [data_row(24, day=21, month="August"), data_row(26, day=21, month="August")],
        )
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        row = conn.execute(
            "SELECT schema_donor FROM source_files WHERE filename = 'IFTTT_aisvn (5).xlsx'"
        ).fetchone()
        conn.close()
        # August inherits from the August header, not the July one.
        self.assertEqual(row["schema_donor"], "aisvn/IFTTT_aisvn (4).xlsx")

    def test_narrower_donor_does_not_overflow_the_file(self):
        # A 4-column file inheriting an 11-column header keeps 3 metrics.
        folder = self.settings.raw_dir / "test"
        write_xlsx(
            folder / "IFTTT_test (2).xlsx",
            [["time", "nix", "temp", "wifi"], ["July 5, 2020 at 11:40AM", 96, 29.47, 5520]],
        )
        write_xlsx(
            folder / "IFTTT_test (3).xlsx",
            [["July 8, 2020 at 07:11AM", 84, 25.79, 3952]],
        )
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        row = conn.execute(
            "SELECT nix_raw, wifi_raw, solar_v, battery_v FROM readings"
            " WHERE station_id = 'test' AND ts_utc = ?",
            ("2020-07-08T00:11:00Z",),
        ).fetchone()
        conn.close()
        self.assertEqual(row["nix_raw"], 84)
        self.assertEqual(row["wifi_raw"], 3952)
        self.assertIsNone(row["solar_v"])
        self.assertIsNone(row["battery_v"])


class TestSideBlockNoteRecovery(unittest.TestCase):
    """Lab notes live in spare side-block columns and must not be lost.

    data/raw/Voltage_phumy/Voltage_phumy (2).xlsx holds the discharge-test
    annotation in column H, which is outside the primary A:D block.  Reading only
    the primary block -- which is correct for measurements -- would drop it.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = Settings(
            raw_dir=self.tmp / "raw",
            out_dir=self.tmp / "out",
            db_path=self.tmp / "out" / "solardata.db",
            parquet_dir=self.tmp / "out" / "parquet",
            export_dir=self.tmp / "exports",
            report_json=self.tmp / "out" / "q.json",
            report_md=self.tmp / "out" / "q.md",
        )
        folder = self.settings.raw_dir / "Voltage_phumy"
        # The archive really does contain a headered sibling; it is what lets
        # the headerless annotation file borrow its column meaning.
        write_xlsx(
            folder / "Voltage_phumy.xlsx",
            [
                ["time", "raw", "voltage", "millis()", None, "time", "time", "voltage"],
                ["July 5, 2020 at 04:08AM", 2303, 1980, 2204, None, "04:08AM", "04:08:00", 11.286],
            ],
        )
        note = "discharge 7.5 Ah with 0.3A - 25 hours, starting at 18:00 on July 11th"
        write_xlsx(
            folder / "Voltage_phumy (2).xlsx",
            [
                # primary block A:D, blank E, side block F..H
                [
                    "July 12, 2020 at 10:00AM",
                    2853,
                    2449,
                    631431035,
                    None,
                    "10:00AM",
                    "10:00:00",
                    11.2,
                ],
                [
                    "July 12, 2020 at 10:02AM",
                    3091,
                    2511,
                    631553494,
                    None,
                    "10:02AM",
                    "10:02:00",
                    note,
                ],
            ],
        )

    def test_note_in_a_side_block_is_recovered_and_anchored(self):
        summary = ingest(self.settings, verbose=False)
        self.assertEqual(summary.notes, 1)
        conn = connect(self.settings.db_path, read_only=True)
        row = conn.execute("SELECT station_id, ts_utc, column_name, note FROM notes").fetchone()
        conn.close()
        self.assertEqual(row["station_id"], "voltage-phumy")
        self.assertEqual(row["column_name"], "H")
        self.assertIn("discharge 7.5 Ah", row["note"])
        # Anchored to the reading on the same sheet row.
        self.assertEqual(row["ts_utc"], "2020-07-12T03:02:00Z")

    def test_side_block_values_do_not_become_measurements(self):
        ingest(self.settings, verbose=False)
        conn = connect(self.settings.db_path, read_only=True)
        row = conn.execute(
            "SELECT battery_v, adc_raw, voltage_adc FROM readings WHERE ts_utc = ?",
            ("2020-07-12T03:00:00Z",),
        ).fetchone()
        conn.close()
        # Column B -> adc_raw, column C -> voltage_adc; the side block's 11.2 V
        # must not appear in any canonical voltage column.
        self.assertEqual(row["adc_raw"], 2853)
        self.assertEqual(row["voltage_adc"], 2449)
        self.assertIsNone(row["battery_v"])


class TestStationRegistry(unittest.TestCase):
    def test_phumy_chunks_map_to_one_station(self):
        for folder in ("phumy2", "phumy2a", "phumy2b"):
            self.assertEqual(station_for_dir(folder).station_id, "phumy2")

    def test_bench_stations_are_flagged_non_production(self):
        self.assertEqual(station_for_dir("test").station_id, "test")
        self.assertEqual(
            station_for_dir("test").station_id
            in __import__("etl.stations", fromlist=["x"]).NON_PRODUCTION,
            True,
        )

    def test_every_registry_station_declares_its_timezone(self):
        from zoneinfo import ZoneInfo

        from etl.stations import STATIONS

        for station in STATIONS:
            ZoneInfo(station.tz)  # raises if the zone name is wrong


if __name__ == "__main__":
    unittest.main()

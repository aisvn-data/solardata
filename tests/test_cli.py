"""The CLI and the baseline guard.

Two things are tested here that no other layer can catch:

* ``tests/test_ingest.py`` proves the pipeline ingests correctly from fixtures.
  These tests prove it still *agrees with the real archive* (``etl.verify``),
  which is the only check that sees 735,004 rows.
* argparse silently discards a flag given before the subcommand unless the
  shared parser uses ``SUPPRESS``.  That failure is invisible until someone
  runs ``python -m etl --out-dir /tmp ingest`` and gets the wrong database, so
  it gets a test.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from etl import __version__
from etl.cli import _apply_flag_defaults, _settings, build_parser
from etl.db import SCHEMA_PATH
from etl.verify import BASELINE_FIELDS, check, measure, render, write


def _args(argv: list[str]):
    args = build_parser().parse_args(argv)
    _apply_flag_defaults(args)
    return args


class TestFlagParsing(unittest.TestCase):
    """Flags must work on either side of the subcommand."""

    def test_flags_before_subcommand_are_not_discarded(self):
        # Regression: argparse's subparser re-applies its own defaults to the
        # namespace, clobbering anything parsed by the parent.  Every one of
        # these silently resolved to the default before the fix.
        settings = _settings(_args(["--out-dir", r"C:\tmp\out1", "ingest"]))
        self.assertEqual(settings.out_dir, Path(r"C:\tmp\out1"))
        # and every derived artefact must follow it
        self.assertEqual(settings.db_path, Path(r"C:\tmp\out1") / "solardata.db")
        self.assertEqual(settings.report_md, Path(r"C:\tmp\out1") / "quality_report.md")

    def test_flags_after_subcommand_still_work(self):
        settings = _settings(_args(["ingest", "--out-dir", r"C:\tmp\out2"]))
        self.assertEqual(settings.out_dir, Path(r"C:\tmp\out2"))

    def test_both_sides_agree(self):
        for argv in (
            ["-q", "ingest"],
            ["ingest", "-q"],
        ):
            self.assertTrue(_args(argv).quiet, argv)

    def test_raw_dir_and_export_dir_from_either_side(self):
        for argv in (
            ["--raw-dir", r"C:\tmp\r", "ingest"],
            ["ingest", "--raw-dir", r"C:\tmp\r"],
        ):
            self.assertEqual(_settings(_args(argv)).raw_dir, Path(r"C:\tmp\r"), argv)
        for argv in (
            ["--export-dir", r"C:\tmp\e", "export"],
            ["export", "--export-dir", r"C:\tmp\e"],
        ):
            self.assertEqual(_settings(_args(argv)).export_dir, Path(r"C:\tmp\e"), argv)

    def test_baseline_override_from_either_side(self):
        for argv in (
            ["--baseline", r"C:\tmp\b.json", "verify"],
            ["verify", "--baseline", r"C:\tmp\b.json"],
        ):
            self.assertEqual(_settings(_args(argv)).baseline_path, Path(r"C:\tmp\b.json"), argv)

    def test_defaults_when_nothing_is_passed(self):
        settings = _settings(_args(["ingest"]))
        self.assertFalse(settings.only)
        self.assertEqual(settings.export_granularity, "hour")
        self.assertTrue(settings.duplicate_policy)

    def test_every_subcommand_has_a_handler(self):
        for command in (
            "ingest",
            "regimes",
            "parquet",
            "export",
            "report",
            "all",
            "query",
            "verify",
        ):
            args = _args([command] if command != "query" else [command, "SELECT 1"])
            self.assertTrue(hasattr(args, "func"), command)


class TestBaselineGuard(unittest.TestCase):
    """The guard logic, exercised against a real in-memory database.

    Using the actual ``schema.sql`` matters: the whole point of the baseline is
    to notice when a query silently stops counting something, so the test must
    run the real SQL rather than a mock of it.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.baseline = self.tmp / "baseline.json"
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self._populate()

    def tearDown(self):
        self.conn.close()

    def _populate(self, **overrides):
        """Insert a small but structurally complete database.

        The absolute numbers here are deliberately small; the *real* 735,004-row
        figures are asserted separately in ``TestCommittedBaseline`` and by CI.
        What this test needs is for every guard query to run against real rows.
        Idempotent, so a test may re-populate with different counts.
        """
        counts = self._default_counts()
        counts.update(overrides)
        for table in (
            "readings_daily",
            "readings_hourly",
            "regimes",
            "notes",
            "rejects",
            "readings",
            "source_files",
            "stations",
        ):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.executemany(
            "INSERT INTO stations (station_id, display_name, tz, source_dirs) VALUES (?, ?, 'UTC', '[]')",
            [(f"s{i}", f"Station {i}") for i in range(counts["stations"])],
        )
        # Every source file is headerless; the ones that are not counted as
        # "without a donor" point at a sibling, and the rest have none.
        self.conn.executemany(
            "INSERT INTO source_files"
            " (rel_path, filename, source_dir, station_id, has_header, schema_donor,"
            "  n_columns, n_body_rows)"
            " VALUES (?, ?, 'd', 's0', 0, ?, 4, 10)",
            [
                (f"f{i}.xlsx", f"f{i}.xlsx", "donor.xlsx")
                for i in range(counts["files"] - counts["headerless_without_donor"])
            ]
            + [("f_nod.xlsx", "f_nod.xlsx", None)] * counts["headerless_without_donor"],
        )
        self.conn.executemany(
            "INSERT INTO readings (station_id, ts_utc, ts_local, tz) VALUES ('s0', ?, 'x', 'UTC')",
            [(f"2020-01-01T00:{i:02d}:00Z",) for i in range(counts["readings"])],
        )
        self.conn.executemany(
            "INSERT INTO rejects (reason) VALUES (?)",
            [("duplicate_ts",)] * counts["duplicate_ts"]
            + [("repeated header row",)] * counts["malformed_rejects"],
        )
        self.conn.executemany("INSERT INTO notes (note) VALUES (?)", [("n",)] * counts["notes"])
        self.conn.executemany(
            "INSERT INTO regimes"
            " (station_id, column, valid_from, status, detected_by, confidence)"
            " VALUES ('s0', 'battery_v', ?, 'unconfirmed', 'manual', 'high')",
            [(f"2020-01-0{i + 1}T00:00:00Z",) for i in range(counts["unconfirmed_regimes"])],
        )
        self.conn.executemany(
            "INSERT INTO readings_hourly (station_id, ts_utc, n_samples) VALUES ('s0', ?, 1)",
            [(f"2020-01-{d:02d}T00:00:00Z",) for d in range(1, counts["hourly_buckets"] + 1)],
        )
        self.conn.executemany(
            "INSERT INTO readings_daily (station_id, day, ts_utc_day, n_samples)"
            " VALUES ('s0', ?, ?, 1)",
            [
                (f"2020-01-{d:02d}", f"2020-01-{d:02d}T00:00:00Z")
                for d in range(1, counts["daily_buckets"] + 1)
            ],
        )
        return counts

    def test_measure_reads_every_field(self):
        counts = self._populate()
        measured = measure(self.conn)
        self.assertEqual(measured, counts)

    def test_no_drift_passes(self):
        self._populate()
        result = check(self.conn, self._write_baseline())
        self.assertTrue(result.ok, result.drift)
        self.assertEqual(result.drift, {})

    def _write_baseline(self, **overrides) -> Path:
        """Write a baseline with the given values *without* touching the database.

        The database holds whatever ``setUp``/``_populate`` last inserted, so
        passing overrides here is what creates a drift for ``check`` to find.
        """
        counts = dict(self._default_counts())
        counts.update(overrides)
        self.baseline.write_text(json.dumps({"counts": counts}), encoding="utf-8")
        return self.baseline

    @staticmethod
    def _default_counts() -> dict[str, int]:
        return {
            "readings": 50,
            "files": 6,
            "stations": 2,
            "duplicate_ts": 3,
            "malformed_rejects": 1,
            "notes": 2,
            "unconfirmed_regimes": 2,
            "headerless_without_donor": 0,
            "hourly_buckets": 4,
            "daily_buckets": 2,
        }

    def test_lost_readings_fails(self):
        self._write_baseline(readings=45)
        result = check(self.conn, self.baseline)
        self.assertFalse(result.ok)
        self.assertEqual(result.drift["readings"], (45, 50))

    def test_duplicated_readings_fails(self):
        # The INSERT OR IGNORE dedupe silently stopping is the other mode.
        self._write_baseline(readings=55, duplicate_ts=0)
        result = check(self.conn, self.baseline)
        self.assertFalse(result.ok)
        self.assertIn("readings", result.drift)
        self.assertIn("duplicate_ts", result.drift)

    def test_lost_schema_donor_fails(self):
        # A headerless file with no donor means its measurements were discarded
        # even though every timestamp ingested.  This field catches exactly that.
        self._write_baseline(headerless_without_donor=1)
        result = check(self.conn, self.baseline)
        self.assertFalse(result.ok)
        self.assertIn("headerless_without_donor", result.drift)

    def test_drift_table_names_the_field_and_delta(self):
        self._write_baseline(readings=45)
        text = render(check(self.conn, self.baseline))
        self.assertIn("readings", text)
        self.assertIn("+5", text)
        # every field is listed so a passing run is auditable too
        for name in BASELINE_FIELDS:
            self.assertIn(name, text)

    def test_write_records_a_reason_and_provenance(self):
        self._populate()
        payload = write(self.conn, self.baseline, reason="added a station")
        self.assertEqual(payload["counts"]["readings"], 50)
        self.assertEqual(payload["recorded"]["reason"], "added a station")
        # tool_version and the ingest notes travel with it, so a later diff
        # explains itself without re-running anything.
        self.assertEqual(payload["recorded"]["tool_version"], __version__)


class TestCommittedBaseline(unittest.TestCase):
    """The baseline actually committed in data/baseline.json."""

    PATH = Path(__file__).resolve().parent.parent / "data" / "baseline.json"

    def test_baseline_file_exists_and_is_well_formed(self):
        self.assertTrue(self.PATH.exists(), "data/baseline.json is missing")
        payload = json.loads(self.PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(payload["counts"]), set(BASELINE_FIELDS))
        self.assertTrue(payload["recorded"].get("reason"), "baseline must record a reason")

    def test_baseline_matches_documented_numbers(self):
        # These are the figures quoted in README.md and CHANGELOG.md.  If a
        # rebuild moves them, the documentation is wrong and this fails.
        counts = json.loads(self.PATH.read_text(encoding="utf-8"))["counts"]
        self.assertEqual(counts["readings"], 734908)
        self.assertEqual(counts["files"], 364)
        self.assertEqual(counts["stations"], 8)
        self.assertEqual(counts["duplicate_ts"], 4399)
        self.assertEqual(counts["notes"], 10)
        self.assertEqual(counts["unconfirmed_regimes"], 20)
        self.assertEqual(counts["headerless_without_donor"], 0)

    def test_malformed_rejects_cover_the_excluded_rows(self):
        # 6 repeated header rows + 100 excluded pre-reinstall rows in
        # aisvn/IFTTT_aisvn (25).xlsx.  The exclusions have to stay visible.
        counts = json.loads(self.PATH.read_text(encoding="utf-8"))["counts"]
        self.assertEqual(counts["malformed_rejects"], 106)

    def test_invariants_that_must_never_relax(self):
        counts = json.loads(self.PATH.read_text(encoding="utf-8"))["counts"]
        self.assertEqual(counts["headerless_without_donor"], 0)
        self.assertGreater(counts["readings"], 0)
        self.assertGreater(counts["files"], 0)


if __name__ == "__main__":
    unittest.main()

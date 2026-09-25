"""The Parquet staleness check used by CI.

``data/processed/parquet/`` is committed, and the pipeline overwrites it.  So CI
snapshots the committed layout before building and compares afterwards.  These
tests cover the comparison logic and the path parsing, using real Parquet files
written through pyarrow -- the same code path CI exercises.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "parquet_manifest.py"
_spec = importlib.util.spec_from_file_location("parquet_manifest", _SCRIPT)
pm = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(pm)


def write_partition(root: Path, station: str, year: str, rows: int) -> None:
    directory = root / f"station={station}" / f"year={year}"
    directory.mkdir(parents=True, exist_ok=True)
    table = pa.table({"ts_utc": [f"{year}-01-01T00:{i:02d}:00Z" for i in range(rows)]})
    pq.write_table(table, directory / "part-0.parquet")


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_counts_rows_per_partition(self):
        root = self.tmp / "pq"
        write_partition(root, "phumy2", "2023", 5)
        write_partition(root, "aisvn", "2020", 3)
        write_partition(root, "aisvn", "2021", 2)
        data = pm.manifest(root)
        self.assertEqual(
            data["partitions"],
            {
                "station=aisvn/year=2020": 3,
                "station=aisvn/year=2021": 2,
                "station=phumy2/year=2023": 5,
            },
        )
        self.assertEqual(data["total_rows"], 10)

    def test_missing_root_is_empty_not_an_error(self):
        data = pm.manifest(self.tmp / "nope")
        self.assertEqual(data["total_rows"], 0)
        self.assertEqual(data["partitions"], {})

    def test_partition_values_come_from_the_directory_names(self):
        directory = self.tmp / "station=aisvn" / "year=2020"
        self.assertEqual(pm._value(directory, "station", "?"), "aisvn")
        self.assertEqual(pm._value(directory, "year", "?"), "2020")
        self.assertEqual(pm._value(directory, "missing", "?"), "?")


class TestCompare(unittest.TestCase):
    @staticmethod
    def _layout(partitions, total=None):
        return {
            "partitions": partitions,
            "total_rows": sum(partitions.values()) if total is None else total,
        }

    def test_identical_layouts_produce_no_problems(self):
        layout = self._layout({"station=a/year=2020": 5, "station=b/year=2021": 3})
        self.assertEqual(pm.compare(layout, layout), [])

    def test_changed_row_count_is_reported(self):
        before = self._layout({"station=a/year=2020": 5})
        after = self._layout({"station=a/year=2020": 4})
        problems = pm.compare(before, after)
        self.assertEqual(len(problems), 1)
        self.assertIn("station=a/year=2020", problems[0])
        self.assertIn("committed 5", problems[0])
        self.assertIn("rebuilt 4", problems[0])

    def test_added_and_removed_partitions_are_reported(self):
        before = self._layout({"station=a/year=2020": 5, "station=b/year=2021": 3})
        after = self._layout({"station=a/year=2020": 5, "station=c/year=2022": 1})
        problems = pm.compare(before, after)
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("station=b/year=2021" in p for p in problems))
        self.assertTrue(any("station=c/year=2022" in p for p in problems))

    def test_total_mismatch_alone_is_reported(self):
        # manifest() always derives total from the partition table, so this
        # guards only the fallback branch.
        before = {"partitions": {}, "total_rows": 10}
        after = {"partitions": {}, "total_rows": 11}
        self.assertEqual(len(pm.compare(before, after)), 1)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "pq"
        write_partition(self.root, "aisvn", "2020", 4)
        self.recording = self.tmp / "before.json"

    def test_write_then_check_round_trips(self):
        self.assertEqual(pm.main(["write", str(self.root), "-o", str(self.recording)]), 0)
        self.assertEqual(pm.main(["check", str(self.root), "--against", str(self.recording)]), 0)

    def test_check_fails_after_the_layout_changes(self):
        pm.main(["write", str(self.root), "-o", str(self.recording)])
        # Simulate a rebuild that produced a different partition.
        write_partition(self.root, "aisvn", "2020", 7)
        self.assertEqual(pm.main(["check", str(self.root), "--against", str(self.recording)]), 1)

    def test_first_build_with_no_committed_parquet_passes(self):
        # A repository that has never committed parquet must not fail CI.
        self.recording.write_text(json.dumps({"partitions": {}, "total_rows": 0}))
        self.assertEqual(pm.main(["check", str(self.root), "--against", str(self.recording)]), 0)


if __name__ == "__main__":
    unittest.main()

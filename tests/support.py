"""Build real XLSX fixtures, so the tests exercise the openpyxl path production uses.

The 0.8 suite wrote 39 temporary archives and never cleaned one of them up, and
its one test that read the real ``data/raw`` took about fifty seconds because
``detect_block`` parses a sheet and that parse is 86% of a build. Neither is
acceptable: a suite you stop running is a suite that stops catching things.

So: a fixture is a real XLSX, because that is the only way to catch a reader
that has stopped understanding the file format.  The archive is *not* read by the
unit tests -- ``etl.audit`` reads it, in the build, where fifty seconds is a
minute of CI rather than a stalled test run.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook


def write_xlsx(path: Path, header: list[str] | None, rows: list[list[object]]) -> Path:
    """Write a sheet, with column A as text whether it holds a header or a date.

    openpyxl turns a string that looks like a date into a datetime cell, and the
    reader then sees a cell object rather than the text the archive actually
    contains. Forcing the string format keeps the fixture byte-comparable with
    the real files, which is the whole point of using openpyxl here.
    """
    workbook = Workbook()
    sheet = workbook.active
    if header is not None:
        sheet.append(header)
        for cell in sheet[1]:
            cell.number_format = "@"
    for row in rows:
        sheet.append(row)
    width = len(header) if header else (len(rows[0]) if rows else 1)
    for column in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:width]:
        sheet.column_dimensions[column].width = 22
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    workbook.close()
    return path


class TempArchiveCase(unittest.TestCase):
    """A test case with a throwaway raw archive and output directory.

    Both are removed in ``tearDown``, which 0.8's 39 ingest tests never did --
    they left a directory of XLSX files behind in ``%TEMP%`` for every test that
    ever ran, in this repository and in every clone of it.
    """

    def setUp(self) -> None:
        from etl.config import Settings

        self.tmp = Path(tempfile.mkdtemp(prefix="solardata-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.raw = self.tmp / "raw"
        self.out = self.tmp / "processed"
        self.export = self.tmp / "public"
        self.settings = Settings(raw_dir=self.raw, out_dir=self.out, export_dir=self.export)

    def write(self, folder: str, rows: list[list[object]], header: list[str] | None = None) -> Path:
        """Write one file into the raw archive and return its path."""
        return write_xlsx(self.raw / folder / "IFTTT_test.xlsx", header, rows)

    def build(
        self, stations: dict[str, list[list[object]]], headers: dict[str, list[str]] | None = None
    ):
        """Write a raw archive and ingest it, returning the summary.

        ``stations`` maps a *source directory* name to its rows, and ``headers``
        the same keys to their header row.  Omit a header to write a headerless
        file, which is 305 of the 364 files in the real archive.
        """
        from etl import build_db
        from etl.readers import xlsx

        headers = headers or {}
        for folder, rows in stations.items():
            if not rows:
                continue
            self.write(folder, rows, headers.get(folder))
        xlsx.clear_read_cache()
        self.addCleanup(xlsx.clear_read_cache)
        return build_db.ingest(self.settings, verbose=False)

    def connect(self, *, read_only: bool = False):
        """A connection to the built database.

        Read-write by default, because the aggregate and export stages write and
        a test that opens read-only to save a line gets `attempt to write a
        readonly database` from whichever stage it forgot.
        """
        from etl.db import connect

        return connect(self.settings.db_path, read_only=read_only)

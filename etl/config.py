"""Paths, sentinels, flag names, and the human decisions about specific files.

Everything a person decided about the archive lives in this module or in
``etl.catalog``, and nothing else decides anything.  If a number in the database
is not the number the sheet wrote, one of these two files says why.

What moved to ``etl.catalog`` in 0.9
------------------------------------
Per-channel plausibility bands, per-station unit overrides and confirmed scale
factors used to live in three places -- ``normalize/metrics.py``,
``config.CHANNEL_UNITS`` and ``build_regimes.py`` -- and were read in seven.
A band was a property of a *column name*, which is why 232 mA was tested against
a +/-50 A band. They are now one declaration per (station, channel) in
``etl.catalog``, applied once, at ingest.

What is left here
-----------------
Decisions that are not about a measurement: values the collector used as
placeholders, whole files that are not measurements, and windows over which a
reading should not be believed or should not be stored at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

__all__ = [
    "BAD_WINDOWS",
    "EXCEL_LOCK_PREFIX",
    "FILE_EXCLUSIONS",
    "FLAG_BAD_WINDOW",
    "FLAG_FREE_TEXT",
    "FLAG_MISALIGNED",
    "FLAG_NO_SIGNAL",
    "FLAG_OUT_OF_RANGE",
    "FLAG_SENTINEL",
    "NULL_WINDOWS",
    "REASONS",
    "REPO_ROOT",
    "ROW_EXCLUSIONS",
    "SENTINELS",
    "Settings",
    "is_excel_lock_file",
    "settings_from_env",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Files that are not in the archive, whatever they are called.
#
# Excel writes a 165-byte owner file next to any workbook it has open, named
# ``~$`` plus the workbook's own name -- so ``Voltage_phumy.xlsx`` sitting open in
# Excel puts ``~$Voltage_phumy.xlsx`` in the same folder. It is not a sheet, it
# holds no rows, and openpyxl cannot open it at all: the build died on
#
#     PermissionError: data/raw/Voltage_phumy/~$Voltage_phumy.xlsx
#
# which is a bad failure in three ways. It names a file that looks like archive
# content, it says "permission denied" about a directory nobody has protected, and
# it costs the two minutes of ingest to find out that somebody has a spreadsheet
# open.
#
# Matched on the ``~$`` prefix rather than added one name at a time, because the
# name is derived from the workbook's, so an allowlist of filenames would go stale
# the next time a sheet is opened. The prefix is Microsoft's, not ours, and it is
# not a legal first character for a name the collector would have used.
#
# These are skipped, not read, so they are not in `source_files` and cannot reach
# a reading. The build reports how many it skipped, so a run that skipped one is
# visible rather than silent.
# ---------------------------------------------------------------------------
EXCEL_LOCK_PREFIX = "~$"


def is_excel_lock_file(name: str) -> bool:
    """True for an Excel owner file, which is never a sheet in the archive."""
    return name.startswith(EXCEL_LOCK_PREFIX)


# ---------------------------------------------------------------------------
# Placeholder values the collector wrote instead of a measurement.
#
# Keyed by float, because a cell holding 342.0 is rendered as the text "342" on
# the way out of openpyxl and has to match the float.
#
# Every one of these becomes NULL with quality_flags = 'sentinel' and a rejects
# row. None of them becomes 0: a 0 V panel reading at midnight is a
# measurement, and -992 is a hole in the sheet.
# ---------------------------------------------------------------------------
SENTINELS: dict[float, str] = {
    -992.0: "ifttt_missing_value",
    -1.0: "ifttt_missing_value",
    342.0: "adc_rail",
    342.1: "adc_rail",
}

# ---------------------------------------------------------------------------
# Quality flags. A reading row's quality_flags is the comma-joined, order-
# preserving union of these for that row, and '' means no flag.
#
# Two families are parameterised by channel, ``no_signal:<channel>`` and
# ``bad_window:<channel>``, which is why a flat lookup cannot explain every
# flagged row: the report publishes the two window tables from this module
# alongside the counts.
# ---------------------------------------------------------------------------
FLAG_SENTINEL = "sentinel"
FLAG_OUT_OF_RANGE = "out_of_range"
FLAG_NO_SIGNAL = "no_signal"
FLAG_BAD_WINDOW = "bad_window"
FLAG_MISALIGNED = "schema_misaligned"
FLAG_FREE_TEXT = "free_text"

# The complete vocabulary, for the report and for the test that asserts no other
# flag is ever written.
ALL_FLAGS: tuple[str, ...] = (
    FLAG_SENTINEL,
    FLAG_OUT_OF_RANGE,
    FLAG_NO_SIGNAL,
    FLAG_BAD_WINDOW,
    FLAG_MISALIGNED,
    FLAG_FREE_TEXT,
)

#: What each flag means, in prose, published to the site so a flagged value can
#: be explained from the number itself rather than from a reader's memory.
FLAG_NAMES: dict[str, str] = {
    FLAG_SENTINEL: (
        "The sheet wrote a placeholder the collector uses for a missing value. "
        "Stored as NULL; the raw cell is in rejects."
    ),
    FLAG_OUT_OF_RANGE: (
        "Outside this station's plausibility band for this channel, in the unit "
        "the value is stored in. The value is kept, always."
    ),
    FLAG_NO_SIGNAL: (
        "A configured window in which the collector says the input was "
        "disconnected. Stored as NULL; the raw cell is in rejects."
    ),
    FLAG_BAD_WINDOW: (
        "A configured window a human has said not to believe. Kept and flagged, "
        "because the sample is real even if the level is not."
    ),
    FLAG_MISALIGNED: ("The row's width did not match the layout the catalog declares for it."),
    FLAG_FREE_TEXT: ("The cell held prose rather than a number. Recovered into notes."),
}

# ---------------------------------------------------------------------------
# rejects.reason is a stable category, never a sentence.
#
# This is not a style preference. Storing the collector's ~300-character note as
# the reason on each of the 220,069 cells a window nulled put one paragraph into
# 220,069 rows, cost 80.6 MiB, and made rejects larger than readings; it also
# turned 4,403 duplicate timestamps into 4,361 singleton groups in the report,
# because the timestamp was interpolated into the text. The category goes in the
# row and the prose goes here, once, and the report republishes it.
# ---------------------------------------------------------------------------
REASON_SENTINEL = "sentinel"
REASON_STATION_SETUP = "station_setup"
REASON_PRE_REINSTALL = "pre_reinstall"
REASON_DUPLICATE_TS = "duplicate_ts"
REASON_REPEATED_HEADER = "repeated_header"
REASON_UNPARSEABLE_TS = "unparseable_ts"
REASON_FREE_TEXT = "free_text"
REASON_NULL_WINDOW = "null_window"

REASONS: tuple[str, ...] = (
    REASON_SENTINEL,
    REASON_STATION_SETUP,
    REASON_PRE_REINSTALL,
    REASON_DUPLICATE_TS,
    REASON_REPEATED_HEADER,
    REASON_UNPARSEABLE_TS,
    REASON_FREE_TEXT,
    REASON_NULL_WINDOW,
)

# ---------------------------------------------------------------------------
# Whole files that are not measurements.
#
# The file still gets a source_files row, and every data row in it gets its own
# rejects row with reason 'station_setup', so the decision is inspectable one
# cell at a time rather than taken on trust.
#
# The first entry below is dead: 0.8.0's raw repair deleted
# ``test/IFTTT_test.xlsx`` from the archive, and it is kept here rather than
# removed so that the repair is visible in the file that records the decision.
# An exclusion that matches nothing rejects nothing.
# ---------------------------------------------------------------------------
FILE_EXCLUSIONS: tuple[tuple[str, str], ...] = (
    (
        "test/IFTTT_test.xlsx",
        "collector: the test station's 11-column solar layout is system setup, "
        "not measurement. 0.8.0's raw repair deleted this file from the archive, "
        "so the entry no longer matches anything; it is kept so the repair is "
        "visible here rather than silently forgotten",
    ),
    (
        "test/IFTTT_test (1).xlsx",
        "collector: the 4,120 readings this file uniquely contributes are dated "
        "2020-07-01 to 07-08, after the probe had already begun and after "
        "IFTTT_test (2).xlsx had started covering the same stretch, so they are "
        "not measurements. 0.8 described this file as an 11-column solar layout; "
        "the raw repair removed that stretch, and what is left is 4,121 rows of "
        "the same nix/temp/wifi probe the neighbouring files carry. The "
        "exclusion is kept on the collector's word and the overlap is still "
        "there; the description is corrected",
    ),
)

# ---------------------------------------------------------------------------
# A row floor: rows before this sheet row in this file are not measurements.
# Half-open on the sheet row number, which is 1-based as openpyxl reports it.
# ---------------------------------------------------------------------------
ROW_EXCLUSIONS: tuple[tuple[str, int, str], ...] = (
    (
        "aisvn/IFTTT_aisvn (25).xlsx",
        102,
        "pre-reinstall window; collector confirmed rows from here on are usable",
    ),
)

# ---------------------------------------------------------------------------
# Windows over which a stored number is a false claim rather than a measurement.
#
# The value is NULLed and a rejects row is written with reason 'null_window'.
# The reason carries the channel, never this prose.
# ---------------------------------------------------------------------------
NULL_WINDOWS: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
    (
        "phumy2",
        "2022-10-01T00:00:00Z",
        "2024-01-01T00:00:00Z",
        ("solar2_v",),
        "collector: the collector input was disconnected and the sheet logged a "
        "flat 0.0 V for the whole window. Every one of the 220,069 readings is "
        "that placeholder, so there is no measurement to store. The window is "
        "half-open: the channel reads normally again from 2024-01-01.",
    ),
    (
        "aisvn",
        "2020-06-15T06:10:00Z",
        "2020-06-17T04:14:00Z",
        ("temp_c",),
        "collector: a pre-recompile stretch where the sheet logged 200 as a "
        "stand-in for 'no temperature'. The window ends at 04:14 UTC precisely "
        "so the 114 genuine tenths that follow survive. Nothing is nulled today: "
        "the collector resolved the placeholder at source, and the window is kept "
        "because the archive still carries the boundary.",
    ),
)

# ---------------------------------------------------------------------------
# Windows over which a reading is stored and flagged rather than nulled.
#
# A human has said the value is not to be believed, but it is still what the
# sheet wrote, so it stays and the row says so. Half-open on valid_from <=
# ts_utc < valid_to.
# ---------------------------------------------------------------------------
BAD_WINDOWS: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
    (
        "aisvn",
        "2020-10-23T00:00:00Z",
        "2020-10-30T00:00:00Z",
        ("solar_v", "battery_v", "temp_c"),
        "the collector was being reconfigured: the station kept sampling while "
        "the applet and its wiring were being changed, so the levels in this "
        "window describe a half-built logger. Kept and flagged, because the "
        "samples are real; the good window starts at valid_to.",
    ),
)


@dataclass(frozen=True)
class Settings:
    """Resolved paths and switches for one pipeline run."""

    raw_dir: Path = REPO_ROOT / "data" / "raw"
    out_dir: Path = REPO_ROOT / "data" / "processed"
    export_dir: Path = REPO_ROOT / "public" / "data"
    granularity: str = "both"
    only: tuple[str, ...] = ()
    all_stations: bool = False
    quiet: bool = False

    @property
    def db_path(self) -> Path:
        return self.out_dir / "solardata.db"

    @property
    def parquet_dir(self) -> Path:
        return self.out_dir / "parquet"

    @property
    def report_json(self) -> Path:
        return self.out_dir / "quality_report.json"

    @property
    def report_md(self) -> Path:
        return self.out_dir / "quality_report.md"

    @property
    def baseline_path(self) -> Path:
        return REPO_ROOT / "data" / "baseline.json"

    def ensure_dirs(self) -> None:
        for path in (self.out_dir, self.parquet_dir, self.export_dir):
            path.mkdir(parents=True, exist_ok=True)

    def raw_dirs(self) -> list[Path]:
        if not self.raw_dir.is_dir():
            return []
        return sorted(
            (p for p in self.raw_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name.lower(),
        )

    def with_out_dir(self, out_dir: Path) -> Settings:
        return replace(self, out_dir=Path(out_dir))


def settings_from_env() -> Settings:
    return Settings(
        raw_dir=Path(os.environ.get("SOLARDATA_RAW_DIR", REPO_ROOT / "data" / "raw")),
        out_dir=Path(os.environ.get("SOLARDATA_OUT_DIR", REPO_ROOT / "data" / "processed")),
    )

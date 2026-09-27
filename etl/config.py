"""Filesystem layout and tunables for the pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Raw values that mean "sensor not connected / input floating / rail" rather
# than a genuine measurement. They become NULL, never 0 -- averaging them in
# drags every aggregate towards zero.
#
# -992 and -1 are IFTTT's own missing-value markers, in `test` and
# `Maker_Webhooks_Events`. 342.1 was found in `aisvn.temp_c`: 14,107 readings at
# exactly 342.1 and 7 at 342.0. That is the signature of a saturating float32
# conversion, not a temperature -- no sensor in Ho Chi City reports 342 degC, and
# a value repeating identically 14,000 times is the hardware saying "no".
#
# Keyed by float, not string, because the XLSX reader normalises integral floats
# to their integer spelling: a cell holding 342.0 arrives as the text "342", so
# a string-keyed table silently misses the `.0` variants. Comparing the parsed
# number removes the whole class of spelling variants.
SENTINELS: dict[float, str] = {
    -992.0: "ifttt_missing_value",
    -1.0: "ifttt_missing_value",
    342.0: "adc_rail",
    342.1: "adc_rail",
}

# Values that repeat exactly for long runs and therefore carry no information
# (rail/clip artefacts, e.g. 3532 on the LiPo channel).  These are *retained*
# but flagged, because we cannot prove they are invalid.
CLIP_CANDIDATES: tuple[float, ...] = (3532.0,)

# Flag a reading as clipped when a value repeats for at least this many
# consecutive rows in the same column of the same file.
CLIP_RUN_LENGTH = 200

# Quality bits written to readings.quality_flags (comma separated).
FLAG_SENTINEL = "sentinel"
FLAG_CLIP = "clip"
FLAG_OUT_OF_RANGE = "out_of_range"
FLAG_DUPLICATE_TS = "duplicate_ts"
FLAG_NON_MONOTONIC = "non_monotonic"
FLAG_FREE_TEXT = "free_text"
FLAG_MISALIGNED = "schema_misaligned"

#: ``rejects.reason`` categories.  Deliberately short and stable: the report
#: groups by this column, so a sentence here turns a count into a singleton.
#: The reasoning behind a window belongs in ``NULL_WINDOWS``/``BAD_WINDOWS``
#: below, which is version-controlled prose stored exactly once.
REASON_SETUP = "station_setup"
REASON_NO_SIGNAL = "null_window"
REASON_ROW_FLOOR = "pre_reinstall"

#: Whole source files excluded from ``readings``, as (rel_path, why).
#:
#: Wider than :data:`ROW_EXCLUSIONS`, which drops rows *before* a sheet row in a
#: file that is otherwise ingested. This drops the file: every data row in it is
#: counted into ``rejects`` with :data:`REASON_SETUP` and its sheet row recorded,
#: so the loss is individually inspectable rather than a number in a report.
#:
#: Both entries are the ``test`` station's 11-column *solar* layout. The
#: collector's account of the station is that the solar stretch was system setup
#: rather than measurement, and that the measurements are the 4-column probe
#: (``time, nix, temp, wifi``) from 2020-07-05 onwards.
#:
#: The exclusion is by file rather than by date because the archive does not
#: respect the date. ``IFTTT_test (1).xlsx`` *starts* on 2020-06-14 but every
#: row it uniquely contributes is dated 2020-07-01 18:18 to 2020-07-08 12:12 --
#: its June rows duplicate ``IFTTT_test.xlsx`` and were absorbed by the primary
#: key. A timestamp cut-off at 2020-07-01 would therefore have kept 4,120 solar
#: readings from July, which the collector does not consider measurements.
#:
#: What remains is 33,377 readings from the 4-column probe files, spanning
#: 2020-07-05 to 2020-08-21, with no solar channel at all. Excluding the two
#: files removes 3,994 readings outright and a further 2,150 rows that were
#: already duplicates of rows held in the other file -- which is why
#: `duplicate_ts` falls by 2,150 and the recorded exclusion is 6,144 rows
#: rather than 3,994. The two numbers are both correct and they answer different
#: questions: what left the readings, and what left the archive.
FILE_EXCLUSIONS: tuple[tuple[str, str], ...] = (
    (
        "test/IFTTT_test.xlsx",
        "collector: the test station's 11-column solar layout is system setup, "
        "not measurement. The station's readings are the 4-column probe that "
        "follows it",
    ),
    (
        "test/IFTTT_test (1).xlsx",
        "collector: same 11-column solar layout. This file starts 2020-06-14 but "
        "its June rows duplicate IFTTT_test.xlsx, so everything it uniquely "
        "contributes is 4,120 readings dated 2020-07-01 to 07-08 -- after the "
        "probe had already begun, and still not measurements",
    ),
)

# ---------------------------------------------------------------------------
# Row-level exclusions, decided by the person who collected the data.
#
# Each entry is (rel_path_suffix, first_usable_sheet_row, why).  Rows before the
# boundary are not ingested; they are counted in `rejects` so the loss stays
# visible.
#
# `rejects.reason` gets the *category* from `REASON_*`, never `why`.  Rule 2 in
# `AGENTS.md` asks for that and the archive is where ignoring it shows: the
# `NULL_WINDOWS` prose was once stored on 220,074 rows, costing 80.6 MiB and
# turning a grouped count into a singleton.  `why` is read once per entry by
# `report.collect`, which publishes it to `quality.json` next to the count.
# ---------------------------------------------------------------------------
ROW_EXCLUSIONS: tuple[tuple[str, int, str], ...] = (
    (
        "aisvn/IFTTT_aisvn (25).xlsx",
        102,
        # The applet was reinstalled during 2020-10-25..30. The margin notes in
        # this file read "this all is just garbage", "Pin 4 is temperature -
        # calibrated ...", "Installed in the dark, let's start again!". For the
        # first 100 rows battery reads 29.2 V and temp 16 degC, which are not
        # measurements of anything. Measured transition: battery steps from
        # -0.99 to 12.84 V at sheet row 112, but rows 102-111 are the
        # powered-down state (solar 0, load 0), so the earlier boundary is used
        # and those 10 extra rows are harmless. Confirmed by the collector.
        "pre-reinstall window; collector confirmed rows from here on are usable",
    ),
)

#: Collector-confirmed unit corrections applied **at ingest**, before the
#: plausibility check. (station_id, column, valid_from_utc, valid_to_utc|None,
#: multiply_by, why). ``valid_to=None`` means "to the end of the record".
#:
#: This is not :data:`NULL_WINDOWS` and it is not a regime. Those act on a value
#: that is already stored; this acts on the number the sheet wrote, because the
#: collector has said what unit the channel was logging in and a plausibility
#: band is only meaningful in the unit the value is stored in.
#:
#: `aisvn.temp_c` is the case that forces it. The channel wrote tenths of a
#: degree before the 2020-06-17 15:20 local recompile and plain degrees after,
#: so a genuine 33.5 degC reading arrives as ``335`` and the 5-45 degC band
#: flags every real measurement in the archive. Correcting it in the aggregate
#: would mean the flags were already wrong, and rule 2 says a flag a reader
#: cannot trust is worse than no flag at all.
#:
#: The result is that `readings.temp_c` is tenths of a degree throughout and the
#: band for it is 50-900. The 114 readings the sheet wrote as tenths (335 =
#: 33.5 degC) are already correct and are left alone by the correction, which is
#: the check that this is the right reading of the archive: a rule that scaled
#: them too would put them at 3,350 degC.
UNIT_FIXES: tuple[tuple[str, str, str, str | None, float, str], ...] = (
    (
        "aisvn",
        "temp_c",
        "2020-06-17T08:20:00Z",
        None,
        10.0,
        "collector: the applet was recompiled at 15:20 local and temp_c switched "
        "from tenths of a degree to plain degrees. The 114 readings before it, "
        "11:14 to 15:18 local, are already tenths (335 = 33.5 degC) and must not be "
        "scaled again",
    ),
    (
        "phumy2",
        "temp_c",
        "2020-06-15T00:00:00Z",
        None,
        10.0,
        "collector: phumy2 logs tenths of a degree for the whole record. The raw "
        "values are 155 to 806, which is 15.5 to 80.6 degC",
    ),
    (
        "test",
        "temp_c",
        "2020-07-01T00:00:00Z",
        None,
        100.0,
        "collector: the probe's temperature is in hundredths of a degree; the raw "
        "values are 2472 to 3009, which is 24.72 to 30.09 degC",
    ),
)

#: Per-station unit and band, where a station's declared unit differs from the
#: column's default in ``etl.normalize.metrics``. (station_id, column, unit,
#: lo, hi, why).
#:
#: The band table is keyed by column, so it describes one unit for every station
#: that logs the column. `temp_c` is stored in tenths of a degree on `aisvn` and
#: `phumy2` but in **hundredths** on `test`, where the collector asked for
#: hundredths because the probe's readings are that precise. A single band in
#: tenths therefore flags all 33,377 of `test`'s temperatures as implausible,
#: which is the same defect as a column whose name disagrees with its contents:
#: a flag that is wrong every time is worse than no flag.
#:
#: This is the small, early version of the per-column `column_semantics` the
#: `solardata_raw.db` layout is heading towards, where the unit belongs to a
#: (station, column) pair rather than to a column name.
CHANNEL_UNITS: tuple[tuple[str, str, str, float, float, str], ...] = (
    (
        "test",
        "temp_c",
        "0.01 degC",
        2149.0,
        3131.0,
        "collector: the probe logs hundredths of a degree and asked for that "
        "resolution specifically, so the values are kept as written rather than "
        "rounded to tenths. The band is 21.49 to 31.31 degC, the observed range",
    ),
)

#: Windows in which a channel reports a *constant* value that is affirmatively
#: wrong, so it is nulled rather than merely flagged.
#:
#: Distinct from BAD_WINDOWS, which flags but keeps. Here the stored number
#: makes a false claim: 0.0 V from a photovoltaic panel says "the panel produced
#: nothing", when the truth is "the wire was disconnected". Charting a year of
#: that as a flat line at zero would be a wrong answer, not an ugly one.
#:
#: (station_id, valid_from_utc, valid_to_utc, columns, reason)
#:
#: The 2023 `phumy2.solar2_v` window is identified by its hour-of-day profile.
#: A working panel reads 0.0 at night and non-zero around midday: in 2020 the
#: channel is 100% zero from 18:00 to 05:00 and 2% at noon. Across 2023 it is
#: 100% zero at *every* hour including 12:00, which is a disconnected input
#: rather than a dark panel. It recovers in 2024-01.
NULL_WINDOWS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "phumy2",
        "2022-10-01T00:00:00Z",
        "2024-01-01T00:00:00Z",
        "solar2_v",
        "solar2_v reads 0.0 at every hour of the day for the whole of 2023, "
        "including noon; a working panel is 100% zero at night and ~2% at "
        "midday (see 2020). The input was disconnected, so 0.0 is a false "
        "reading rather than a measurement. The channel recovers in 2024-01. "
        "Collector's note: the reading only appears when the sun is on the "
        "panel, and drops after a bridge and load were fitted.",
    ),
    (
        "aisvn",
        "2020-06-15T06:10:00Z",
        "2020-06-17T04:14:00Z",
        "temp_c",
        "collector: 200 is a placeholder for 'no temperature recorded', not a "
        "temperature. It fills every one of the first 1,359 readings, from "
        "2020-06-15 13:10 local to 2020-06-17 11:12 local, and the channel only "
        "reports real values from 11:14 local onwards. A stored 200 degC is a "
        "false claim, exactly as phumy2's 0.0 V panel is: charting it would "
        "draw a line at 200 degC through a Ho Chi City summer. Nulled. The window "
        "ends at 11:14 local, the first genuine reading, because the boundary "
        "has to exclude as well as include and the 114 readings from 11:14 "
        "onwards are real tenths of a degree",
    ),
)

#: Windows in which specific channels are known bad, decided by the collector.
#: (station_id, valid_from_utc, valid_to_utc, comma-separated columns, why)
#: Half-open: the good window starts at valid_to.
BAD_WINDOWS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "aisvn",
        "2020-10-23T00:00:00Z",
        "2020-10-30T00:00:00Z",
        "solar_v,battery_v,temp_c",
        "solar and battery stop being plausible on 2020-10-23 (collector-confirmed, as "
        "is the temperature on the 23rd); the system was reinstalled on 2020-10-30 "
        "('installed in the dark'), after which all three channels are normal. "
        "Measured: temperature median 161 tenths (16.1 degC) in the window against "
        "323 tenths (32.3 degC) from 2020-10-30.",
    ),
)


@dataclass(frozen=True)
class Settings:
    """Resolved paths and behaviour switches for one pipeline run."""

    raw_dir: Path = REPO_ROOT / "data" / "raw"
    out_dir: Path = REPO_ROOT / "data" / "processed"
    db_path: Path = REPO_ROOT / "data" / "processed" / "solardata.db"
    parquet_dir: Path = REPO_ROOT / "data" / "processed" / "parquet"
    #: Served by Vite from ``public/`` and fetched by the browser, so it lives
    #: under ``public/`` rather than in the gitignored ``data/exports``.
    export_dir: Path = REPO_ROOT / "public" / "data"
    report_json: Path = REPO_ROOT / "data" / "processed" / "quality_report.json"
    report_md: Path = REPO_ROOT / "data" / "processed" / "quality_report.md"
    #: Expected output of a build.  Committed, and enforced by CI, so that a
    #: pipeline change which silently alters the data fails loudly.
    baseline_path: Path = REPO_ROOT / "data" / "baseline.json"

    # Chunking
    parquet_rows_per_group: int = 50_000
    #: Which rollups the export stage publishes.  ``both`` is the default: the
    #: site switches resolution at runtime, and the daily rollup is derived from
    #: the hourly one, so the two cannot disagree.  There is no ``raw`` -- the
    #: native 119 s cadence is 734,908 rows, which is a Parquet download and not
    #: a static file a browser can fetch.
    export_granularity: str = "both"  # both | hour | day

    # Deduplication.  "keep_first" wins on (station_id, ts_utc) collisions,
    # which come from overlapping 2000-row chunk boundaries and IFTTT re-sends.
    duplicate_policy: str = "keep_first"

    # Only build these artefacts (comma separated); empty means all.
    only: tuple[str, ...] = field(default_factory=tuple)

    def ensure_dirs(self) -> None:
        for p in (self.out_dir, self.parquet_dir, self.export_dir):
            p.mkdir(parents=True, exist_ok=True)

    def raw_dirs(self) -> list[Path]:
        if not self.raw_dir.is_dir():
            return []
        return sorted(
            (p for p in self.raw_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name.lower(),
        )


def settings_from_env() -> Settings:
    """Allow overriding the raw/output locations without editing code."""
    return Settings(
        raw_dir=Path(os.environ.get("SOLARDATA_RAW_DIR", REPO_ROOT / "data" / "raw")),
        out_dir=Path(os.environ.get("SOLARDATA_OUT_DIR", REPO_ROOT / "data" / "processed")),
    )

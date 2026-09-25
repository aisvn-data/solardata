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

# ---------------------------------------------------------------------------
# Row-level exclusions, decided by the person who collected the data.
#
# Each entry is (rel_path_suffix, first_usable_sheet_row, why).  Rows before the
# boundary are not ingested; they are counted in `rejects` so the loss stays
# visible, and the reason is recorded verbatim rather than paraphrased.
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

#: Windows in which specific channels are known bad, decided by the collector.
#: (station_id, valid_from_utc, valid_to_utc, comma-separated columns, why)
#: Half-open: the good window starts at valid_to.
BAD_WINDOWS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "aisvn",
        "2020-06-15T00:00:00Z",
        "2020-06-17T08:20:00Z",
        "temp_c",
        "commissioning placeholder: the channel reports exactly 200.0 for every one of "
        "the first 1,359 readings (2020-06-15 13:10 local onward), then 342.1 for a "
        "4-hour block, and only becomes real after 2020-06-17 15:20 local -- which is "
        "the same moment the applet changed its column layout. 90% of all "
        "out-of-range temperatures on this station are in this window.",
    ),
    (
        "aisvn",
        "2020-10-23T00:00:00Z",
        "2020-10-30T00:00:00Z",
        "solar_v,battery_v,temp_c",
        "solar and battery stop being plausible on 2020-10-23 (collector-confirmed, as "
        "is the temperature on the 23rd); the system was reinstalled on 2020-10-30 "
        "('installed in the dark'), after which all three channels are normal. "
        "Measured: temperature median 16.1 degC in the window vs 32.3 degC from "
        "2020-10-30.",
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
    export_granularity: str = "hour"  # hour | day | raw

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

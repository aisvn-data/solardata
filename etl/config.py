"""Filesystem layout and tunables for the pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Raw values that mean "sensor not connected / input floating" rather than a
# genuine measurement.  Found in the ``test`` and ``Maker_Webhooks_Events``
# archives.  They must become NULL, never 0 -- averaging them in drags every
# aggregate towards zero.
SENTINEL_NEGATIVE: dict[str, str] = {
    "-992": "ifttt_missing_value",  # 8366 cells in Maker_Webhooks, 4668 in test
    "-1": "ifttt_missing_value",
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


@dataclass(frozen=True)
class Settings:
    """Resolved paths and behaviour switches for one pipeline run."""

    raw_dir: Path = REPO_ROOT / "data" / "raw"
    out_dir: Path = REPO_ROOT / "data" / "processed"
    db_path: Path = REPO_ROOT / "data" / "processed" / "solardata.db"
    parquet_dir: Path = REPO_ROOT / "data" / "processed" / "parquet"
    export_dir: Path = REPO_ROOT / "data" / "exports"
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

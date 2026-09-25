"""Compare a freshly built database against the recorded baseline.

This is the machine-readable half of the rule in ``AGENTS.md``: *a change that
silently alters the reading count is a bug even if every test passes*.  The
tests exercise fixtures; only this check sees the real 735,004-row archive, so
it is what catches a pipeline change that quietly drops a channel, loses a
headerless file, or double-counts a chunk boundary.

Two failure modes it is designed to catch, both of which happen silently:

* **Loss.**  A schema-inheritance regression makes a headerless file map no
  columns.  Every timestamp still ingests, every test still passes, and the
  measurements quietly become NULL.
* **Duplication.**  ``INSERT OR IGNORE`` stops absorbing an overlap, so the
  same instant lands twice and the count rises by thousands.

When a change is *intended* -- a new station, a corrected timezone -- the
baseline is deliberately updated rather than loosened, so the diff shows up in
the pull request as an explicit number.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from etl import __version__

#: What the build is expected to produce.  Order matters only for the diff
#: table.  These are the numbers a reviewer actually cares about: the reading
#: count, the loss/bookkeeping counters, and the derived artefacts.
BASELINE_FIELDS: tuple[str, ...] = (
    "readings",
    "files",
    "stations",
    "duplicate_ts",
    "malformed_rejects",
    "notes",
    "unconfirmed_regimes",
    "headerless_without_donor",
    "hourly_buckets",
    "daily_buckets",
)

#: Queries backing each field.  Each must return exactly one row/column.
_QUERIES: dict[str, str] = {
    "readings": "SELECT COUNT(*) FROM readings",
    "files": "SELECT COUNT(*) FROM source_files",
    "stations": "SELECT COUNT(*) FROM stations",
    "duplicate_ts": "SELECT COUNT(*) FROM rejects WHERE reason = 'duplicate_ts'",
    "malformed_rejects": "SELECT COUNT(*) FROM rejects WHERE reason <> 'duplicate_ts'",
    "notes": "SELECT COUNT(*) FROM notes",
    "unconfirmed_regimes": "SELECT COUNT(*) FROM regimes WHERE status = 'unconfirmed'",
    # Must stay 0: any headerless file with no schema donor has had its
    # measurements discarded, which is the exact bug this module exists for.
    "headerless_without_donor": (
        "SELECT COUNT(*) FROM source_files WHERE has_header = 0 AND schema_donor IS NULL"
    ),
    "hourly_buckets": "SELECT COUNT(*) FROM readings_hourly",
    "daily_buckets": "SELECT COUNT(*) FROM readings_daily",
}


@dataclass
class BaselineResult:
    expected: dict[str, int]
    actual: dict[str, int]

    @property
    def drift(self) -> dict[str, tuple[int, int]]:
        """Fields whose value moved, as ``(expected, actual)``."""
        return {
            name: (self.expected.get(name), self.actual[name])
            for name in BASELINE_FIELDS
            if self.expected.get(name) != self.actual[name]
        }

    @property
    def ok(self) -> bool:
        return not self.drift


def measure(conn: sqlite3.Connection) -> dict[str, int]:
    """Read the current values of every baseline field."""
    return {name: int(conn.execute(sql).fetchone()[0]) for name, sql in _QUERIES.items()}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check(conn: sqlite3.Connection, path: Path) -> BaselineResult:
    """Compare the built database against the recorded baseline."""
    return BaselineResult(load(path)["counts"], measure(conn))


def write(conn: sqlite3.Connection, path: Path, *, reason: str = "") -> dict:
    """Record the current build as the new baseline.

    Called only deliberately, with a reason, so that the next person can see
    why the numbers moved rather than finding a silently rewritten file.
    """
    counts = measure(conn)
    conn_row = conn.execute(
        "SELECT tool_version, started_at, notes FROM ingest_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    payload = {
        "description": (
            "Expected output of `python -m etl ingest`. CI fails on any drift; "
            "see etl/verify.py and AGENTS.md."
        ),
        "counts": counts,
        "recorded": {
            "tool_version": conn_row["tool_version"] if conn_row else __version__,
            "run_started_at": conn_row["started_at"] if conn_row else None,
            "ingest_notes": conn_row["notes"] if conn_row else None,
            "reason": reason,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def render(result: BaselineResult) -> str:
    """Human-readable diff table, used by the CLI and echoed into CI logs."""
    lines = [f"{'field':<28} {'expected':>9} {'actual':>9} {'delta':>6}", "-" * 54]
    for name in BASELINE_FIELDS:
        expected = result.expected.get(name)
        actual = result.actual[name]
        delta = "" if expected == actual else f"{actual - expected:+d}"
        lines.append(f"{name:<28} {expected!s:>9} {actual:>9} {delta:>6}")
    return "\n".join(lines)

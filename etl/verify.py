"""The baseline guard: a build that changed the data has to say why.

The guarantee this file protects is not "the tests pass". It is that the build
still produces the same 731,885 readings from the same 364 files. Those are
different promises: the test suite exercises fixtures, and only a real run over
the archive can notice that a change quietly stopped ingesting a channel, or
started flagging a third of one.

Re-recording the baseline is a deliberate act with a reason attached, and the
reason travels with the file, so the diff explains itself in the pull request
without anyone re-running anything:

    python -m etl verify --update-baseline --reason "corrected the phumy2 tz"

Never by hand. A red build is the point.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import catalog

__all__ = ["BASELINE_FIELDS", "BaselineResult", "check", "load", "measure", "render", "write"]

#: field -> SQL. One row, one column each, in the order the diff table prints.
#:
#: ``readings`` is a per-station sum rather than a single ``COUNT(*)`` because
#: there is no longer one readings table to count. It is still one number and it
#: is still the headline.
BASELINE_FIELDS: dict[str, str] = {
    "readings": "SELECT COALESCE(SUM(n_readings), 0) FROM stations",
    "files": "SELECT COUNT(*) FROM source_files",
    "stations": "SELECT COUNT(*) FROM stations",
    "duplicate_ts": "SELECT COUNT(*) FROM rejects WHERE reason = 'duplicate_ts'",
    "rejects": "SELECT COUNT(*) FROM rejects",
    "sentinels": "SELECT COUNT(*) FROM rejects WHERE reason = 'sentinel'",
    "null_windows": "SELECT COUNT(*) FROM rejects WHERE reason = 'null_window'",
    "notes": "SELECT COUNT(*) FROM notes",
    "excluded_files": "SELECT COUNT(*) FROM source_files WHERE excluded IS NOT NULL",
    "out_of_range": "SELECT COALESCE(SUM(n_out_of_range), 0) FROM channel_stats",
    "channel_stats": "SELECT COUNT(*) FROM channel_stats",
    "hourly_buckets": "SELECT COUNT(*) FROM readings_hourly",
    "daily_buckets": "SELECT COUNT(*) FROM readings_daily",
    "undeclared_layouts": "SELECT COUNT(*) FROM source_files WHERE layout_channels IS NULL",
}


@dataclass
class BaselineResult:
    expected: dict[str, int]
    actual: dict[str, int]

    @property
    def drift(self) -> dict[str, tuple[int, int]]:
        return {
            name: (self.expected.get(name, 0), self.actual.get(name, 0))
            for name in BASELINE_FIELDS
            if self.expected.get(name) != self.actual.get(name)
        }

    @property
    def ok(self) -> bool:
        return not self.drift


def measure(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, sql in BASELINE_FIELDS.items():
        out[name] = int(conn.execute(sql).fetchone()[0] or 0)
    return out


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check(conn: sqlite3.Connection, path: Path) -> BaselineResult:
    payload = load(path)
    return BaselineResult(expected=payload["counts"], actual=measure(conn))


def write(conn: sqlite3.Connection, path: Path, *, reason: str) -> dict:
    from . import __version__

    run = conn.execute(
        "SELECT started_at, notes FROM ingest_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    payload = {
        "description": (
            "The expected output of a full build over data/raw. Enforced by "
            "`python -m etl verify` and by the data workflow. Re-record with "
            "`--update-baseline --reason ...`, never by hand."
        ),
        "counts": measure(conn),
        "recorded": {
            "tool_version": __version__,
            "run_started_at": run["started_at"] if run else None,
            "ingest_notes": run["notes"] if run else None,
            "reason": reason,
            "stations": [s.station_id for s in catalog.STATIONS],
        },
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def render(result: BaselineResult) -> str:
    if result.ok:
        return f"baseline matches: {result.actual['readings']:,} readings"

    lines = [
        "BASELINE DRIFT -- the build no longer produces the recorded data.",
        "",
        f"{'field':<20} {'expected':>14} {'actual':>14} {'delta':>12}",
        "-" * 62,
    ]
    for name, (expected, actual) in result.drift.items():
        lines.append(f"{name:<20} {expected:>14,} {actual:>14,} {actual - expected:>+12,}")
    lines += [
        "",
        "If the change is intended, re-record the baseline deliberately:",
        '  python -m etl verify --update-baseline --reason "why the numbers moved"',
        "",
        "Do not loosen data/baseline.json by hand. The baseline is the record of",
        "what the data is, and a failing build is the point.",
    ]
    return "\n".join(lines)

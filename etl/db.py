"""SQLite connection handling and schema bootstrap."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        # Bulk inserts dominate; a big cache and relaxed sync keep the build
        # inside a few minutes for ~740k rows.
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA cache_size = -64000")
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def start_run(conn: sqlite3.Connection, raw_dir: Path, tool_version: str) -> int:
    cur = conn.execute(
        "INSERT INTO ingest_runs (started_at, tool_version, raw_dir) VALUES (?, ?, ?)",
        (now_iso(), tool_version, str(raw_dir)),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, notes: str = "") -> None:
    conn.execute(
        "UPDATE ingest_runs SET finished_at = ?, notes = ? WHERE run_id = ?",
        (now_iso(), notes, run_id),
    )
    conn.commit()


def log_build(
    conn: sqlite3.Connection,
    run_id: int | None,
    artefact: str,
    target: str,
    rows: int,
    size: int,
) -> None:
    conn.execute(
        "INSERT INTO build_log (run_id, artefact, target, rows_written, bytes_written, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, artefact, target, rows, size, now_iso()),
    )
    conn.commit()

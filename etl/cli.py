"""Command line.

    python -m etl ingest        # XLSX -> eight station tables, from scratch
    python -m etl aggregate     # rollups + the per-station channel measurements
    python -m etl export        # public/data, per station
    python -m etl report        # the per-station quality report
    python -m etl audit         # the build-time checks on the real archive
    python -m etl verify        # fail if the build != data/baseline.json
    python -m etl all           # all of the above, in order

Five stages rather than 0.8's seven, because two of them are gone: there is no
``regimes`` stage, since the confirmed scales are declarations in ``etl.catalog``
rather than things a detector finds, and no separate ``parquet`` stage, since
the interchange export is part of ``export`` and is optional.

``ingest`` rebuilds the database from nothing every time.  There is no append and
no update, by design -- see the module docstring in ``etl.build_db``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, audit, build_aggregate, build_db, build_exports, report, verify
from .config import Settings

__all__ = ["main"]

STAGES = ("ingest", "aggregate", "export", "report", "audit", "verify")
#: What `all` runs, and the order that matters. `aggregate` needs the ingest
#: (it reads the station tables) and `verify` needs everything, because the
#: baseline records the rollup bucket counts.
ALL_STAGES = ("ingest", "aggregate", "export", "report", "audit")


def _settings(args: argparse.Namespace) -> Settings:
    """Resolve the run's paths from the defaults plus whatever was passed.

    A dataclasses.replace chain rather than three branches that each rebuild the
    whole Settings: 0.8 had a version where ``--out-dir`` moved db_path but not
    report_json, which is the kind of thing that only shows up when a build has
    already run.
    """
    settings = Settings()
    for field in ("raw_dir", "out_dir", "export_dir"):
        value = getattr(args, field, None)
        if value:
            settings = replace(settings, **{field: Path(value)})
    return settings


def _baseline_path(args: argparse.Namespace, settings: Settings) -> Path:
    return Path(args.baseline) if getattr(args, "baseline", None) else settings.baseline_path


def _say(args: argparse.Namespace, message: str) -> None:
    if not getattr(args, "quiet", False):
        print(message)


def _open(settings: Settings, *, read_only: bool = False) -> sqlite3.Connection:
    from .db import connect

    if not settings.db_path.exists():
        raise SystemExit(f"no database at {settings.db_path}. Run `python -m etl ingest` first.")
    return connect(settings.db_path, read_only=read_only)


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def cmd_ingest(args: argparse.Namespace) -> int:
    settings = _settings(args)
    _say(args, f"solardata {__version__}: reading {settings.raw_dir}")
    summary = build_db.ingest(settings, verbose=not args.quiet)
    _say(args, f"  {summary.line()}")
    for station_id, (first, last) in sorted(summary.per_station_range.items()):
        _say(args, f"    {station_id:<16} {first[:10]} .. {last[:10]}")
    if summary.failed:
        _say(args, f"  WARNING: {summary.failed} file(s) failed")
    return 1 if summary.failed else 0


def cmd_aggregate(args: argparse.Namespace) -> int:
    settings = _settings(args)
    conn = _open(settings)
    try:
        _say(args, "aggregating")
        build_aggregate.build(conn, verbose=not args.quiet)
        return 0
    finally:
        conn.close()


def cmd_export(args: argparse.Namespace) -> int:
    settings = _settings(args)
    conn = _open(settings)
    try:
        _say(args, "exporting public/data")
        build_exports.build(conn, settings, verbose=not args.quiet)
        return 0
    finally:
        conn.close()


def cmd_report(args: argparse.Namespace) -> int:
    settings = _settings(args)
    conn = _open(settings)
    try:
        _say(args, "writing the quality report")
        report.write(conn, settings, verbose=not args.quiet)
        return 0
    finally:
        conn.close()


def cmd_audit(args: argparse.Namespace) -> int:
    settings = _settings(args)
    conn = _open(settings, read_only=True)
    try:
        _say(args, "auditing the build against the archive")
        checks = audit.run(conn, settings.raw_dir)
        failed = 0
        for check in checks:
            mark = "ok  " if check.ok else "FAIL"
            _say(args, f"  [{mark}] {check.name}: {len(check.notes)} note(s)")
            for note in check.notes:
                _say(args, f"         {note}")
            for failure in check.failures:
                print(f"         {failure}", file=sys.stderr)
                failed += 1
        if failed:
            print(
                f"\n{failed} audit failure(s). See above; the build is not trustworthy "
                "until they are resolved.",
                file=sys.stderr,
            )
        return 1 if failed else 0
    finally:
        conn.close()


def cmd_verify(args: argparse.Namespace) -> int:
    settings = _settings(args)
    conn = _open(settings)
    try:
        if args.update_baseline:
            if not args.reason:
                print(
                    "--update-baseline requires --reason. The reason travels with the "
                    "file so the diff explains itself.",
                    file=sys.stderr,
                )
                return 2
            path = _baseline_path(args, settings)
            payload = verify.write(conn, path, reason=args.reason)
            print(f"baseline re-recorded at {path}: {args.reason}")
            for name, value in payload["counts"].items():
                print(f"  {name:<20} {value:,}")
            return 0

        path = _baseline_path(args, settings)
        if not path.exists():
            print(
                f"no baseline at {path}. Record one with:\n"
                '  python -m etl verify --update-baseline --reason "first build"',
                file=sys.stderr,
            )
            return 2

        result = verify.check(conn, path)
        if result.ok:
            print(verify.render(result))
            return 0
        print(verify.render(result), file=sys.stderr)
        return 1
    finally:
        conn.close()


def cmd_all(args: argparse.Namespace) -> int:
    for name in ALL_STAGES:
        code = STAGE_FUNCS[name](args)
        if code:
            return code
    if not getattr(args, "update_baseline", False) and "verify" in (args.only or ALL_STAGES):
        return cmd_verify(args)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    settings = _settings(args)
    conn = _open(settings, read_only=True)
    try:
        cursor = conn.execute(args.sql)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
        if not rows:
            print("(no rows)")
            return 0
        widths = [max(len(c), *(len(str(r[i])) for r in rows)) for i, c in enumerate(columns)]
        print("  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True)))
        print("  ".join("-" * w for w in widths))
        for row in rows:
            print("  ".join(str(v).ljust(w) for v, w in zip(row, widths, strict=True)))
        print(f"\n{len(rows)} row(s)")
        return 0
    finally:
        conn.close()


STAGE_FUNCS = {
    "ingest": cmd_ingest,
    "aggregate": cmd_aggregate,
    "export": cmd_export,
    "report": cmd_report,
    "audit": cmd_audit,
    "verify": cmd_verify,
    "all": cmd_all,
    "query": cmd_query,
}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser, *, defaults: bool = True) -> None:
    """The flags that work on either side of the subcommand.

    ``python -m etl -q ingest`` and ``python -m etl ingest -q`` are the same
    command, and both spellings appear in the documentation, so both have to
    work.  The trick is ``argument_default=SUPPRESS`` on the subparser copy: a
    flag given before the subcommand is parsed into the namespace, and a
    subparser that then supplies its own default would overwrite it.  With
    SUPPRESS the subparser only sets a value when the flag is actually present,
    and the top-level parser supplies the defaults for the flags nobody gave.
    """
    group = parser.add_argument_group("common")
    default = None if defaults else argparse.SUPPRESS
    group.add_argument("--raw-dir", default=default, help="input archive (default data/raw)")
    group.add_argument(
        "--out-dir", default=default, help="artefact directory (default data/processed)"
    )
    group.add_argument(
        "--export-dir", default=default, help="site data directory (default public/data)"
    )
    group.add_argument(
        "-q",
        "--quiet",
        action="store_const",
        const=True,
        default=default if not defaults else False,
        help="print less (the tests use this)",
    )


def _add_verify_flags(parser: argparse.ArgumentParser, *, defaults: bool = True) -> None:
    default = None if defaults else argparse.SUPPRESS
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        default=default if not defaults else False,
        help="re-record data/baseline.json",
    )
    parser.add_argument(
        "--reason", default="" if defaults else default, help="why the numbers moved"
    )
    parser.add_argument("--baseline", default=default, help="baseline path")


HELP = {
    "ingest": "XLSX -> eight station tables, rebuilt from scratch",
    "aggregate": "hourly and daily rollups, and the per-station channel measurements",
    "export": "public/data: stations.json, metrics.json and one CSV set per station",
    "report": "the per-station data-quality report",
    "audit": "build-time checks against the real archive",
    "verify": "fail if the build does not match data/baseline.json",
    "all": "every stage, in order",
    "query": "read-only SQL against the built database",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m etl",
        description="Convert the raw IFTTT/Google-Sheets solar archive into a queryable store.",
        epilog=(
            "Common flags may go before or after the stage. Stages: "
            + ", ".join(sorted(HELP))
            + "."
        ),
    )
    parser.add_argument("--version", action="version", version=f"solardata {__version__}")
    _add_common(parser)
    _add_verify_flags(parser)
    sub = parser.add_subparsers(dest="stage", required=True, metavar="stage")

    for name in sorted(HELP):
        p = sub.add_parser(name, help=HELP[name])
        _add_common(p, defaults=False)
        if name in ("all", "verify"):
            _add_verify_flags(p, defaults=False)
        if name == "query":
            p.add_argument(
                "sql",
                help='read-only SQL, e.g. "SELECT station_id, COUNT(*) FROM stations GROUP BY 1"',
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # The subparsers use `argument_default=SUPPRESS` so that a flag given before
    # the stage is not overwritten by the stage's own default, which means every
    # value has to be defaulted here rather than by argparse.
    for name, fallback in (
        ("baseline", None),
        ("update_baseline", False),
        ("reason", ""),
        ("only", list(ALL_STAGES)),
    ):
        if not hasattr(args, name):
            setattr(args, name, fallback)
    try:
        return STAGE_FUNCS[args.stage](args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

"""Command line interface: ``python -m etl <command>``."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from etl import __version__
from etl.config import Settings, settings_from_env

STAGES = ("db", "regimes", "parquet", "export", "report", "verify")


#: Optional flags and their defaults.  The shared parent parser is built with
#: ``argument_default=SUPPRESS`` so that an unspecified flag sets *no*
#: attribute at all.  Without that, argparse's subparser re-applies its own
#: defaults to the namespace and silently clobbers anything given before the
#: subcommand -- which is exactly the bug this table exists to prevent.
_FLAG_DEFAULTS: dict[str, object] = {
    "raw_dir": None,
    "out_dir": None,
    "export_dir": None,
    "baseline": None,
    "only": None,
    "quiet": False,
    "hourly": False,
    "all_stations": False,
    "update_baseline": False,
    "reason": "",
}


def _apply_flag_defaults(args) -> None:
    for name, default in _FLAG_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, default)


def _settings(args) -> Settings:
    base = settings_from_env()
    overrides: dict = {}
    raw_dir = getattr(args, "raw_dir", None)
    out_dir = getattr(args, "out_dir", None)
    export_dir = getattr(args, "export_dir", None)
    only = getattr(args, "only", None)
    baseline = getattr(args, "baseline", None)

    if raw_dir:
        overrides["raw_dir"] = Path(raw_dir)
    if out_dir:
        # Redirecting the output directory has to move every artefact that
        # lives under it, otherwise the database lands in one place and the
        # report in another.
        out = Path(out_dir)
        overrides.update(
            out_dir=out,
            db_path=out / "solardata.db",
            parquet_dir=out / "parquet",
            report_json=out / "quality_report.json",
            report_md=out / "quality_report.md",
        )
    if export_dir:
        overrides["export_dir"] = Path(export_dir)
    if only:
        overrides["only"] = tuple(s.strip() for s in only.split(",") if s.strip())
    if getattr(args, "hourly", False):
        overrides["export_granularity"] = "hour"
    if baseline:
        overrides["baseline_path"] = Path(baseline)
    return Settings(**{**base.__dict__, **overrides})


def _selected(args, *wanted: str) -> bool:
    only = getattr(args, "only", None)
    if not only:
        return True
    return any(stage in only for stage in wanted)


def cmd_ingest(args) -> int:
    from etl.build_db import ingest

    settings = _settings(args)
    print(f"solardata etl {__version__}")
    print(f"raw  : {settings.raw_dir}")
    print(f"out  : {settings.out_dir}")
    started = time.time()
    summary = ingest(settings, verbose=not getattr(args, "quiet", False))
    elapsed = time.time() - started

    print()
    print(f"files read      {summary.files}")
    print(f"rows ingested   {summary.rows_ingested:,}")
    print(f"duplicates      {summary.rows_duplicate:,} (absorbed by the primary key)")
    print(f"rejected cells  {summary.rows_rejected:,}")
    print(f"notes recovered {summary.notes}")
    for station_id, n in sorted(summary.per_station.items()):
        rng = summary.per_station_range.get(station_id)
        span = f"  {rng[0]} -> {rng[1]}" if rng else ""
        print(f"  {station_id:<14} {n:>8,} rows{span}")
    if summary.unknown_dirs:
        print(f"unknown folders skipped: {summary.unknown_dirs}")
    print(f"\ndone in {elapsed:.1f}s -> {settings.db_path}")
    return 0


def cmd_regimes(args) -> int:
    from etl.build_regimes import detect
    from etl.db import connect

    settings = _settings(args)
    if not settings.db_path.exists():
        print(f"no database at {settings.db_path}; run `ingest` first", file=sys.stderr)
        return 1
    conn = connect(settings.db_path)
    try:
        print("detecting unit-scale regimes ...")
        detect(conn, verbose=True)
    finally:
        conn.close()
    return 0


def cmd_parquet(args) -> int:
    from etl.build_parquet import build
    from etl.db import connect, log_build

    settings = _settings(args)
    if not settings.db_path.exists():
        print(f"no database at {settings.db_path}; run `ingest` first", file=sys.stderr)
        return 1
    conn = connect(settings.db_path)
    try:
        print("writing Parquet ...")
        rows = build(conn, settings.parquet_dir, verbose=not getattr(args, "quiet", False))
        log_build(conn, None, "parquet", str(settings.parquet_dir), rows, 0)
    finally:
        conn.close()
    return 0


def cmd_export(args) -> int:
    from etl.build_exports import build
    from etl.db import connect, log_build

    settings = _settings(args)
    if not settings.db_path.exists():
        print(f"no database at {settings.db_path}; run `ingest` first", file=sys.stderr)
        return 1
    conn = connect(settings.db_path)
    try:
        print("writing frontend exports ...")
        written = build(
            conn,
            settings.export_dir,
            include_hourly=args.hourly,
            include_non_production=args.all_stations,
            verbose=not getattr(args, "quiet", False),
        )
        log_build(
            conn,
            None,
            "export",
            str(settings.export_dir),
            written["daily"] + written["hourly"],
            0,
        )
    finally:
        conn.close()
    return 0


def cmd_report(args) -> int:
    from etl.db import connect
    from etl.report import write

    settings = _settings(args)
    if not settings.db_path.exists():
        print(f"no database at {settings.db_path}; run `ingest` first", file=sys.stderr)
        return 1
    conn = connect(settings.db_path)
    try:
        report = write(conn, settings.report_json, settings.report_md)
    finally:
        conn.close()
    totals = report["totals"]
    print(
        f"report: {totals['readings']:,} readings, "
        f"{report['source_files']['total']} files "
        f"({report['source_files']['without_header']} without headers)"
    )
    print(f"  {settings.report_md}")
    print(f"  {settings.report_json}")
    return 0


STAGE_ORDER = ("regimes", "parquet", "export", "report")
STAGE_FUNCS = {
    "regimes": cmd_regimes,
    "parquet": cmd_parquet,
    "export": cmd_export,
    "report": cmd_report,
}


def cmd_all(args) -> int:
    """Full rebuild: ingest, then every downstream stage."""
    code = cmd_ingest(args)
    if code != 0:
        return code
    for stage in STAGE_ORDER:
        if not _selected(args, stage):
            continue
        print()
        code = STAGE_FUNCS[stage](args)
        if code != 0:
            return code
    # Verify last: it is the check on everything the other stages produced.
    # Skipped by default in `all` so a local rebuild is not gated on a stale
    # baseline file; CI runs `verify` explicitly.
    if _selected(args, "verify") and not getattr(args, "update_baseline", False):
        print()
        code = cmd_verify(args)
        if code != 0:
            return code
    return 0


def cmd_query(args) -> int:
    """Ad-hoc read-only SQL against the built database."""
    from etl.db import connect

    settings = _settings(args)
    if not settings.db_path.exists():
        print(f"no database at {settings.db_path}; run `ingest` first", file=sys.stderr)
        return 1
    conn = connect(settings.db_path, read_only=True)
    try:
        cursor = conn.execute(args.sql)
        names = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        if names:
            print(" | ".join(names))
            print("-" * 80)
        for row in rows:
            print(" | ".join("" if v is None else str(v) for v in row))
        print(f"\n({len(rows)} rows)")
    finally:
        conn.close()
    return 0


def cmd_verify(args) -> int:
    """Fail if the built database does not match data/baseline.json."""
    from etl.db import connect
    from etl.verify import check, render, write

    settings = _settings(args)
    if not settings.db_path.exists():
        print(f"no database at {settings.db_path}; run `ingest` first", file=sys.stderr)
        return 1
    conn = connect(settings.db_path, read_only=not args.update_baseline)
    try:
        if args.update_baseline:
            payload = write(conn, settings.baseline_path, reason=args.reason or "")
            print(f"baseline updated -> {settings.baseline_path}")
            for name, value in payload["counts"].items():
                print(f"  {name:<28} {value:>9}")
            print("\nReview this diff in the pull request: it is the record of what")
            print("changed in the data, not just in the code.")
            return 0

        if not settings.baseline_path.exists():
            print(
                f"no baseline at {settings.baseline_path};"
                " create one with `python -m etl verify --update-baseline`",
                file=sys.stderr,
            )
            return 1

        result = check(conn, settings.baseline_path)
    finally:
        conn.close()

    print(render(result))
    if result.ok:
        print("\nbaseline matches: the reading count did not move.")
        return 0

    print("\nBASELINE DRIFT -- the build no longer produces the recorded data.")
    print("If this change is intended, re-record it with:")
    print('  python -m etl verify --update-baseline --reason "<why>"')
    for name, (expected, actual) in result.drift.items():
        print(f"  {name}: {expected} -> {actual} ({actual - expected:+d})")
    return 1


def build_parser() -> argparse.ArgumentParser:
    # Shared flags live on a parent parser so they work on either side of the
    # subcommand: `python -m etl -q ingest` and `python -m etl ingest -q`.
    # SUPPRESS is essential here -- see _FLAG_DEFAULTS.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--raw-dir", help="override data/raw")
    common.add_argument("--out-dir", help="override data/processed")
    common.add_argument("--export-dir", help="override data/exports")
    common.add_argument("--only", help=f"comma separated subset of {','.join(STAGES)}")
    common.add_argument("-q", "--quiet", action="store_true", help="less per-file output")
    common.add_argument(
        "--hourly", action="store_true", help="also write hourly rollups (export/all)"
    )
    common.add_argument(
        "--all-stations",
        action="store_true",
        help="include bench/non-production stations (test, voltage-phumy)",
    )
    common.add_argument("--baseline", help="override data/baseline.json")

    parser = argparse.ArgumentParser(
        prog="python -m etl",
        parents=[common],
        description="Convert the raw IFTTT/Google-Sheets archive into a queryable store.",
    )
    parser.add_argument("--version", action="version", version=f"solardata-etl {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser(
        "ingest", parents=[common], help="XLSX -> SQLite (always the first stage)"
    )
    ingest.set_defaults(func=cmd_ingest)

    regimes = sub.add_parser("regimes", parents=[common], help="detect unit-scale changes")
    regimes.set_defaults(func=cmd_regimes)

    parquet = sub.add_parser("parquet", parents=[common], help="SQLite -> partitioned Parquet")
    parquet.set_defaults(func=cmd_parquet)

    export = sub.add_parser(
        "export", parents=[common], help="SQLite -> CSV rollups for the website"
    )
    export.set_defaults(func=cmd_export)

    report = sub.add_parser("report", parents=[common], help="write the data-quality report")
    report.set_defaults(func=cmd_report)

    every = sub.add_parser(
        "all", parents=[common], help="ingest + regimes + parquet + export + report"
    )
    every.set_defaults(func=cmd_all)

    query = sub.add_parser(
        "query", parents=[common], help="run read-only SQL against the built database"
    )
    query.add_argument("sql", help='e.g. "SELECT station_id, COUNT(*) FROM readings GROUP BY 1"')
    query.set_defaults(func=cmd_query)

    verify = sub.add_parser(
        "verify",
        parents=[common],
        help="fail if the build does not match data/baseline.json",
    )
    verify.add_argument(
        "--update-baseline",
        action="store_true",
        help="record the current build as the new baseline",
    )
    verify.add_argument("--reason", default="", help="why the baseline is moving")
    verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_flag_defaults(args)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)

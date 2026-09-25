"""Command line interface: ``python -m etl <command>``."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from etl import __version__
from etl.config import Settings, settings_from_env

STAGES = ("db", "regimes", "parquet", "export", "report")


def _settings(args) -> Settings:
    base = settings_from_env()
    overrides: dict = {}
    if args.raw_dir:
        overrides["raw_dir"] = Path(args.raw_dir)
    if args.out_dir:
        out = Path(args.out_dir)
        overrides.update(
            out_dir=out,
            db_path=out / "solardata.db",
            parquet_dir=out / "parquet",
            report_json=out / "quality_report.json",
            report_md=out / "quality_report.md",
        )
    if args.export_dir:
        overrides["export_dir"] = Path(args.export_dir)
    if getattr(args, "only", None):
        overrides["only"] = tuple(s.strip() for s in args.only.split(",") if s.strip())
    if getattr(args, "hourly", False):
        overrides["export_granularity"] = "hour"
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
    summary = ingest(settings, verbose=not args.quiet)
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
        rows = build(conn, settings.parquet_dir, verbose=not args.quiet)
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
            verbose=not args.quiet,
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


def build_parser() -> argparse.ArgumentParser:
    # Shared flags live on a parent parser so they work on either side of the
    # subcommand: `python -m etl -q ingest` and `python -m etl ingest -q`.
    common = argparse.ArgumentParser(add_help=False)
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)

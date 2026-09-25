# Changelog

All notable changes to `solardata` are recorded here, including findings about
the raw archive. The format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/).

## [0.4.0] — 2026-09-26

### Added

- **A working website.** Two tabs over the cleaned data:
  - **Explore** — pick a station, year and date range; chart any combination of
    solar voltage, battery, power, temperature and energy from the daily
    rollups, with a hover readout and summary tiles that include coverage
    (days reported, readings, hours covered) alongside the statistics.
  - **Data quality** — the database inspector: overview counts, per-folder
    breakdown, quality flags with their meaning, the unconfirmed scale regimes,
    the full channel-mapping table with confidence, the collector's own margin
    notes, and the rejected cells with samples.
- `src/data.js` — data access. Two properties of the dataset drive it:
  - An empty CSV cell stays `null` and breaks the chart line. It is never
    coerced to 0, because `0 W` at midnight and "the sensor was disconnected"
    are different facts and conflating them makes outages look like
    measurements.
  - Which metrics exist is discovered from the rows, not hardcoded, because
    `phumy2` has no `solar_v` or `battery_v` at all across 415k rows.
- `src/components/TimeSeriesChart.jsx` — a hand-rolled SVG line chart. No chart
  library: daily rollups need a line chart, and a package would be ~100 kB of
  JavaScript to draw two paths. Gaps break the path into separate subpaths, the
  hover target is the nearest row by date (so a day with no data still reports
  "no data"), and all series share one y-axis so an empty band reads honestly.
- `src/components/TimeControls.jsx`, `StatTiles.jsx`, `StationExplorer.jsx`,
  `QualityInspector.jsx`.
- `scripts/check_frontend.mjs` — 12 checks over the chart helpers and the real
  exported CSVs, run in CI. The two things most likely to be quietly wrong —
  coercing an empty cell to 0, and drawing a line across a gap — both produce a
  chart that looks fine and reads as data that does not exist, and a screenshot
  would not catch either.
- The export stage now writes to `public/data/` (Vite's static directory) and
  emits `quality.json` from the same `etl.report.collect` call the committed
  Markdown report uses, so the browser and the repository cannot disagree.
  167 KB across 15 files: `stations.json`, `quality.json`, 13 daily CSVs.
- A `frontend` CI job: the checks above, `npm run build`, and an assertion that
  the data files reached `dist/` — the deploy can otherwise succeed while every
  page shows an error.

### Changed

- `etl/config.py`: the default export directory moved from `data/exports/` to
  `public/data/`, so the site works from a plain clone with no server and
  `public/data` is committed rather than generated at deploy time.
- CI: the `build` job now also depends on `frontend`, and warns when the
  rebuilt `public/data/` differs from the committed copy — the same treatment
  the quality report gets, for the same reason.

### Findings

- **The SQLite file is not bloated.** `data/raw` is 30.4 MiB only because XLSX
  is deflate-compressed; uncompressed it is 251.4 MiB, so the database at
  158.2 MiB is **0.63× the raw XML**. The measurements are already 8-byte
  float64 (23 `REAL` columns, confirmed with `typeof()`). What is wasteful is
  30 MiB of `TEXT` — `ts_local` and `tz`, both derivable — but removing them
  yields ~128 MiB, still over GitHub's 100 MiB limit, so it would not make the
  database committable. Recorded in `docs/format-design.md` with the
  attribution, and deliberately deferred.

Build baseline unchanged: 735,004 readings, 4,403 duplicates, 10 notes,
23 regimes, 89 Python tests.

---

## [0.3.0] — 2026-09-25

### Added

- **Continuous integration.** `.github/workflows/ci.yml` runs on every push and
  pull request:
  1. `ruff check`, `ruff format --check`, `pytest` — fast, no data.
  2. A full `python -m etl all` against the real 364-file archive, then
     `python -m etl verify`. **Any baseline drift fails the job.**
  3. A Parquet freshness check. The committed `data/processed/parquet/` layout
     is snapshotted before the build and compared after, because the build
     overwrites it — otherwise a stale published artefact goes unnoticed.
  4. The build summary is written to `$GITHUB_STEP_SUMMARY` and the report is
     uploaded as an artefact.
- `.github/workflows/release.yml` attaches a `VACUUM`ed, gzipped
  `solardata.db` to a tag's GitHub Release, baseline-verified first.
- **`etl/verify.py` and the baseline guard.** `data/baseline.json` records the
  expected output of a build; `python -m etl verify` compares against it and
  exits non-zero on any drift. This is the only check that sees the real
  735,004-row archive, so it is the only thing that catches a pipeline change
  that silently alters the data while every unit test still passes. The two
  modes it targets:
  - **Loss** — a schema-inheritance regression leaves a headerless file mapped
    to no columns. Every timestamp still ingests; the measurements become NULL.
    The `headerless_without_donor` field is pinned to 0 for exactly this.
  - **Duplication** — `INSERT OR IGNORE` stops absorbing an overlap, so the same
    instant lands twice and the count rises by thousands.

  Re-record deliberately, with a reason, so the diff appears in the pull
  request as an explicit number rather than a silent rewrite.
- `scripts/parquet_manifest.py` — snapshot and compare the committed Parquet
  layout, so the same check runs locally.
- Committed artefacts: `data/processed/parquet/` (7.4 MiB),
  `data/processed/quality_report.md` and `.json`, and `data/baseline.json`. The
  processed data is now available from a clone without running anything.
- 27 new tests (89 total): the baseline guard against a real in-memory
  database, the CLI flag-parsing regression below, and the Parquet staleness
  logic.

### Fixed

- **Every CLI flag placed before the subcommand was silently ignored.**
  `python -m etl --out-dir /tmp ingest` used the default output directory and
  reported success. argparse's subparser re-applies its own defaults to the
  namespace, clobbering anything the parent parser had already set. The shared
  parent now uses `argument_default=SUPPRESS` with defaults applied explicitly,
  and `tests/test_cli.py::TestFlagParsing` locks it down. This also meant the
  documented claim that "common flags work on either side of the subcommand" was
  false for the four flags that existed before this release.

### Changed

- `data/exports/` is still gitignored: the rollups are only ~0.1 MiB but change
  on every build and nothing in `src/` reads them yet. Un-ignore when the
  frontend is wired up.

### Build baseline

Unchanged by this release — the numbers below are the same as 0.2.0, which is
the point: adding CI and committing artefacts did not move the data.

| | |
|---|---|
| Raw files | 364 in 10 folders |
| Readings | **735,004** across 8 stations |
| Range | 2020-05-16T15:52Z → 2024-02-01T20:18Z |
| Duplicate timestamps absorbed | 4,403 (each also recorded in `rejects`) |
| Malformed cells rejected | 6 |
| Notes recovered | 10 |
| Unconfirmed scale regimes | 23 |
| Artefact sizes | SQLite 158 MiB · Parquet 7.4 MiB · frontend CSV 0.1 MiB |

---

## [0.2.0] — 2026-09-25

First working pipeline over the whole archive, plus a full audit of the raw
XLSX files. **This release is where the findings below were established; they
are the reason the schema looks the way it does.**

### Added

- `etl/` — a Python package that converts `data/raw` into a queryable store.
  - `etl/readers/xlsx.py` — primary-column-block detection, header detection,
    SHA-256 of each raw file, full-sheet iteration for note recovery.
  - `etl/readers/times.py` — the single timestamp format and its UTC
    conversion.
  - `etl/normalize/` — header-to-metric mapping, sentinel and plausibility
    handling, scale-regime detection.
  - `etl/build_db.py` — the ingest, with per-file schema-donor resolution.
  - `etl/build_regimes.py`, `build_parquet.py`, `build_exports.py`, `report.py`.
  - `etl/schema.sql` — SQLite DDL: `stations`, `source_files`, `readings`,
    `readings_hourly`, `readings_daily`, `metric_defs`, `regimes`, `rejects`,
    `notes`, `ingest_runs`, `build_log`.
- `python -m etl {ingest,regimes,parquet,export,report,all,query}`.
- `data/processed/quality_report.md` and `.json` — the human-facing half of the
  pipeline.
- 61 tests, including real XLSX fixtures and regression tests for the two
  failures described below.- `AGENTS.md`, `docs/data-dictionary.md`, `docs/data-sources.md`,
  `docs/format-design.md`, `pyproject.toml`, `Makefile`, `requirements.txt`.

### Build baseline

| | |
|---|---|
| Raw files | 364 in 10 folders |
| Readings | **735,004** across 8 stations |
| Range | 2020-05-16T15:52Z → 2024-02-01T20:18Z |
| Duplicate timestamps absorbed | 4,403 (each also recorded in `rejects`) |
| Malformed cells rejected | 6 |
| Notes recovered | 10 |
| Unconfirmed scale regimes | 23 |
| Artefact sizes | SQLite 158 MiB · Parquet 6.8 MiB · frontend CSV 0.1 MiB |

---

## Findings about the raw archive

These are the discoveries that shaped the design. Each was measured, not
assumed.

### F1 — Only 59 of 364 files have a header row

Header presence is not random. It marks applet/firmware revision boundaries:
the header survives only on the chunks that happened to be re-exported.

| Folder | Files | With header | Inheriting a schema |
|---|---:|---:|---:|
| `AISVN_Solar` | 7 | 7 | 0 |
| `Maker_Webhooks_Events` | 5 | 5 | 0 |
| `Solar_2020-05-16` | 7 | 7 | 0 |
| `phumy2` | 102 | 21 | 81 |
| `phumy2a` | 100 | 2 | 98 |
| `aisvn` | 39 | 5 | 34 |
| `aisvn2` | 79 | 7 | 72 |
| `test` | 19 | 3 | 16 |
| `Voltage_phumy` | 3 | 1 | 2 |
| `phumy2b` | 3 | 1 | 2 |

A naive `pandas.read_excel` therefore reads a data row as a header for 84% of
the archive. Worse, the first implementation of this pipeline handled headerless
files by mapping *no* columns — which ingested all 735,004 timestamps and
discarded every measurement. That is why
`tests/test_ingest.py::TestHeaderlessSchemaInheritance` exists.

### F2 — Folder names are archive chunks, not stations

`phumy2`, `phumy2a` and `phumy2b` are consecutive 2000-row splits of one IFTTT
applet and cover a continuous June 2020 → February 2024 record. Treating them as
three stations would fragment every query. `etl/stations.py` maps all three
folders onto the single `phumy2` station and keeps the folder in
`source_files.source_dir` for provenance.

### F3 — The same header means different things at different times

`aisvn` files (25) and (28) share this header row:

```
time, solar, battery, current, power, load, wind, temp, solar2, LiPo, boot
```

but read:

| file | date | battery | temp | lipo |
|---|---|---:|---:|---:|
| `IFTTT_aisvn (25).xlsx` | 2020-10-28 | 29.12 V | 16.2 degC | 0.34 |
| `IFTTT_aisvn (28).xlsx` | 2021-11-02 | 14.44 V | 34.0 degC | 4.12 |

A 29 V battery and a 16 degC ambient temperature in Ho Chi City in October are
not measurements. No file records the change. This is the single most important
reason the schema keeps `metric_defs` and `regimes` as data rather than folding
meanings into column names.

### F4 — The collector's own notes explain F3

The side-block column L of `aisvn/IFTTT_aisvn (25).xlsx` contains 9 dated
annotations, now recovered into the `notes` table with UTC anchors:

| ts_utc | note |
|---|---|
| 2020-10-25T19:23Z | `this all is just garbage` |
| 2020-10-27T09:09Z | `STROMAUSFALL!!` |
| 2020-10-27T13:05Z | `Note8 provides Internet` |
| 2020-10-27T23:43Z | `normal Internet wieder da` |
| 2020-10-29T10:29Z | `at Library ...` |
| 2020-10-29T10:30Z | `Pin 4 is temperature - calibrated ...` |
| 2020-10-30T06:52Z | `leave home` |
| 2020-10-30T09:17Z | `arrive at school` |
| 2020-10-30T11:05Z | `Installed in the dark, let's start again!` |

`Pin 4 is temperature - calibrated ...` is dated 2020-10-29, and the
`aisvn` temperature record confirms it. Monthly means of in-range readings:

| month | n | mean temp_c |
|---|---:|---:|
| 2020-09 | 5 | 33.4 |
| 2020-10 | 2,588 | **22.3** |
| 2020-11 | 4,779 | 32.4 |

The whole `aisvn` temperature channel spans 0.0–342.1 degC. October 2020 is the
broken window; the notes say the hardware was being reinstalled and pin 4
recalibrated. This needs a human decision, not a heuristic.

### F5 — `-992` and `-1` are sentinels, not measurements

13,034 cells of `-992` (8,366 in `Maker_Webhooks_Events`, 4,668 in `test`) and
10,467 cells of `-1`. These mean the input was floating or disconnected. They
become `NULL` with `quality_flags = 'sentinel'`. Storing them as 0 or averaging
them in would drag every aggregate towards zero.

### F6 — `boot` is a counter, not a flag

`boot` increments by one per reading (14875 → 14876) and resets when the logger
rebools. It is stored as `boot_count` (INTEGER) and is a useful reboot detector,
but it is not a boolean despite the name.

### F7 — Redundant side-by-side column blocks

Many sheets carry 2–3 parallel blocks of the same observations separated by an
empty column, each with its own `time` header. They are Google Sheets formula
experiments with mostly-zero duplicates. `Voltage_phumy.xlsx` has three blocks,
one of which is a hand-made summary table at a coarser time granularity
(`04:08AM`, `04:08:00`) plus the lab annotations. Reading `A:Z` interleaves
them. `detect_block` finds the primary block; `iter_all_cells` exists only to
recover the prose.

### F8 — Six files repeat a header row mid-file

A new block of readings was appended below an existing block, so the header
repeats partway down. All 6 occurrences are recorded in `rejects` with the sheet
row number.

### F9 — Timestamps are US-locale text with no timezone

`July 14, 2020 at 10:12AM` — English month names, 12-hour clock, no seconds, no
offset. Lexicographic sorting is wrong (`April` < `August` < `December` <
`February`). The ingest stores `ts_utc` (RFC 3339, `Z`) plus `ts_local` and
`tz`. All stations are assumed `Asia/Ho_Chi_Minh` (UTC+07:00, no DST) — an
assumption, not a measurement.

### F10 — Duplicates are two different things

4,403 within-station collisions, which the `(station_id, ts_utc)` primary key
absorbs. Separately there are ~121,000 timestamps shared *between* stations;
those are different instruments sampling the same wall-clock instant and are
correctly kept as separate rows. Conflating the two would have deleted real
data.

The heavy concentrations are `test` (2,390) and `voltage-phumy` (2,013), where
the sheet is a concatenation of several exports rather than a clean 2000-row
chunk. `IFTTT_phumy2 (33).xlsx` holds 9,724 body rows for the same reason.

Each absorbed duplicate also gets a `rejects` row with
`reason = 'duplicate_ts'`, so the count above resolves to individually
inspectable rows rather than being a summary figure. The reason is a stable
category precisely so the report can group by it; per-row detail lives in
`raw_value` and `sheet_row`.

### F11 — The `test` folder is not a solar station

`IFTTT_test (2).xlsx` has the header `time, nix, temp, wifi` — a WiFi/temperature
probe. The folder mixes at least three unrelated schemas at the same column
count, so the folder name cannot be used to infer the layout. It and
`voltage-phumy` are marked `is_production = 0` and excluded from published
exports.

### F12 — Sampling is 2-minute, with real gaps

357 of 364 files have a median 2-minute interval; 6 have 1-minute. Coverage has
multi-month gaps between collector generations, which the report lists as
`coverage_gaps`. A 2-minute cadence is baked into the `energy_wh` calculation in
`readings_hourly`; revisit it if a station's cadence is confirmed to differ.

---

## Open questions

Carried in `AGENTS.md` § "Open questions a human still has to answer": timezone
confirmation, the 23 scale regimes, the `aisvn` October-2020 window, the
`aisvn (25).xlsx` channel meanings, the meaning of `load_v`, and whether
`test`/`voltage-phumy` should ever be published.

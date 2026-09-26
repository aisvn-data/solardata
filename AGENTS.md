# AGENTS.md

Guidance for AI agents and humans working in this repository.

## What this repository is

`solardata` holds four years of solar telemetry collected by several stations in
Nha Be and Phu My Hung, Ho Chi City, Vietnam, between May 2020 and February
2024. The readings were forwarded to Google Sheets by IFTTT webhooks and the
Sheets were later exported as XLSX and chunked into files of 2000 rows.

Two independent halves live here:

| Path | Language | Purpose |
|---|---|---|
| `etl/` | Python | Convert the raw archive into a queryable store |
| `src/` | React + Vite | The website that displays it |
| `public/data/` | CSV + JSON | What the website fetches, written by `etl/build_exports.py` |

They meet only at `public/data/`, a committed build artefact. `etl/` never
imports from `src/`, and `src/` never imports from `etl/`.

## Commands

```bash
pip install -r requirements.txt   # or: make setup

python -m etl all                 # full rebuild (~3 min; the ingest is the slow part)
python -m etl ingest              # XLSX -> SQLite
python -m etl regimes             # detect unit-scale changes
python -m etl aggregate           # hourly/daily rollups, applying confirmed scales
python -m etl parquet             # SQLite -> partitioned Parquet
python -m etl export              # SQLite -> CSV rollups for the website
python -m etl report              # write the data-quality report
python -m etl verify              # fail if the build != data/baseline.json
python -m etl query "SELECT ..."  # ad-hoc read-only SQL

make test                         # pytest
make check                        # ruff + pytest
```

`make help` lists everything. The Makefile is a thin convenience wrapper and
needs GNU Make; the `python -m etl` commands above work anywhere. Common flags
work on either side of the subcommand (`python -m etl -q ingest` and
`python -m etl ingest -q` are the same), and `--raw-dir` / `--out-dir` redirect
the input and output.

## The rules that matter

These are not style preferences. Each one exists because breaking it produces
plausible-looking but wrong data, which is the worst failure mode for a
scientific dataset.

### 1. Never edit anything under `data/raw/`

It is the primary source of truth and the only copy. The pipeline is
reproducible; the archive is not. If a raw file looks wrong, that is a finding
to document, not a file to fix.

### 2. Never drop a value silently

Every raw cell must end up in exactly one place:

- a typed column in `readings`, or
- `NULL` in `readings` plus a reason in `quality_flags`, or
- the `rejects` table, or
- the `notes` table, if it is human prose.

This is why `-992` becomes `NULL` with `quality_flags = 'sentinel'` rather than
0, and why an implausible reading is *flagged and kept* rather than filtered.
It is also why a duplicate timestamp, which the primary key discards, still gets
a `rejects` row with `reason = 'duplicate_ts'`. A future reader must be able to
disagree with a decision and see exactly which rows it affected.

Keep `rejects.reason` a stable category, not a sentence. The report groups by
it, and interpolating a timestamp into the text turns 4,403 duplicates into
4,361 singleton groups. Per-row detail belongs in `raw_value` and `sheet_row`.

### 3. Never rescale a value on the strength of a heuristic

`regimes` records scale *proposals* with `status = 'unconfirmed'`. Only
`status = 'confirmed'` is ever applied, and only in the rollups, never in
`readings`. If you find yourself writing `value * 0.001` in a query, stop: either
the regime has been confirmed, or you are inventing data.

### 3a. The rollups are a separate stage, and the order matters

`aggregate` runs *after* `regimes`, not inside `ingest`. Applying a scale needs
the `regimes` table, and rolling up during the ingest meant the daily table was
built before the detector ran — so on a clean build it came out silently
unscaled. If you add a stage, check every workflow that calls `verify` still
runs it; `tests/test_workflows.py` does that automatically.

### 4. Parse timestamps; never compare them as strings

Column A is US-locale free text (`July 14, 2020 at 10:12AM`). Lexicographic
ordering is wrong — `April` sorts before `August` — so every comparison,
`GROUP BY`, and donor-selection must go through
`etl.readers.times.parse_local`. Use `ts_utc` (RFC 3339, `Z`) for anything
analytical and `ts_local` only for display.

### 5. Read only the primary column block

Many sheets carry two or three parallel blocks of the same readings separated by
an empty column. `etl.readers.xlsx.detect_block` finds the primary block; use
`iter_cells` rather than reading `A:Z`. The side blocks are Google Sheets
formula experiments with mostly-zero duplicates, plus — in `Voltage_phumy` — a
hand-made summary table at a coarser time granularity and the lab annotations.

Side-block *prose* is still worth reading: `iter_all_cells` exists solely to
recover it into the `notes` table.

### 6. 305 of the 364 raw files have no header row

A headerless file borrows its column meaning from the nearest *preceding*
sibling that has one, recorded in `source_files.schema_donor` with
`inferred = 1`. Donor selection must order on the **parsed** timestamp. If you
change that logic, `tests/test_ingest.py::TestHeaderlessSchemaInheritance` will
catch it, because getting it wrong silently discards 90% of the archive's
measurements while still ingesting every timestamp.

### 7. `NULL` and `0` are different facts

`0 W` at midnight is a real measurement. `NULL` means "not measured, or the
sensor was disconnected". Aggregations must decide explicitly which they want;
`COUNT(col)` versus `COUNT(*)` is usually the distinction that matters.

## Where things live

```
etl/
  cli.py            argparse entry point, one function per stage
  config.py         paths, sentinel list, quality-flag names
  stations.py       folder -> station registry, and the timezone assumption
  schema.sql        the full SQLite DDL; read this before changing a query
  db.py             connection handling, ingest_runs bookkeeping
  verify.py         the baseline guard CI enforces
  readers/
    times.py        the one timestamp format, and its UTC conversion
    xlsx.py         block detection, row iteration, SHA-256
  normalize/
    metrics.py      canonical columns, units, header -> column mapping
    quality.py      sentinels, plausibility flags, note detection
    units.py        scale-regime detection
  build_db.py       the ingest
  build_regimes.py  scale-regime detection over the built database
  build_parquet.py  Parquet interchange output
  build_exports.py  CSV rollups the website fetches
  report.py         the data-quality report
scripts/
  parquet_manifest.py   snapshot/compare the committed Parquet layout
  check_frontend.mjs    chart + CSV semantics, over the real exports
```

## Regenerating after a change

`ingest` deletes and rebuilds `data/processed/solardata.db` from scratch, so
there is no incremental-update logic to get wrong. After changing anything in
`etl/`, run:

```bash
make check && python -m etl all && python -m etl verify
```

Then read `data/processed/quality_report.md` and check the numbers did not move.
A change that silently alters the reading count is a bug even if every test
passes, so treat the report as the acceptance test for data changes — and let
`verify` enforce it, because it is the only check that sees the real archive.

Current baseline, for comparison: **734,908 readings** across 8 stations from
364 files, 4,399 duplicate timestamps absorbed, 106 rejected cells, 10
recovered notes, 14 unconfirmed scale regimes.

### When the numbers *should* move

Some changes legitimately alter the output — a corrected timezone, a new
station, a fixed header mapping. Re-record the baseline deliberately, with a
reason, so the diff appears in the pull request as an explicit number rather
than a silent rewrite:

```bash
python -m etl verify --update-baseline --reason "corrected tz for phumy2a"
```

Never "fix" a red build by loosening `data/baseline.json` by hand. The baseline
is the record of what the data is, and CI failing is the point.

## Continuous integration

Four workflows, split by cost and by what they actually read.

| Workflow | When | Cost |
|---|---|---|
| `ci.yml` | every push and pull request | ~1 min |
| `data.yml` | only when `data/raw/**`, `etl/**`, `data/baseline.json` or `requirements.txt` change; plus Mondays 03:17 UTC and on demand | ~1 min |
| `pages.yml` | push to `main`, or manually | ~30 s |
| `release.yml` | `v*` tag, or manually | ~2 min |

**`ci.yml`** is the required gate: `ruff check`, `ruff format --check`,
`pytest`, the frontend checks and `npm run build`. It is deliberately *not*
path-gated — a docs-only change must still show a CI run.

**`data.yml`** runs the full pipeline over all 364 raw files and then
`python -m etl verify`. Any baseline drift fails the job. It is gated because a
pull request that touches only `src/` cannot change a reading: the data comes
from `data/raw` through `etl/`, and both are committed. Rebuilding proves
nothing and costs minutes.

Two things to know before changing the gate:

- **It is deliberately not a required status check.** A path-gated workflow
  produces *no run at all* when the paths do not match, and a required check
  that never appears leaves a pull request waiting forever.
- **The weekly schedule is the backstop.** A path filter only sees the paths
  GitHub reports, so anything it misses is caught on Monday. Keep it.

`tests/test_workflows.py` asserts the split: that `data.yml` is gated on the
right paths and includes a schedule, that `ci.yml` is not gated and does not
depend on the build, and that any workflow calling `verify` runs every stage
the baseline depends on.

The build is roughly a minute rather than three because the reader caches
parsed sheets: profiling showed `_read_rows` called 1,820 times for 364 files,
since each file was parsed once to scan it, again in `detect_block`, and again
in `iter_cells`. Reading a sheet is 86% of the build, so removing the
redundancy is the whole win. `tests/test_ingest.py::TestReadCache` covers it.

### Why `solardata.db` is not committed

It is 158 MiB, over GitHub's 100 MiB per-file limit for a git blob, so a commit
of it would be rejected outright. What *is* committed:

| Path | Size | Why |
|---|---|---|
| `data/processed/parquet/` | 7.4 MiB | The interchange format; gives anyone the processed data from a clone |
| `public/data/` | 167 KB | The CSV/JSON rollups the site fetches, so GitHub Pages works from a clone |
| `data/processed/quality_report.md` | ~10 KB | The review artefact, readable in a pull request |
| `data/processed/quality_report.json` | ~80 KB | Machine-readable form of the same |
| `data/baseline.json` | ~1 KB | The expected counts CI enforces |
| `data/raw/**` | 30.4 MiB | The primary source of truth |

`solardata.db` and the retired `data/exports/` are gitignored. Rebuild locally
with `make build`, or download the database from a Release.

## The website

Plain JSX, no TypeScript, no state library, no chart library. Data flows one
way: `python -m etl export` writes `public/data/`, `src/data.js` fetches it, and
the components render it. There is no build step between the CSV and the DOM.

Two rules the frontend inherits from the pipeline, and the reason for each:

- **A gap is a gap.** An empty cell in `readings` reaches the chart as `null`
  and breaks the line. If you ever coerce it to 0 — even "just for the chart" —
  every sensor outage becomes a measurement, and the chart will look correct.
- **Available metrics are discovered, not declared.** `phumy2` has no `solar_v`
  or `battery_v` at all, so `availableMetrics()` derives the list from the rows
  and the UI disables what a station does not have.

`node scripts/check_frontend.mjs` guards both, and runs in CI. Add to it when you
change the chart or the CSV parsing.

### Deploying to GitHub Pages

`.github/workflows/pages.yml` builds `dist/` and publishes it on every push to
`main`. Two things are easy to get wrong:

- **Pages must be enabled once in the repository settings**, which no workflow
  file can do for you: *Settings → Pages → Build and deployment → Source →
  "GitHub Actions"*. Until that is set, every request returns
  `404 There isn't a GitHub Pages site here` no matter what the workflow does.
- **`base` in `vite.config.js` is `/solardata/`**, which is the project-pages
  path for a repository named `solardata` under the `kreier` account. Renaming
  either requires changing `base` too, or every asset and data fetch 404s.

The data files are committed under `public/data/`, so a frontend-only change
deploys without re-running the Python pipeline. The deploy asserts that
`dist/data/` contains `stations.json`, `quality.json` and all 13 CSVs, so a
build that loses them fails instead of publishing a site full of errors.

## Open questions a human still has to answer

These are recorded, not solved. Do not quietly decide them in code.

1. **The 14 remaining unconfirmed scale regimes.** 13 are confirmed
   (millivolts as integers, collector-verified, including `phumy2.solar2_v` and
   `phumy2.lipo2_v`). What is left needs the firmware. The weakest are the
   `aisvn` ×0.001 windows either side of 2020-06-17, which are only three days
   each and sit in the commissioning period.
2. **The `phumy2` bridge ratio.** `solar2_v` is confirmed as millivolts, but the
   level steps from ~5000 mV to ~1200 mV when a bridge and load were fitted, so
   the stored value is a divider output rather than the panel voltage. Without
   the ratio, `solar2_v` after the bridge is not a panel voltage and should not
   be charted as one.
3. **`aisvn.load_v` behaviour change.** The collector reports the load rail as
   10–12 V when a load is switched on and 0 when none is present. The 0 state
   works up to 2020-07-10 and persists sporadically until 2020-10-30
   (28,192 readings: 80% of June, 78% of July, 19% of August, 0% from November
   onwards, where the channel is 9.5–24.7 V and never 0). What changed, and
   whether the 0 readings after July are genuine or a stuck pin, is unknown.
4. **The `aisvn` gaps.** No readings between 2020-10-25 and 2020-11-04, and
   September 2020 has only 12 readings. **Confirmed by the collector: the
   collector was down, no data was lost in the Sheets export.** No action
   needed; recorded so nobody goes looking for a bug.
5. **Non-production stations** stay excluded from published exports. `test` is
   a WiFi probe mixed with solar channels, and its temperature channel peaks at
   21:00, consistent with being indoors. `voltage-phumy` is an ADC calibration
   sheet.
6. **`phumy2.current2_a`.** Reads 155–1997 against a ±50 A band, so it is
   milliamps, and is flagged `out_of_range` on all 415,117 rows. The scale is not
   confirmed.

## Conventions

- Python 3.11+, `from __future__ import annotations`, full type hints.
- `ruff` for lint and format (`make fmt`), 100-column lines, double quotes.
- Docstrings explain *why*, especially where the archive forced a strange
  decision. A comment restating the code is noise; a comment recording which
  file and row motivated the code is the point.
- Tests use plain `unittest` assertions under `pytest`, and build real XLSX
  fixtures so they exercise the same openpyxl path as production.
- The frontend is plain JSX with no TypeScript and no state library. Keep it
  that way until there is a reason not to.
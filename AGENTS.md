# AGENTS.md

Guidance for AI agents and humans working in this repository.

## What this repository is

`solardata` holds six years of solar telemetry collected by eight stations in
Nha Be and Phu My Hung, Ho Chi City, Vietnam, between May 2020 and September
2026. The readings were forwarded to Google Sheets by IFTTT webhooks and the
Sheets were later exported as XLSX and chunked into files of 2000 rows.

Two independent halves live here:

| Path | Language | Purpose |
|---|---|---|
| `etl/` | Python | Convert the raw archive into a queryable store |
| `src/` | React + Vite | The website that displays it |
| `public/data/` | CSV + JSON | What the website fetches, written by `python -m etl export` |

They meet only at `public/data/`, a committed build artefact. `etl/` never
imports from `src/`, and `src/` never imports from `etl/`.

## The one idea of 0.9

**A channel is a fact about a station, not a fact about a column name.**

`solar_v` is 0-29.8 V at `aisvn` and 0-23,860 at `aisvn2`, because the second
station's sheet logged millivolts. 0.8 carried one plausibility band per *column
name* in `etl/normalize/metrics.py`, a per-station unit override in
`config.CHANNEL_UNITS`, and 22 confirmed scale factors in `build_regimes.py`, and
read them in seven places. The band and the scale were each measured against the
other, both were wrong, and the result was **631,252 `out_of_range` flags, 99.4%
of them arithmetic rather than a fact about the hardware** — including all
416,088 of `phumy2`'s readings, on a sensor measuring a quarter of an amp, which
is a station the site then reported as broken.

Everything below follows from fixing that:

- **Eight tables, one per station.** `s_aisvn`, `s_aisvn2`, `s_aisvn_solar`,
  `s_maker_webhooks`, `s_phumy2`, `s_solar_2020_05`, `s_test`,
  `s_voltage_phumy`. Each holds only what that station collects. 75% of the cells
  in 0.8's single 32-column `readings` table were NULL, and no reader could tell
  which NULLs meant "not connected" from which meant "not measured".
- **One unit, everywhere.** The confirmed scale is applied once, at ingest. The
  database, the rollups, the CSVs, the browser and the axis label all hold the
  same number in the same unit. No regime table, no stored-vs-display split, no
  out-of-range count recomputed after the fact.
- **`etl/catalog.py` is the documentation.** One declaration per
  `(station, channel)`: label, description, kind, unit, confirmed scale,
  plausibility band, which statistics the rollups carry, and whether the site
  charts it. The database, the report and the browser are all generated from it.
  If you are about to add a second place to put a number, don't.
- **A station's CSV carries only that station's channels.** So a channel cannot
  be listed-but-unreachable, or present as a column of NULLs, because it is only
  ever a column where the station has it.

## Commands

```bash
pip install -r requirements.txt   # or: make setup

python -m etl all                 # full rebuild (~2 min; the ingest is the slow part)
python -m etl fresh               # delete the database, then all, then verify
python -m etl ingest              # XLSX -> eight station tables, from scratch
python -m etl aggregate           # rollups + the per-station channel measurements
python -m etl export              # public/data, one CSV set per station
python -m etl report              # the per-station quality report
python -m etl audit               # checks against the real archive
python -m etl verify              # fail if the build != data/baseline.json
python -m etl query "SELECT ..."  # ad-hoc read-only SQL

python scripts/release_notes.py          # the notes for the version in package.json
python scripts/release_notes.py --check  # exit 2 if version, code and changelog disagree

make test                         # pytest
make check                        # ruff + pytest
```

`fresh` is `data_fresh.yml` from the command line, and it is the command to reach
for after changing `etl/catalog.py`. It removes the database *and its `-wal` and
`-shm` sidecars* first, because SQLite replays a surviving write-ahead log on the
next open — deleting only the `.db` can hand back rows from the previous run.
Excel also drops a 165-byte `~$`-prefixed owner file beside any workbook it has
open; those are skipped rather than read, because openpyxl raises
`PermissionError` on one and the build used to die on it.

Five stages rather than 0.8's seven: there is no `regimes` stage, because the
confirmed scales are declarations in `etl/catalog.py` rather than things a
detector finds, and no `parquet` stage, because the interchange export is not
worth a fifth stage and its own `pyarrow` dependency.

`ingest` **deletes and rebuilds** the database every time. There is no
incremental-update logic to get wrong, and `data_fresh.yml` exists to make a
from-scratch run reproducible and dispatchable.

Common flags work on either side of the stage: `python -m etl -q ingest` and
`python -m etl ingest -q` are the same command. `--raw-dir` and `--out-dir`
redirect the input and the output.

## The rules that matter

These are not style preferences. Each one exists because breaking it produces
plausible-looking but wrong data, which is the worst failure mode for a
scientific dataset.

### 1. Never edit anything under `data/raw/`

It is the primary source of truth and the only copy. The pipeline is
reproducible; the archive is not. If a raw file looks wrong, that is a finding to
document, not a file to fix.

### 2. Never drop a value silently

Every raw cell must end up in exactly one place:

- a typed column in that station's table, in that channel's published unit, or
- `NULL` plus a reason in `flags`, with a `rejects` row, or
- the `rejects` table, or
- the `notes` table, if it is human prose.

This is why `-992` becomes `NULL` with `flags = 'sentinel'` rather than 0, and
why an implausible reading is *flagged and kept* rather than filtered. It is also
why a duplicate timestamp, which the primary key discards, still gets a `rejects`
row with `reason = 'duplicate_ts'`. A future reader must be able to disagree with
a decision and see exactly which rows it affected.

Keep `rejects.reason` a stable category, not a sentence. The report groups by
it, and interpolating a timestamp into the text turns 4,403 duplicates into
4,361 singleton groups. Per-row detail belongs in `raw_value` and `sheet_row`.

### 3. Never rescale a value on the strength of a heuristic

Every non-1.0 `scale` in `etl/catalog.py` was confirmed against the hardware.
There are five, and they are all divisors of 1000: a collector that logged
millivolts or milliamps as integers. There is no detector any more — 0.8's
proposed 11 windows, 2 of which stayed unconfirmed forever, are now open
questions in `etl/catalog.py` and `docs/roadmap.md`. If you find yourself
writing `value * 0.001` in a query, stop: either the channel declares it, or you
are inventing data.

### 4. A band belongs to a (station, channel), in the unit the value is stored in

Not to a column name, and not in the unit the sheet happened to write. Testing a
millivolt cell against a volt band is what produced 631,252 flags.

**That includes the shared rollup.** `readings_hourly` and `readings_daily` are one
table fed by a `UNION ALL` of the eight station tables, so a `<channel>_n_oor`
counter needs one SQL expression to serve all eight branches — and
`_banded_published()` used to deduplicate by channel *name* and take the first
station's band. `aisvn` is declared first, so its 9–16 V band was applied to
every station's `battery_v`, and `aisvn-solar`'s, stored in volts at 0–5.148 V
after a confirmed ×0.002, had **all 13,788 of its readings flagged**. The
per-channel totals were all correct, so nothing noticed: the rollup was the only
place the two disagreed. `_bound_case` now keys the test on the row's own
`station_id`. Three stations publish a `battery_v`; a one-sided band tests only
the side it declares, so `aisvn2.current_a_chA`'s ceiling of 500 does not report
its 55% of negative readings as faults.

**A band that fires on more than `BAND_FIRE_FRACTION` (1%) of a channel's own
record is not a band.** It is reporting a unit mismatch, or the channel is
bimodal, and either way a count that fires on one sample in five cannot tell a
contaminated aggregate from a normal day. Anything above the threshold must carry
a `band_note` saying why the fire *is* the finding, and `python -m etl audit`
fails without one. `aisvn.solar2_v` sits at 19.5 V and `aisvn.lipo_v` at 6.84 V,
each for 18% of the record, so both are banded at the collector's stated ceiling
**and** annotated — 0.9.0 bounded `lipo_v` at 0–8.7 V purely to stop it firing,
which is a band widened to be quiet.

A channel with **no** band is a decision, not an omission: an uncalibrated ADC
count, an uptime counter, and any channel whose unit is unresolved. It needs a
`band_note` saying so, and it is never flagged. A one-sided band is the honest
form for a sensor that is genuinely bipolar.

### 5. A time-scoped change is a `Correction`, dated and declared

Not a scale, and not an average. `aisvn`'s current channel reads 6.6 A low from
2020-08-24 18:42 local and its power channel's output is inverted and four times
too large from the same instant; both are the collector's dated fault, and both
are `etl.catalog.Correction` declarations — `add 6.6` and `factor -0.25` — applied
once at ingest, after the scale, only where a declared window covers the reading's
instant.

Windows are half-open `[from_ts, to_ts)`, so a boundary cannot be claimed twice —
with an `add` that is a double-counted offset, not a merely redundant one. `op`
is deliberately only `add` and `factor`: a correction needing a conditional is a
symptom that the period is described wrongly, and the way to find that out is to
make the declaration impossible to write. Where no clock is available, nothing is
applied, because guessing a period is the thing this exists to stop.

0.8 needed 22 confirmed scale windows for one station and left 8 more unconfirmed
forever. There are two declarations here, both dated, both from the collector.

### 6. Parse timestamps; never compare them as strings

Column A is US-locale free text (`July 14, 2020 at 10:12AM`). Lexicographic
ordering is wrong — the sheets write `July 4` and `July 14` unpadded, so string
order puts the fourteenth before the fourth — so every comparison, `GROUP BY`
and donor-equivalent must go through `etl.readers.times.parse_local`. Use
`ts_utc` for anything analytical and `ts_local` only for display.

`parse_local` reads the fields out of its own regex rather than calling
`strptime`, because `%B` and `%p` resolve out of the C library's `LC_TIME`
tables, which are not loaded on Windows. With `strptime` every cell in the
archive fails to parse: zero readings and 738,358 rejects, in a shape that reads
as a data problem and is a locale one.

### 7. A file's column meanings come from the catalog, keyed on (station, width)

305 of the 364 raw files have no header row. Their schema is a declared layout in
`etl/catalog.py`, looked up by the sheet's width. The archive contains exactly
nine `(station, width)` pairs and no station has two layouts of the same width,
so the lookup is total.

**An undeclared width is a hard build failure.** 0.8 resolved it by finding the
nearest preceding sibling with a header of the same width, ordering on the parsed
timestamp, and getting that wrong discarded 90% of the archive's measurements
while still ingesting every timestamp and reporting no problem.
`tests/test_ingest.py` asserts both halves: that a headerless file resolves, and
that an undeclared width stops the build.

### 8. NULL and 0 are different facts

`0 W` at midnight is a real measurement. `NULL` means "not measured, or the
sensor was disconnected". Aggregations must decide explicitly which they want;
`COUNT(col)` versus `COUNT(*)` is usually the distinction that matters. An empty
CSV cell stays empty and breaks the chart's line.

### 9. A flag describes the value you stored, not the value you publish

They are the same value in 0.9, and that is the point. The confirmed scale is
applied on the way in, so `flags`, the rollup's `<channel>_n_oor` counter and the
band the chart rings a value against are all the same criterion, tested in the
same unit, on the same number. 0.8 stored the raw value, applied the scale only
in the rollups, and needed `_rescale_oor_counts` to stop the two disagreeing
about which values were out of range.

## Where things live

```
etl/
  catalog.py      THE DOCUMENTATION: stations, layouts, channels, units, scales,
                  bands, exclusions, and each station's open questions
  config.py       sentinels, flag and reason vocabularies, and the human
                  decisions about specific files and windows
  cli.py          argparse entry point, one function per stage
  schema.sql      the shared DDL; the eight station tables and the two rollups
                  are generated from catalog.py (see db.py)
  db.py           connections, the generated DDL, run bookkeeping
  audit.py        build-time checks against the real archive
  verify.py       the baseline guard
  report.py       the per-station quality report, in Markdown and JSON
  readers/
    times.py      the one timestamp format, and its UTC conversion
    xlsx.py       block detection, row iteration, a parse cache, SHA-256
  build_db.py         the ingest
  build_aggregate.py  rollups and channel_stats
  build_exports.py    public/data
scripts/
  check_frontend.mjs   chart + CSV semantics, over the real exports
  check_render.mjs     the component tree, rendered
  release_notes.py     the version, and the notes for it, from one place
```

Three tables are **generated** rather than written in `schema.sql`: the eight
station tables, and `readings_hourly`/`readings_daily`. Their columns are the
channels the catalog declares, so a hand-written table for each would drift from
the catalog the moment a channel was added — which is what happened in 0.8, where
columns were named in four files and the four had already drifted once.

## Regenerating after a change

`ingest` deletes and rebuilds `data/processed/solardata.db`, so there is no
incremental path to get wrong. After changing anything in `etl/`, run:

```bash
make check && python -m etl all && python -m etl verify
```

Then read `data/processed/quality_report.md`. A change that silently alters the
reading count is a bug even if every test passes, so treat the report as the
acceptance test for a data change — and let `verify` enforce it, because it is
the only check that sees the real archive.

Current baseline, for comparison: **731,885 readings** across 8 stations from 364
files, 2,250 duplicate timestamps absorbed, 259,463 rejected cells, 11 recovered
notes, **40,393 out-of-range channel values** (0.9.0: 3,695; 0.8: 631,252).

The rise from 3,695 is deliberate and is almost entirely two channels:
`aisvn.solar2_v` (0–15 V) and `aisvn.lipo_v` (0–5 V) each flag 14,107 readings,
which are one exact value repeated — 19.5 V and 6.84 V, on 2020-07-16 →
2020-08-05. Everything else is 12,179, or 1.7% of the archive. Both carry a
`band_note` saying the fire *is* the finding, and
`tests/test_catalog.py::TestBandsAreQuietOrExplained` pins both by name so a
third band firing at 18% cannot be added silently. A band wide enough to be quiet
about a plateau is a band that has been widened to lie.

### Running a from-scratch rebuild locally

```bash
python -m etl fresh        # delete the database and its -wal/-shm, then all, then verify
```

`make fresh` is the same thing. The deletion is the point: a database is a
gitignored artefact some earlier run left behind, and the `-wal` sidecar matters
as much as the database because SQLite replays a surviving write-ahead log on the
next open — deleting only the `.db` can hand back rows from the previous run.

### When the numbers *should* move

Some changes legitimately alter the output — a corrected timezone, a new
station, a fixed band. Re-record the baseline deliberately, with a reason, so the
diff appears in the pull request as an explicit number rather than a silent
rewrite:

```bash
python -m etl verify --update-baseline --reason "widened aisvn.lipo_v to its 2S ceiling"
```

Never "fix" a red build by loosening `data/baseline.json` by hand. The baseline
is the record of what the data is, and CI failing is the point.

## Continuous integration

Five workflows. `ci.yml` is the required gate; `data.yml` and `data_fresh.yml`
see the archive; `pages.yml` deploys; `release.yml` publishes the database.

| Workflow | When | What it does |
|---|---|---|
| `ci.yml` | every push and pull request | `ruff`, `pytest`, both frontend checks, `npm run build` |
| `data.yml` | when `data/raw/**`, `etl/**`, `tests/**`, `data/baseline.json` or `requirements.txt` change; plus Mondays 03:17 UTC and on demand | `etl all` then `verify` |
| `data_fresh.yml` | `workflow_dispatch` with a required reason, plus Mondays 04:23 UTC | **Deletes the database first**, then `etl all` and `verify`. Reports whether the rebuild moved anything. |
| `pages.yml` | push to `main`, or manually | `vite build` and deploy; asserts `dist/data` has the three JSON files and all 32 CSVs |
| `release.yml` | `v*` tag, or manually from `main` | `etl all`, `verify`, `release_notes.py --check`, VACUUM, gzip, attach to a Release |

**`ci.yml`** is deliberately *not* path-gated: a docs-only change must still show
a CI run. A path-gated required check produces no run at all when the paths do not
match, and a pull request then waits forever for a status that never arrives.

**`data.yml`** is path-gated for the opposite reason: a pull request that touches
only `src/` cannot change the data, and rebuilding proves nothing. The weekly
schedule is the backstop — a path filter only sees the paths GitHub reports, so
anything it misses is caught on Monday.

**`data_fresh.yml`** exists because "the database is an artefact some earlier run
left behind" is a real failure mode. It removes `solardata.db` *and its `-wal`
and `-shm` sidecars* — a surviving WAL makes SQLite replay the previous run's
rows — so the build starts from nothing. Reach for it after changing
`etl/catalog.py`, when `data.yml` has been red and you need to know whether the
drift is in the build or in the archive, or for a clean release database. It does
not commit; a human reads the summary and decides.

`tests/test_guards.py` asserts the shape of all of this: that `data_fresh.yml`
deletes the database, that `ci.yml` does not build the data, that `data.yml` is
gated on the right paths and has a schedule, that any workflow calling `verify`
runs every stage the baseline depends on, and that `release.yml` gets its notes
from `scripts/release_notes.py` rather than from its own `awk`.

### Why the test suite is fast, and why the archive is not in it

167 tests over real XLSX fixtures, slowest 0.07 s. 0.8 had 147 and one of them
read all 364 raw files, which took about fifty seconds and stalled the run at
test 40 — a suite you stop waiting for is a suite you stop running.

The checks that need the archive are in `etl/audit.py` and run in the build, where
a minute of reading is a minute of CI. The unit tests use fixtures written with
openpyxl, because that is the only way to catch a reader that has stopped
understanding the file format — and each fixture is removed in `tearDown`, which
0.8's 39 ingest tests never did.

### Why `solardata.db` is not committed

It is 104 MiB as the ingest leaves it (~85 MiB after `VACUUM`), over GitHub's
100 MiB per-file limit for a git blob, so a commit of it would be rejected
outright. `release.yml` gzips it to a Release asset.

0.8's was 182 MiB. The difference is the eight station tables holding the columns
each station actually has, plus `WITHOUT ROWID` on a TEXT instant, which stores
the rows in key order and removes the need for a separate index.

What *is* committed:

| Path | Size | Why |
|---|---|---|
| `public/data/` | 3.3 MiB | The rollups the site fetches, so GitHub Pages works from a clone |
| `data/processed/quality_report.md` / `.json` | ~85 KB | The review artefact, readable in a pull request |
| `data/baseline.json` | ~1 KB | The expected counts CI enforces |
| `data/raw/**` | 30.4 MiB | The primary source of truth |

`solardata.db` and the retired `data/exports/` are gitignored. Rebuild locally
with `make build`, or download the database from a Release.

0.9 removed the committed `data/processed/parquet/` tree, the Parquet stage and
`scripts/parquet_manifest.py`. If you go looking for the next win: the export is
now per station, so the rollup tables carry a lot of NULL for stations with few
channels, and the database could be smaller still. It is 104 MiB and nobody has
complained.

## The website

Plain JSX, no TypeScript, no state library, no chart library. Data flows one
way: `python -m etl export` writes `public/data/`, `src/data.js` fetches it, and
the components render it. There is no build step between the CSV and the DOM.

Five rules the frontend inherits from the pipeline, and the reason for each:

- **A gap is a gap.** An empty cell in a CSV reaches the chart as `null` and
  breaks the line. If you ever coerce it to 0 — even "just for the chart" —
  every sensor outage becomes a measurement, and the chart will look correct.
  `check_frontend.mjs` has a check by name.
- **A station's channels come from the station, not from discovery.** There is no
  global column list and no `VALUE_COLUMNS` map, so there is nothing to keep in
  step with the exporter. `channelsFor(station)` reads `stations.json`; the CSV
  header is intersected with it. 0.8's `discoverChannels` scanned a hardcoded
  30-column map for non-NULL values, which meant `aisvn2` logging `solar3_v` had
  nowhere to appear and selecting AISVN #2 offered nothing that worked.
- **A value is already in the unit it is displayed in.** The pipeline applies the
  confirmed scale at ingest, so there is no `divisor`, no stored unit and no
  display unit. 0.8 had all three, and a missing divisor in one call site printed
  a 28 °C afternoon at 280 °C with no error anywhere.
- **A flagged value is marked, never dropped.** A value outside its channel's
  band is drawn, ringed, counted and listed under the chart, and the bands come
  from `stations.json` — the same declaration the ingest applied to the raw cell.
  Do not reintroduce a heuristic here: a median/MAD "spike" test used to drop 16
  real days of `aisvn` 2020 out of 101, because a panel's 24-hour mean is
  dominated by night and one afternoon sample looks like an outlier against it.
  `check_frontend.mjs` has a regression check by name.
- **A prop name is a contract.** It is the one thing in the frontend that no check
  could see, and it has shipped three times as a white page rather than a failed
  assertion. `npm run check:render` exists for it.

`node scripts/check_frontend.mjs` guards the first four, over the real committed
exports, and runs in CI. `npm run check:render` renders the component tree —
`check_frontend` exercises the pure helpers in `src/data.js` and the CSV files,
and `vite build` cannot check a prop name because this is plain JSX with no types.
It asserts the app mounts, that `TimeControls` accepts the props
`StationExplorer` passes it, that every station's offered channels are exactly
its CSV's channels, and that every excluded channel carries a reason. It runs in
`ci.yml` and `pages.yml`, and `npm run build` depends on it.

### Deploying to GitHub Pages

`.github/workflows/pages.yml` builds `dist/` and publishes it on every push to
`main`. Two things are easy to get wrong:

- **Pages must be enabled once in the repository settings**, which no workflow
  file can do for you: *Settings → Pages → Build and deployment → Source →
  "GitHub Actions"*. Until that is set, every request returns
  `404 There isn't a GitHub Pages site here` no matter what the workflow does.
- **`base` in `vite.config.js` is `/solardata/`**, the project-pages path for a
  repository named `solardata`. Renaming either requires changing `base` too, or
  every asset and data fetch 404s.

The data files are committed under `public/data/`, so a frontend-only change
deploys without re-running the Python pipeline. The deploy asserts that
`dist/data/` contains `stations.json`, `metrics.json`, `quality.json` and all 32
CSVs, so a build that loses them fails instead of publishing a site full of
errors.

The site opens on one specific view — `DEFAULT_VIEW` in
`src/components/StationExplorer.jsx`: AISVN #1, November 2021, hourly, with
`battery_v`, `solar_v` and `temp_c`. It is applied **once**, on the first rollup
that loads, and the range it sets is the month's own data bounds rather than
hardcoded dates. Three things about it are easy to break:

- The default is a *claim about the archive*. `scripts/check_render.mjs`
  resolves `openingView()` and `defaultSelection()` against the real
  `stations.json` and the real rollup, and fails if the station, year, month,
  resolution or any channel is not there. Mutation-test it before trusting it.
- The range reset is keyed on station and year, **not** on the effect running and
  **not** on the resolution. `ranges` arrives after the CSV and re-runs the
  effect; clearing on every run wipes the default. Keying on the resolution made
  the Hour button throw away the reader's From/To.
- **The controls and the data are a pair, and a mismatched pair must be
  unrepresentable.** `yearForPick()` returns the year a station publishes rather
  than reading `.year` off a string — the bug that set it to `undefined` for every
  station, aborted the loader, and left the previous station's rollup on screen
  under this station's name with *this* station's bands on the *previous*
  station's values, ringing every point. The loader now records which
  station/year/resolution a rollup belongs to, and a rollup that is not the
  current period is waited for, never drawn. `scripts/check_render.mjs` resolves
  `yearForPick()` against the real `stations.json`; mutation-test it.
- The channel selection is keyed on the **station**, not `station:resolution`. A
  resolution switch is a different sampling of the same days and must not change
  what is being measured. The set actually drawn is derived by intersecting with
  the rollup at render time, so a channel a year does not carry is not drawn
  without the choice being lost.
- `wind_v` is **not** one of the default channels and must not become one. It is
  now charted — the collector confirmed it as a power measurement in watts — so
  the reason it is not a *default* is that it is not what the default view is
  about, not that it is unmeasured.

## Open questions a human still has to answer

These are recorded, not solved. Do not quietly decide them in code. Each station
carries its own list in `etl/catalog.py` — asserted non-empty by
`tests/test_catalog.py` — and the full list with context is in
[`docs/roadmap.md`](docs/roadmap.md).

1. **`aisvn.battery_v` reaches 29.8 V in 2020 and 17.9 V in 2021** against a
   12 V lead-acid pack the collector confirmed. 1,717 readings, 2.2% of the
   channel. The band is right, so the readings are the question: a second pack, a
   mis-scaled input, or a band wrong for what is installed.
2. **`aisvn.lipo_v` and `maker-webhooks.lipo_v` are bimodal** — 6.84 V and
   0.735 V plateaus against a 1S cell's 2.5-4.35 V. The collector has since given
   the ceiling as 5 V, so both are banded there **and** annotated: the plateau
   fires on 18% of `aisvn.lipo_v`'s record, and that fire is the finding. Nothing
   records a recompile at the change.
3. **`aisvn2.current_a_chA` and `current_a_chB` step by roughly 200×** between
   2021-04 and 2021-10, pinning at exactly 1240, and it is not a clean factor.
   No recompile is recorded, so no scale is applied. The collector has given a
   ceiling of 500 for both, so both are banded **above only** — a floor at zero
   would flag 55% and 17% of the record respectively, for a sensor that is
   working. The unit is still unresolved.
4. **`aisvn-solar.solar_v` maxes at 3,532 mV.** A photovoltaic panel should
   reach 15-20 V open circuit. Either the input is not a panel or the station
   never saw a real panel voltage.
5. **`phumy2.power_w` is not a power measurement.** The hardware was never
   implemented: 416,083 of 416,088 readings are exactly 0 and the remaining five
   are 13,810-19,877 W — six distinct values in six years. It is in the database,
   unbanded, and not on the chart.
6. **`aisvn-solar.wind_v` is wired and it logs nothing.** It is exactly 0 for all
   13,788 of its readings, so it is hidden as `constant`. The other two stations'
   `wind_v` is now charted: the collector confirmed it as a power measurement in
   watts, so `aisvn` is 0-29.8 W and `maker-webhooks` 0-14.7 W, banded 0-50 W
   after their confirmed scales. What the *input* is connected to is still asked
   of the collector — a number that reads plausibly in watts is not the same
   answer as knowing what is on the other end of the wire.
7. **`aisvn-solar.load1_v` and `load2_v` are in an unestablished unit.** They
   record 0-1,598 and 0-3,026, which cannot be volts. Millivolts would make them
   plausible; nothing confirms it, so they are neither charted nor banded.
8. **`aisvn.load_v`'s 0 V state changes behaviour on 2020-07-10** and nothing
   recorded explains it. The rail's full scale is also unresolved, so the 0-20 V
   band is the collector's figure rather than a measured ceiling.
9. **`phumy2.solar2_v` is a divider output after a bridge and load were fitted**,
   stepping from ~5,000 mV to ~1,200 mV, so it is not a panel voltage and should
   not be charted as one. The bridge ratio is unknown.
10. **`maker-webhooks` resets its submission counter every 16 readings** — 526
    times in 8,535, 523 of them with no gap in sampling. A genuine reboot looks
    like that when the station keeps sampling, but a counter that moves that fast
    may be something else. Unexplained.
11. **`test/IFTTT_test (1).xlsx` is excluded on a stale reason.** The exclusion
    still holds on the overlap, but 0.8 described the file as an 11-column solar
    layout when the 0.8.0 raw repair left it as 4,121 rows of the same probe its
    neighbours carry.
12. **`aisvn` has no readings between 2020-10-25 and 2020-11-04**, and September
    2020 has 12 readings. The collector confirmed the collector was down and no
    data was lost in the export, so there is nothing to fix.
13. **`aisvn.current_a` does not track solar even after the +6.6 A correction.**
    The corrected curve varies by about 0.3 A where `power_w`'s corrected curve
    tracks the panel exactly (0 W at night, 34.5 W at noon), which is the evidence
    for the −0.25 sign. So one correction is a repair and the other is a patch:
    the current channel's sign is fixed and its signal is not restored. The
    collector's request for more insight at higher resolution is about this.

## Conventions

- Python 3.11+, `from __future__ import annotations`, full type hints.
- `ruff` for lint and format (`make fmt`), 100-column lines, double quotes.
- Docstrings explain *why*, especially where the archive forced a strange
  decision. A comment restating the code is noise; a comment recording which
  file and row motivated the code is the point.
- Tests use plain `unittest` assertions under `pytest`, and build real XLSX
  fixtures with openpyxl so they exercise the same path as production.
- The frontend is plain JSX with no TypeScript and no state library. Keep it
  that way until there is a reason not to.

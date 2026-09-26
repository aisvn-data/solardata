# Changelog

All notable changes to `solardata` are recorded here, including findings about
the raw archive. The format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- **`rejects.reason` was a sentence, on 220,074 rows.** Rule 2 says to keep it
  a stable category, and the archive is where ignoring that shows: the
  `NULL_WINDOWS` path stored the collector's ~300-character note as the reason on
  every cell it nulled. That is **80.6 MiB of one paragraph, repeated**, and it
  made `rejects` (96.1 MiB) as large as `readings` — in a database that is not
  committed and that nobody downloads whole.

  | | before | after |
  |---|---:|---:|
  | `rejects.reason` for those rows | 300 chars | `null_window`, 11 chars |
  | `rejects` table | 96.1 MiB | **15.8 MiB** |
  | `solardata.db`, VACUUMed | 329.2 MiB | **166.7 MiB** |
  | gzipped, the Release asset | 20.0 MiB | 18.7 MiB |

  **No data moved.** The counts are identical — 734,908 readings, 224,579
  rejected cells, 220,074 of them nulled in the same window — so
  `python -m etl verify` matches the baseline unchanged and no re-record was
  needed. What changed is where the sentence lives: once, in `config.py`, which
  is version-controlled and human-readable. `report.collect` republishes it to
  `quality.json` as `null_windows` and `bad_windows`, paired with the number of
  rows each explains, so the site can still explain every flagged cell — and now
  says so on a dedicated **Windows** tab, where previously a reader saw the
  category with no reasoning attached. The same fix applies to
  `pre-reinstall window; collector confirmed rows from here on are usable`,
  which is now `pre_reinstall`.

- **`null_window` rejects had an empty `column_name`.** The code derived it by
  iterating `row_flags` for `no_signal:` prefixes, but `row_flags` is a merged
  *string*, so the loop walked its characters and matched nothing. Every one of
  the 220,074 rows said nothing about which channel it was about. `_null_windows`
  now returns the columns it nulled. Worth 1.6 MiB and, more to the point, the
  window's `n_rejected` in the report was silently 0.

### Added

- A **Windows** tab in the data-quality inspector, rendering each configured
  window once: station, channels, span, row count, and the reasoning in full.
  `rejects.by_reason` also gained a Meaning column, so a category is never a
  shrug.
- `check_frontend.mjs` asserts the invariant by name — *a reject reason is a
  category, never a sentence* — checking that every `reason` is at most 40
  characters, that `null_window` is present as a groupable category, and that
  the window counts add up to the reject total. It fails the build if the prose
  ever creeps back.

### Changed

- The size claims in `AGENTS.md`, `README.md`, `docs/data-dictionary.md` and
  `docs/format-design.md` are updated to the measured figures. `CHANGELOG.md` is
  deliberately not: its older numbers are records of releases where they were
  true, and rewriting a changelog to match today's build is how a changelog
  stops being a changelog.

## [0.7.1] — 2026-09-26

A patch, and the interesting part is the bug. The reading of the archive is
unchanged again: `python -m etl verify` reports the same 734,908 readings across
364 files.

### Fixed

- **Every metric checkbox was disabled, for every station.** 0.7.0 changed
  `availableMetrics()` to pass the metric list straight through to
  `TimeControls`, but it returns metric *objects* while the picker tests
  `metrics.includes(metric.key)` — a string. So `available` was false for every
  metric and all five rendered `disabled`.

  It was silent in the worst way: no error, no empty chart, and the default two
  channels still drew from the selection the load effect had already stored, so
  the chart looked correct while offering no way to change it. The two shapes are
  interchangeable at a glance, which is the actual defect — the function is now
  `availableMetricKeys()` and returns keys as its only form, so there is nothing
  to mismatch. `check_frontend.mjs` has a regression check by name that walks
  every published station-year and asserts each key is a string and selectable,
  and pins `solar-2020-05` as the one station whose disabled picker is correct.

### Added

- **A month selector.** Sits between Year and From, and offers only the months
  that actually have data — `aisvn` 2020 gets seven options, not thirteen. It is
  derived from the range rather than stored separately: it shows a month only
  when From and To are exactly that month's bounds, so the two controls cannot
  disagree. Choosing one moves the date inputs to that month; editing a date
  drops it back to "All". The bounds come from the data rather than the calendar,
  so a partly-reported month is not padded with empty days.

### Changed

- **The page is wider** — 1120px to 1320px, via a `--page-width` custom property
  so the header and body cannot drift apart again. Seven stat tiles need about
  900px and the explorer now has roughly 980px once the station rail and gutters
  are taken out, so the six value tiles and the station tile sit on one row
  instead of the station name wrapping below them. Below about 1240px the grid
  reflows to two rows rather than squeezing the uppercase labels, and a label
  now wraps rather than overflowing into its neighbour. Prose is capped
  separately (`.hero-copy`, `.prose`), so a wider page does not stretch a
  paragraph to an unreadable line length.

## [0.7.0] — 2026-09-26

A reading of the archive does not change in this release: `python -m etl verify`
reports the same 734,908 readings, 364 files, 4,399 duplicate timestamps, 10
recovered notes and 11 unconfirmed regimes. What changes is what the site shows
and how it decides what to distrust.

### Added

- **The site can plot the hourly rollup.** `readings_hourly` was already built,
  exported behind a flag and never read; the browser could only fetch
  `readings_daily`. Both are now published by default and the explorer has a
  **Day / Hour** switch, so a solar curve has a dawn and a dusk instead of being
  a flat 24-hour mean. That is 13 more station-years and 24,210 rows for 1.7 MB
  of CSV; `public/data/` goes from 167 KB to 1.9 MiB.

  The two are kept consistent by assertion rather than by convention:
  `check_frontend.mjs` now checks that every day and every sample count in a
  daily file is also in its hourly sibling, and pins both inventories (13 files
  / 1,124 daily rows, 13 files / 24,210 hourly rows).

  Hour is as fine as the site goes. The native cadence is 119 s — 734,908
  readings, which is the Parquet export and not a file a browser fetches. There
  is deliberately no `raw` granularity; `config.export_granularity` documented
  one and nothing read it.

- **`public/data/metrics.json`**, the plausibility bands copied verbatim from
  `etl/normalize/metrics.py`, so the browser applies the same criterion the
  ingest applied to each raw cell. A band corrected in Python now reaches the
  site on the next export instead of drifting against a second copy in
  JavaScript. Channels with no band are listed with null bounds, so the UI can
  answer "never flagged" rather than infer it from a missing key.

- Every flagged value in the current range is **listed** under the chart, with
  its channel, value, recorded band and sample count. "Marked, never dropped"
  was a claim a reader had to take on trust; now it is a table.

### Fixed

- **The outlier filter was removing real data, and its answer depended on which
  metrics were ticked.** The chart dropped any day where every *selected* metric
  was a "spike" against that channel's own median/MAD. Two things were wrong
  with that test. It was measuring sampling coverage rather than plausibility: a
  panel's 24-hour mean is dominated by night, so a fully covered day averages
  6–9 V while a single afternoon sample averages 17–19 V, and the test read
  that as 8 real days being spikes. And because the test was per-metric, the
  same day was filtered under one selection and drawn under another.

  Measured on `aisvn` 2020 with the default solar + battery selection:

  | | before | after |
  |---|---:|---:|
  | days dropped from the chart | **17 of 101** | **0** |
  | of those, real measurements | **16** | 0 |
  | days dropped when a third metric is ticked | 0 → test pattern drawn | 0 |

  The 16 were five single-afternoon-sample days at 17.7–19.1 V, four
  post-reinstall days at 28.9–29.6 V with 353–701 samples each, and the rest of
  the same shape. They are still shown; they are just not called artefacts.

  Replaced by a band test on the value actually being plotted. `aisvn` 2020 now
  flags 4 days on solar (three unconverted-millivolt commissioning days and the
  2020-10-01 ADC test pattern) and 23 on battery, drawn, ringed and counted.
  The battery count is worth stating rather than hiding: the recorded band is
  9–16 V for a 3S LiPo and `aisvn` reads 17.6–29.6 V on those days, which is a
  question about the hardware, not something the site should resolve by deleting
  the days.

- **The `out_of_range` count was fetched and thrown away.** `n_out_of_range`
  ships in every rollup and was parsed by the frontend, then never read. It now
  appears in the hover readout and the flagged table.

- **The daily and hourly rollups disagree about what "Battery" means.**
  `readings_hourly` has `battery_v_avg` and `readings_daily` does not, because a
  mean of minima is not a useful number — so the daily column is the day's
  *lowest* battery voltage and the hourly column is the hour's mean. Both were
  rendered under one label. The statistic is now named wherever a value is
  shown.

- **Metric selection was keyed by station, not by station-year.** Switching
  `phumy2` from 2022 to 2023 silently narrowed the selection to the one channel
  2023 has, and switching back did not restore it, because the surviving
  selection was non-empty. It is now keyed by station *and* resolution, which is
  the granularity the selection was actually derived from.

- `QualityInspector`'s flag dictionary was a flat table, so the two
  column-parameterised families — `bad_window:<column>` and `no_signal:<column>`,
  226,372 rows between them — rendered as a bare dash. It now resolves the
  prefix. `clip`, `non_monotonic` and `free_text` are labelled for what they
  actually are: declared in `etl/config.py`, never assigned.

- `docs/data-dictionary.md` listed `clip`, `non_monotonic` and `free_text` as
  live flags, omitted the two parameterised families, and pointed at
  `data/exports/` — a gitignored directory nothing reads — instead of
  `public/data/`.

### Changed

- `--hourly` is replaced by `--granularity {both,hour,day}`. Both rollups are
  written by default, so the flag now narrows the run rather than widening it.
  `config.export_granularity` moves from `"hour"` to `"both"` and stops being
  dead.

## [0.6.1] — 2026-09-26

### Changed

- **CI is split by cost.** `ci.yml` keeps the fast checks and now runs on every
  push and pull request in about a minute; the expensive full build over all 364
  raw files moves to a new `data.yml`, gated on the paths it actually reads
  (`data/raw/**`, `etl/**`, `data/baseline.json`, `requirements.txt` and its own
  definition), plus a weekly sweep on Mondays 03:17 UTC and `workflow_dispatch`.
  A pull request that only touches `src/` cannot change a reading — the data
  comes from `data/raw` through `etl/`, and both are committed — so rebuilding it
  for such a change proved nothing and cost several minutes.
- The data workflow is **deliberately not a required status check**: a
  path-gated workflow produces no run at all when the paths do not match, and a
  required check that never appears leaves a pull request waiting for a status
  that will never arrive. `ci.yml` is the required gate, and it is not itself
  path-gated, so a docs-only change still shows a run.
- The weekly schedule is the backstop for anything the path filter misses. A
  filter only sees the paths GitHub reports.

### Fixed

- **Every raw sheet was being parsed three times.** The ingest reads each file to
  scan its structure, again in `detect_block`, and again in `iter_cells`.
  Profiling 364 files showed **1,820 calls to `_read_rows` for 364 files**, and
  reading a sheet is 86% of the whole build. `etl/readers/xlsx.py` now caches the
  parsed sheet keyed by path *and* file size — the size so a file edited mid-run
  cannot be served stale — and `etl/build_db.py` clears the cache between archive
  folders to bound memory to one folder.

  | | before | after |
  |---|---:|---:|
  | `etl ingest` | 168.9 s | **53.7 s** |
  | `python -m etl all` | ~170 s | **71.7 s** |

  Identical output: 734,908 readings, 4,399 duplicates, 224,579 rejected.

- `tests/test_workflows.py` gained five checks covering the split: that
  `data.yml` is gated on the right paths, that push and pull_request agree, that
  `src/**` is *not* in the gate, that a schedule and a manual trigger exist, that
  `ci.yml` is ungated and does not depend on the build, and that no cheap check
  sits behind the expensive one.
- `tests/test_ingest.py::TestReadCache` covers the cache: one parse per sheet,
  dropped on request, and not served stale when a file changes size.

123 tests, 22 frontend checks.

---

## [0.6.0] — 2026-09-26

Applies the collector-confirmed unit corrections to the published aggregates, so
the charts are in volts instead of millivolts.

### Changed

- **The rollups are now unit-corrected.** `readings` stays raw and is never
  modified — it is the canonical record, and rewriting it would lose the ability
  to disagree with a correction. The hourly and daily rollups, which are what
  the website reads, now have the `status = 'confirmed'` scales applied. Before
  this, `aisvn-solar`'s solar axis read **4570** instead of 4.6 V and `phumy2`
  read **1384**; both now peak at 2.9 V and 3.4 V, which is right for a small
  panel and a small array respectively.
- **Unconfirmed regimes are still never applied.** 11 windows remain
  unconfirmed, all of them `aisvn`, and `aisvn`'s chart therefore still plots raw
  values. That is the rule working, not an oversight: those need the firmware.
- **`aggregate` is a separate stage**, between `regimes` and `parquet`. Applying
  a scale needs the `regimes` table, and rolling up inside the ingest meant the
  daily table was built before the detector ran — on a clean build that is
  silently unscaled. `test_ingest_alone_does_not_build_rollups` locks the
  ordering down.
- **Confirmed regimes are windowed to the channel's extent, not to the
  detector's proposal.** The collector's statement is that these channels log
  millivolts *as integers* for their whole record, so the window comes from the
  data. `phumy2.solar2_v` was previously scaled for its first seven months and
  raw for the following three years.
- **`valid_to` is now the exclusive end.** Writing the last day's own date
  excluded it from the half-open window, which is how `aisvn-solar` kept a raw
  601 V on 2020-06-12 and `phumy2` a raw 1384 V on 2024-02-01.
- **Audit columns.** `readings_hourly` and `readings_daily` gained
  `scaled_channels` and `regime_ids`, and both are exported, so a reader can
  tell a converted value from a raw one without re-deriving it. The frontend
  states the conversion in a note when a station's values have been converted.
- `n_out_of_range` reaches the rollups, so the chart can distinguish an
  implausible day from a merely empty one.

### Fixed

- `readings_hourly` had no `battery_v_max`, so the **daily battery maximum was
  being taken from the hourly minimum** — wrong data, quietly, since the first
  release.
- The confirmed-regime map could name a rollup column that does not exist
  (`battery_v_avg` is hourly-only), and the channel map had `load_v` as a bare
  string rather than a tuple, so it iterated character by character and produced
  `SET l = l * ?`. Both now go through the table's actual column list.

### Build baseline

| | before | after |
|---|---:|---:|
| Readings | 734,908 | 734,908 |
| Unconfirmed regimes | 14 | **11** |
| Hourly / daily buckets | 25,649 / 1,194 | 25,649 / 1,194 |

The three promoted windows are detector proposals for channels the collector then
confirmed — `aisvn-solar.solar_v`, `aisvn-solar.lipo_v`, `aisvn2.battery2_v` —
whose proposal windows are now replaced by confirmed ones spanning the channel.
Readings and bucket counts are unchanged: this release corrects units, not data.

115 Python tests, 22 frontend checks.

---

## [0.5.0] — 2026-09-26

Data corrections, all confirmed by the collector. Baseline re-recorded: **735,004
→ 734,908 readings**.

### Added

- **Metric selection is now remembered per station, and spike days are excluded
  from the chart.** Three separate defects made the explorer look broken:
  - *Selection looked predetermined.* Switching stations fell back to "the first
    two channels this station has" whenever the previous selection did not
    survive, so phumy2 always opened on power + temperature. The selection is
    now keyed by station and restored on return.
  - *phumy2 and aisvn2 had no solar voltage at all.* They log `solar2` and
    `solar3` + `battery2`, and the daily/hourly rollups only aggregated
    `solar_v` / `battery_v`. 308 of 636 phumy2 days and 250 of 250 aisvn2 days
    now carry a solar channel, and the frontend resolves the numbered variant
    as the same metric.
  - *Implausible days were drawn as if they were data.* `aisvn` 2020-10-01 is a
    single ADC test-pattern reading (solar 123, battery 456, current 789)
    between days reading 18 and 19. The rollups now carry `n_out_of_range`, and
    the chart drops days whose values are spikes against that channel's own
    median, stating how many it left out.

- **Spike detection is deliberately scale-blind and declines to guess.** A first
  attempt keyed the filter on the absolute plausibility band, which flagged
  *every* millivolt reading of phumy2 and dropped 636 of 636 days. The filter is
  now relative to the series: median and MAD rather than mean and deviation,
  because one 456 V reading is exactly what drags a mean away from the value you
  want to compare against. When a channel's scatter exceeds 20% of its own level
  the detector returns no limit at all — `phumy2.solar2_v` steps from ~5000 mV to
  ~1200 mV when a bridge is fitted, and over a window holding both halves the
  distribution is bimodal, so either mode looks like a spike against the median
  of the other. Erasing that would delete a real configuration change.
- 7 new checks in `scripts/check_frontend.mjs` covering the spike logic, the
  second solar channel, and the new column. 19 frontend checks, 114 Python tests.
- **GitHub Pages deployment.** `.github/workflows/pages.yml` builds `dist/` and
  publishes it on every push to `main`, so a frontend-only change ships without
  re-running the Python pipeline. The build output was already correct for
  project pages — `base` is `/solardata/` and all 15 data files land in
  `dist/data/` — but nothing deployed it, and Pages answered every request with
  `404 There isn't a GitHub Pages site here` because no site had been created
  for the repository.
  - Pages must be enabled once in the repository settings (*Settings → Pages →
    Build and deployment → Source → GitHub Actions*); no workflow file can do
    that for you.
  - The deploy asserts `dist/data/` contains `stations.json`, `quality.json` and
    all 13 CSVs, so a build that loses them fails instead of publishing a site
    full of errors.
  - `concurrency` cancels an in-flight deploy when a newer commit lands, so the
    published site never moves backwards.
- `tests/test_workflows.py` — 9 checks over the workflow files, in CI. These
  validate the *Actions schema*, not just YAML syntax: `yaml.safe_load` accepts
  any well-formed mapping, so `environment:` sat at the workflow level and
  Actions rejected the whole file with `Unexpected value 'environment'`, while
  every local check passed. The test asserts the allowed top-level, job and step
  keys, that `needs` points at real jobs, that `environment` is on the deploy
  job, and that no workflow is back on a deprecated Node 20 action.
- Action versions moved off the deprecated Node 20 runtimes: `checkout@v5`,
  `setup-node@v5`, `setup-python@v6`, `cache@v5`, `upload-artifact@v5`, plus
  `configure-pages@v5`, `upload-pages-artifact@v4` and `deploy-pages@v4` for the
  new deploy. CI runs Python 3.13 and Node 22, matching the local toolchain.

### Fixed

- **`pyyaml` was used by the tests but never declared.** It worked locally only
  because an unrelated earlier task happened to install it, and CI failed at
  *collection* with `ModuleNotFoundError: No module named 'yaml'` — which aborts
  the entire pytest run rather than skipping one file, so every other test result
  was lost as well. Now declared in `requirements.txt`, and
  `tests/test_workflows.py` imports it inside a `try`/`except` that skips at
  module level so a minimal environment degrades instead of dying.
- `tests/test_workflows.py` gained a check that walks the imports across the
  whole test suite and asserts every third-party module is declared in
  `requirements.txt`. A new import now fails locally instead of reaching CI.
  Verified by removing `pyyaml` from the requirements: the suite fails with
  *test_workflows.py imports 'yaml' but it is not in requirements.txt*.
- **`release.yml` failed at the baseline check.** It ran only `etl ingest`, which
  rebuilds the database but does **not** populate `regimes` — that is a separate
  stage. The table came out empty and the guard correctly reported
  `unconfirmed_regimes: 14 -> 0`. The build now runs `ingest`, `regimes` and
  `report`; `report` was also missing even though the quality report is uploaded
  as a release asset, so the asset was being copied from the commit rather than
  regenerated. This is the baseline guard working as intended: it caught a
  workflow that was quietly producing an incomplete database.
- **Node 20 deprecation warning in the release path.**
  `softprops/action-gh-release@v2` still runs on the Node 20 runtime. The upload
  now uses the `gh` CLI that ships with the runner, which removes both the
  warning and the third-party dependency. `release.yml` now uses only
  `actions/checkout@v5` and `actions/setup-python@v6`, both on Node 24.
- `tests/test_workflows.py` gained two checks after the above:
  - A workflow that calls `etl verify` must first run every stage the baseline
    depends on, or run `etl all` which covers them. Verified by reintroducing
    the missing stages: the suite fails with *release.yml runs `etl verify` but
    never `etl regimes`*.
  - `release.yml` may use only first-party actions, so a third-party action on a
    deprecated runtime cannot come back unnoticed.
- **A schema-donor bug mislabelled 45,986 readings — 59% of the `aisvn`
  station.** On 2020-06-17 the applet gained a `power` column, going from 10
  columns to 11. Donor selection matched on date alone, so the 23 headerless
  11-column files (2)–(23) inherited the 10-column header from the preceding
  sibling. Applying a 10-column header to an 11-column row shifts every channel
  from index 4 onwards by one:

  | raw column | stored as | should be |
  |---|---|---|
  | `power` | `load` | `power` |
  | `load` | `wind` | `load` |
  | `wind` | **`temp`** | `wind` |
  | `temp` | `solar2` | `temp` |
  | `solar2` | `LiPo` | `solar2` |
  | `LiPo` | `boot` | `LiPo` |
  | `boot` | *(dropped)* | `boot` |

  Donor selection now requires an exact column-count match, preferring the
  nearest preceding sibling and falling back to the earliest following one.
  `tests/test_ingest.py::TestDonorWidthMatching` locks this down.

- **A correction to a correction.** 0.4.0 reported an "`aisvn` temperature
  fault, 100% implausible June–September 2020". That was **wrong**, and so was
  the reasoning behind it. The temperature channel was not faulty — it was
  receiving the `wind` channel, which reads 0.0 when there is no wind. After the
  alignment fix, `aisvn.temp_c` is:

  | month | n | median | implausible |
  |---|---:|---:|---:|
  | 2020-06 | 10,545 | 33.7 °C | 19% |
  | 2020-07 | 10,411 | 32.1 °C | **0%** |
  | 2020-08 | 13,283 | 31.9 °C | **0%** |
  | 2020-11 → 2022-02 | 26,665 | 32–34 °C | **0%** |

  The earlier claim also had its diagnosis inverted: October 2020 was never the
  broken window, it was when the collector had already been fixed. The margin
  notes in the archive (*"Pin 4 is temperature - calibrated ..."*,
  *"Installed in the dark, let's start again!"*) describe the repair, not the
  fault.

- **New sentinel: `342.1`.** 14,107 readings in `aisvn.temp_c` sit at exactly
  342.1 and 7 at 342.0 — a saturating float32 conversion, not a temperature.
  Now stored as NULL with `quality_flags = 'sentinel'`. The sentinel table is
  keyed by float rather than by string, because the XLSX reader normalises
  integral floats to their integer spelling: a cell holding 342.0 arrives as
  the text `"342"`, which a string-keyed table misses silently.

- **`aisvn (25).xlsx`, first 100 rows excluded.** The collector confirmed the
  pre-reinstall window is unusable. Measured transition: `battery` steps from
  −0.99 to 12.84 V at sheet row 112, but rows 102–111 are the powered-down state
  (solar 0, load 0), so the earlier boundary is used and those 10 extra rows
  are harmless.

### Changed

- **`phumy2.solar2_v` and `phumy2.lipo2_v` promoted to `confirmed` at ×0.001.**
  The collector confirms `solar2_v` is millivolts from a small ~5 V panel, and
  `lipo2_v` reads 1980–4196 throughout, which is mV of a 3S pack. Note that the
  *level* still moves when a bridge and load were fitted — roughly 5000 mV
  before, ~1200 mV after — so the stored millivolt value is a divider output,
  not always the panel voltage. Recovering true panel voltage needs the bridge
  ratio, which is not in the archive.
- **New `NULL_WINDOWS` mechanism, and the first window in it.** Distinct from
  `BAD_WINDOWS`, which flags but keeps: a window where the stored number makes
  an *affirmatively false* claim is nulled and every affected cell written to
  `rejects`.
  - `phumy2.solar2_v`, 2022-10 → 2023-12: the channel reads 0.0 V at **every
    hour of the day** for the whole of 2023, including noon. A working panel is
    100% zero from 18:00 to 05:00 and 2% at midday — that is exactly the
    profile `solar2_v` shows in 2020. Zero all day is a disconnected input, not a
    dark panel, and 169,789 readings charting as a flat line at zero would be a
    wrong answer rather than an ugly one. The channel recovers in 2024-01.
  - 14 regimes remain unconfirmed, down from 20.
- **Timezone is now a fact, not an assumption.** Confirmed as `Asia/Ho_Chi_Minh`
  (UTC+07:00) by the diurnal temperature cycle: the daily minimum lands at 05:00
  local for both `phumy2` (415k rows) and `aisvn`, which is sunrise in Ho Chi
  City. An offset wrong by 5 or 7 hours would put that minimum at 22:00 or
  midnight. `TestTimezoneIsHoChiMinh` asserts it.
- **2020-06-15 → 2020-06-17 15:20 local flagged as a bad window** for
  `aisvn.temp_c`. Not scattered read errors: the channel reports **exactly
  200.0 for every one of its first 1,359 readings**, then `342.1` for a 4-hour
  block, and only becomes real after 2020-06-17 15:20 local — the same moment
  the applet changed its column layout. 90% of all out-of-range temperatures on
  this station fall in this window, so it is a commissioning artefact rather
  than noise.
- **2020-10-23 → 2020-10-30 flagged as a bad window** for `aisvn`
  `solar_v`/`battery_v`/`temp_c`. Values are kept and flagged, not nulled.
- **`package.json` version synced to 0.5.0** and its dependencies pinned to
  exact versions. `TestVersionConsistency` now asserts that `package.json`,
  `pyproject.toml` and `etl.__version__` agree, that dependencies are not
  `latest`, and that the changelog documents the current version.
- `load_v` documented as a two-state load rail with a behaviour change: 28,192
  readings at exactly 0, last on 2020-10-30. Concentrated in June (80%), July
  (78%) and August (19%); from November 2020 the channel is 9.5–24.7 V and
  never 0.

### Build baseline

| | before | after |
|---|---:|---:|
| Readings | 735,004 | **734,908** |
| Duplicate timestamps | 4,403 | 4,399 |
| Malformed rejects | 6 | 220,180 |
| Unconfirmed regimes | 23 | **14** |
| Hourly / daily buckets | 25,662 / 1,197 | 25,649 / 1,194 |

The −96 readings are the 100 excluded `aisvn (25)` rows less 4 duplicate
timestamps they previously absorbed. The 220,180 malformed rejects are the 6
repeated header rows, the 100 excluded rows, and the 220,074 cells nulled by a
`NULL_WINDOW` — every one recorded rather than silently dropped or, worse,
silently kept as a false zero. 102 tests, 12 frontend checks.

---

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

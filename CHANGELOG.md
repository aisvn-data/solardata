# Changelog

All notable changes to `solardata` are recorded here, including findings about
the raw archive. The format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/).
## [Unreleased]

## [0.12.0] - 2026-09-29

### Fixed

- **Local wall clock time alignment (7-hour shift resolved)**:
  - Raw spreadsheets recorded local Vietnam wall clock time (`Asia/Ho_Chi_Minh`, UTC+7). When ingested, timestamps are normalized to UTC for database indexing and analytical queries.
  - The frontend previously rendered the UTC timestamp directly (`2020-06-15T05:00:00Z` -> `05:00`), causing solar noon to appear at 05:00 AM and sunrise at 23:00 PM.
  - Added `toLocalWallClock()` in `src/data.js` to project UTC timestamps to the station's local wall clock for display. Solar noon now centers at 12:00 PM, sunrise at 06:00 AM, and sunset at 18:00 PM across all chart axes, tooltips, and rollups.
- **Rules of Hooks React error #310**:
  - Moved `diurnalRows` memoization to the top level of `StationExplorer.jsx` before conditional early loading/error returns, ensuring React hook call counts remain identical across every render cycle.

### Added

- **Annual Data Quality Heatmap (`YearHeatmap`)**:
  - 52-week calendar grid embedded beneath the main time-series chart in Station Explorer, visualizing full-year collection and plausibility compliance.
  - Color-coded: Gray (outside station window), Black (no samples logged), Green (clean / in range), Orange (1 channel out of range), Red (>1 channel out of range).
  - Interactive hover tooltips and single-click date focusing.
- **24-Hour Diurnal Overlay Graph (`DiurnalChart`)**:
  - Superimposes 24-hour diurnal curves (00:00 to 24:00 local time) for all days in the active date range.
  - Independent Left (Y1) and Right (Y2) Y-axis autoscaling and channel selectors, enabling direct comparison of disparate units (e.g. 0-15 V voltage vs 0-50 W solar power).
  - Overlapping semi-transparent traces forming an intensity envelope, with an optional bold mean profile overlay.
- **Configuration Tab & Enhanced Editor**:
  - Renamed third navigation tab to "Configuration" (supporting routes `#configuration`, `#inspector`, and `#setup`).
  - Set "Pipeline Steps & Configuration Editor" as the primary default view on the left, with "Raw vs Curated Diff" on the right.
  - Automatic station date initialization using `station.first_ts_utc`.
  - Clear visual separation of "1. Normalization (Hardware Scale)" from "2. Curation (Plausibility Filter)".

## [0.11.1] - 2026-09-29

### Fixed

- **Call stack overflow on large raw telemetry datasets**:
  - Replaced array argument spreading in `Math.min(...dates)` / `Math.min(...values)` and `summarise` with iterative min/max loops. In V8 (Chrome, Edge, Node.js), spreading arrays with over 65,000–170,000 items exceeded call stack limits and crashed the UI to a blank white screen when selecting "Raw" resolution for `phumy2` (2020, 2021, 2022, 2023), `aisvn2` (2020), and `aisvn` (2020).
  - Optimized chart mouse hover targeting to use an \(O(\log N)\) binary search instead of an \(O(N)\) linear scan over 170,000 rows.
  - Capped flagged table display to 200 items with count indicator, preventing DOM freeze when rendering tens of thousands of breach rows.

### Added

- **Browser History & URL Hash Navigation**:
  - Synchronized station, year, resolution, and time range to the URL hash (`#explore?station=...&year=...&res=...`).
  - Added support for browser Back and Forward navigation via the `popstate` / `hashchange` API, allowing the user to seamlessly step back to previous views without losing their state or needing to reload from scratch.
  - Added an `<ErrorBoundary>` component with an in-place recovery card offering "← Go back to previous view" and "Reset to default" to catch any unexpected rendering errors gracefully.

### Documentation

- **Updated `docs/format-design.md` for 0.11**:
  - Completely rewritten to reflect the modern two-tier SQLite architecture (`solardata_raw.db` for verbatim unscaled telemetry and `solardata.db` for curated data).
  - Documented in-browser SQLite Wasm (`sql.js`) execution, CSV rollup exports, and the removal of the legacy Parquet stage.


### Added

- **Interactive Raw vs. Curated Diff Inspector**:
  - A dedicated "Pipeline Inspector" tab in the application enabling direct comparative analysis between raw telemetry and curated output.
  - Sub-tab 1 ("Raw vs Curated Diff") allows selecting station, date, and channel to query raw readings from in-browser SQLite Wasm (`solardata_raw.db`), showing verbatim values, applied normalization scale, hardware corrections, null-window masks, and plausibility band breach checks side-by-side with step badges.
  - Sub-tab 2 ("Pipeline Steps & Configuration Editor") visualizes normalization scales, hardware corrections, null windows, and plausibility bands per station and channel, with inline editing and instant export of modified `normalization.json` and `curation.json` artifacts.
- **In-Browser SQLite Wasm Engine for Native Resolution**:
  - Direct client-side SQL execution over `solardata_raw.db` using `sql.js` (WebAssembly).
  - Enables a "Raw" (1-2 min native resolution) view in Station Explorer alongside Daily and Hourly rollups.
  - Layout expanded by 150px to ensure time controls, quick range presets, and resolution selectors fit seamlessly.

### Changed — the raw archive, again

The collector has made a second round of repairs to the source XLSX. `data/raw/**`
is the primary source of truth and not a file to edit, so as in 0.8.0 this entry
records what each edit changed. Every previous version is in git history.

- **`aisvn/IFTTT_aisvn.xlsx` is now in volts throughout, with the majority column
  order.** `load` is 0–13.716 V with no millivolt readings left and the 195 real
  volt readings that 0.8.0's repair had overwritten with 0 restored, so
  `load_v` is one unit for the whole file. Its header had read `load, power` at
  indices 4 and 5 where `(8)` and `(24)`–`(27)` read `power, load`; files (1)–(7)
  are headerless, sit after this file, and were borrowing its order, so their two
  channels were stored swapped — roughly 14,000 readings of `load_v` reading up to
  70.6 and `power_w` up to 15.45, where the surrounding files have it the other
  way round. Now resolved, and `tests/test_ingest.py::TestHeadersInOneFolderAgree`
  fails if two files of one width ever disagree about a data column again.
  `metric_defs` is keyed on width, which is enough for a folder that *gained* a
  column and not enough for one that reorders two of them.
- **`aisvn2` gains three more repaired files** (`(1)`, `(11)` and the base sheet).
- **`phumy2b` adds a fourth file**, taking the station to 416,088 readings and the
  archive to 731,885, through **2026-09-27**. Coverage is no longer "four years".

### Added

- **A manual release is a first-class trigger, and it is guarded.** `release.yml`
  already had `workflow_dispatch`; what it lacked was anything stopping a run from
  a branch nobody merged. `workflow_dispatch` runs against whichever ref is
  selected in the Actions tab, and `gh release create` creates the tag at *that*
  commit, so a run from a feature branch publishes a database built from
  unmerged work under a tag. A `guard` job now refuses any ref other than
  `refs/heads/main` for a manual run, and the build `needs` it, so a refusal costs
  a second rather than three minutes of ingest and a 19 MiB upload. A tag push is
  unaffected: there the ref is the tag, which is the point of that trigger.
- **The repository allowlist accepts both origins.** `expected_repository` is now a
  comma-separated list defaulting to `aisvn-data/solardata,kreier/solardata`, so a
  manual run works in either repository with nothing typed and a run in a third
  still fails. Previously the check only worked if you remembered to edit the input
  for whichever repository you were in. Pass `*` to switch the check off.

- `aisvn-solar.battery_v` is a **50/50 voltage divider**, so the input is
  multiplied by 2 at ingest and stored and displayed in millivolts, with a
  per-station band of 0–5,100 mV. It was being read as volts and flagged out of
  range on every sample.
- `aisvn2.lipo2_v` is a **disconnected 2S LiPo pin in millivolts** (0–7,097 mV
  observed), with a per-station band of 0–8,000 mV. The column default describes a
  single cell, so the whole channel was flagged.
- `phumy2.current2_a` is confirmed **milliamps** and carries a ×0.001 regime. It
  was absent from `CHANNEL_COLUMNS` in `build_aggregate`, which meant a confirmed
  regime matched the lookup and was then dropped for want of a column to multiply —
  so the scale existed in the database and was never applied.

### Fixed

- **A header spelled as a canonical column name now maps to itself.** The
  collector's repair of `test/IFTTT_test (1)` and `(2)` renamed the probe's header
  row from `nix, temp, wifi` to `nix, temp_c, wifi_tx_ms`. `map_header` refused
  both spellings, so `test.temp_c` became NULL for **all 33,377 readings** and the
  published daily rollup had three empty columns where its temperature had been —
  a missing channel, which is the exact shape of "looks fine". A header that is
  already a canonical column name now maps to itself, placed *after* `_REFUSED` so
  a spelling that was refused for a stated reason cannot be reinstated by it.
- **An out-of-range count is counted in the unit the value is published in.**
  `_oor_exprs` tested the stored value and `_scale_rows` then multiplied the
  bucket by a confirmed regime, so the two disagreed for every scaled bucket:
  `phumy2` reported **all 416,088** of its `current2_a` samples out of range while
  publishing 0.155–2.0 A against a ±50 A band, and the site uses those counts to
  decide whether an aggregate is contaminated. The recount happens against the
  hourly table *before* the daily rows are derived from it, since the daily count
  is a sum of the hourly ones.
- `aisvn`'s seven millivolt regimes and the `temp_c` regime are now **scale 1.0**.
  The boundaries are still recorded — the archive still has them, and the evidence
  for them has not changed — but the collector converted the file, so applying the
  conversion again was dividing volts by a thousand. `aisvn.temp_c` is tenths
  throughout, corrected at ingest over the whole record rather than from the
  recompile onwards, which had left the first 1,480 readings in a different unit
  from the rest of their own file.

### Not changed, on purpose

- **Voltages stay float volts.** The collector's note that `aisvn`'s `load` "is a
  Volt value that needs to be converted to mV" is a statement about the *verbatim*
  layer, not this one. `readings` and both rollups keep every voltage as a float
  number of volts, because that is the column's documented unit, the one the
  plausibility bands are written in, and the one the site draws — and because the
  repair made the column uniformly volts, so there is nothing left to convert.
  Integer millivolts, with the divider ratio beside them, are a step for
  `solardata_raw.db`, which is not built yet. Doing it in `build_db` instead would
  be a unit decision in the wrong layer, and `aisvn-solar.battery_v` already shows
  the cost: a channel whose unit is not the column default has to be corrected at
  ingest *and* override its band *and* ship that override to the browser, in three
  places, because a band can only describe one unit. See
  [`docs/roadmap.md`](docs/roadmap.md#voltage-columns-float-volts-here-integer-millivolts-there).

### Known debt, recorded not fixed

- `n_out_of_range` on `readings` is still counted in the **stored** unit, so a
  confirmed millivolt or milliamp channel stays flagged on every reading while the
  rollup beside it reports none. Both numbers are kept and they describe different
  things — the reading as written, and the value as published — but the pair reads
  as a contradiction until this is written down somewhere the reader will see it.

## [0.10.1] - 2026-09-28

Four commits, one of which a reader can see. No reading, band, scale or count
changed: **731,885 readings, 364 files, `verify` matches the baseline, and
`out_of_range` is still 40,393.**

### Fixed

- **`test.wifi_raw` is published in the unit the collector named, not in `count`.**
  The collector renamed that column `wifi_tx_ms` in the 0.9.0 raw repair, where
  the header row went from `nix, temp, wifi` to `nix, temp_c, wifi_tx_ms`. The
  catalog mapped the new name to its internal `wifi_raw` and then described the
  channel as a "WiFi probe counter" with the unit `count`, so the site printed
  `count` beside a duration in milliseconds and the picker called a time a tally.
  The header was in the catalog the whole time, 1,600 lines above the channel, and
  nothing compared the two. Now `ms`, with `kind: duration` and the collector's
  own name quoted in the description, because the header is the only record of
  what the column measures.

  No band, deliberately: the collector gave the unit and no ceiling, and unlike a
  voltage, a current or a power reading there is no hardware limit a transmission
  time can be tested against. It depends on the protocol, the distance and the
  noise, and a band wide enough to hold 1,605–56,708 ms would be a band that
  flags nothing.
- **Two band notes stated a count that was not this build's.**
  `aisvn-solar.battery_v` said its 0–5.1 V band fires on 8 readings; it fires on
  7. The 8 was left over from before the shared rollup was fixed to key the band
  on the row's own station — at which point that channel was being counted with
  `aisvn`'s 9–16 V band and had no true count to state. `maker-webhooks.lipo_v`
  said "fires on 1,656 of 8,535", which is true of a band it no longer has; at
  0–4.35 V it fires on 4.

  Both are the failure this project exists to prevent: a number in prose,
  plausible, wrong, with nothing to notice it. `etl.audit` already refused a band
  firing above the threshold without a note; it had no way to check a note's
  arithmetic.
- **`aisvn-solar.battery_v`'s note now records why it used to report 100%.**

### Added

- **`etl.audit` gains `check_declared_units`.** Every channel's published unit is
  compared against the unit suffix in the collector's own column name, as the
  layout records it: `temp_c` → `degC`, `wifi_tx_ms` → `ms`. Over the whole
  archive that is two rows, and before this release one of them was wrong. It is
  a **failure** rather than a note, because a false positive is a bug in the
  catalog rather than noise to tolerate, and it reads only the catalog, so it
  needs no archive and also runs in the unit suite. Re-introducing
  `unit="count"` fails the build.
- **A band note's stated count must be the current one.** The rule is narrow
  enough to hold to: every `N of M` in a `band_note` is the count for this build.
  A historical figure is written differently, because the only unambiguous way
  to state a past count is not to use that shape. Checked across all ten notes
  that state a count, against the recorded build.
- **`docs/pipeline.md`**, generated from the catalog and the built database: every
  transformation between a raw cell and a charted point, and all 51 channels
  across 8 stations, with a command per stage. It is checked against
  `etl/catalog.py` so it cannot go quietly stale. The first version of that check
  substring-matched the table row and was decorative — a band of `0 .. 5.1` is a
  substring of the stored range `0 .. 5.148` in the same row, so a document
  claiming `0 .. 9.9` passed. Mutation testing found it; it compares cells now.
- A test that every kind a *published* channel uses has a `KIND_ORDER` entry in
  `src/data.js`, since an unknown kind sorts last and silently.

### Changed

- The three `data/raw/Voltage_phumy/*.xlsx` workbooks were re-saved from Excel,
  which rewrote each container and grew `Voltage_phumy.xlsx` by 70% without
  touching a cell. Verified: the exports are byte-identical, the per-file row,
  ingest, reject and duplicate counts are unchanged, 5,553 readings over
  2020-07-04 to 2020-07-12 as before, and the sha256 the build already recorded
  matches the bytes on disk. Only `source_files.sha256` changed, which is the
  provenance record doing its job.

### Not changed, on purpose

- No band was added to `test.wifi_raw`, and none should be until the collector
  gives a ceiling. A plausible-looking range for a transmission time would be
  invented rather than measured.
- The `v0.10.0` tag stays where it is, one commit behind `opencode`. It was cut
  before these four commits, and moving it would rewrite the ref a published
  release points at. 0.10.1 is a separate tag for a separate build.

| | 0.10.0 | 0.10.1 |
|---|---:|---:|
| readings | 731,885 | **731,885** |
| raw files | 364 | 364 |
| out-of-range channel values | 40,393 | **40,393** |
| hourly / daily buckets | 25,566 / 1,184 | 25,566 / 1,184 |
| unit tests | 171 | **174** |

## [0.10.0] - 2026-09-28

A band per **column** came back, in one place, and took the site with it. Two bugs,
one structural and one a single wrong character, both found the same way: a number
on screen that could not be true, checked against the archive instead of argued
about.

**`aisvn-solar.battery_v` was reported as 100% out of band. 7 of 13,788 readings
actually are.**

### Fixed

- **The shared rollup banded a channel by its column name, not by its station.**
  `readings_hourly` is one table fed by a `UNION ALL` of the eight station tables,
  so `<channel>_n_oor` needed one SQL expression for all eight branches, and
  `_banded_published()` deduplicated by channel *name* and took the first station's
  band for everyone. `aisvn` is declared first, so its 9–16 V band was applied to
  every station's `battery_v` — including `aisvn-solar`'s, which is stored in volts
  at 0–5.148 V after a confirmed ×0.002, and which therefore had **all 13,788 of
  its readings flagged**, 0.757–14.353 V `maker-webhooks` had 138, and
  `aisvn2` was untouched only because its column is `battery2_v`. The per-channel
  totals were all correct, which is why nothing caught it: the rollup was the only
  place the two disagreed.

  This is 0.8's bug -- a band per column name, tested against a value in the wrong
  unit -- rebuilt inside the rollup, and the union is exactly what made it possible.
  The test is now keyed on the row's own `station_id` via a `CASE`, so each station
  is counted with its own band; a station with no band for the channel falls to
  NULL and counts nothing, and a one-sided band tests only the side it declares.
  `aisvn-solar.battery_v` is back to **7**, which is what the raw cells say.
- **Picking a station loaded no data, and drew the previous station's bands over
  the previous station's values.** `years` is an array of plain strings, so
  `setYear(s.years[s.years.length - 1].year)` set the year to `undefined` for
  *every* station. That failed the loader's guard, the `UNION ALL` of the previous
  station's rollup stayed in state, and because a station's channels are its
  metadata intersected with the loaded file's header, selecting **AISVN Solar**
  tested **aisvn-solar's 0–5.1 V band** against **aisvn's 10.5–14.9 V November
  2021 battery**. Every point breached, and the caption said so in the confident
  voice of a finding: *"700 values in this range fall outside their channel's
  recorded band; the first 400 are ringed."* The 400 is `MAX_BREACH_MARKERS`, a
  drawing cap, working as designed. The 700, the month list, the year and the
  "no channel is selected" were all this one bug.

  Three fixes: the pick resolves through an exported pure `yearForPick()`; the
  loader records which station/year/resolution a rollup belongs to and a rollup
  that is not the current period is **waited for, never drawn**, so any future
  mismatch is a spinner rather than a wrong chart; and a failed load drops the key
  as well as showing the error. `check_render.mjs` gained a check for it, which is
  mutation-tested — re-introducing `.year` fails it.
- **A resolution switch no longer changes, or re-keys, the selected channels.** The
  selection was keyed `station:resolution`, so Day and Hour remembered independent
  sets and a reader who picked three channels and pressed Hour got different ones.
  It is keyed on the station now, and the set actually drawn is derived by
  intersecting with the rollup at render time rather than narrowed destructively --
  so a channel a year does not carry is simply not drawn, and is still there when
  the reader switches back.
- `aisvn.wind_v` and `maker-webhooks.wind_v` carried **`phumy2.power_w`'s band
  note**, verbatim, from a copy-paste in 0.9.0. It was talking about 415,112 zeros
  and an unimplemented pin on two channels that are now published.
- `phumy2.power_w`'s stated counts were stale: 415,112 of 415,117 readings are
  zero. It is **416,083 of 416,088**, and the column holds six distinct values in
  six years.
- Two redundant `raw_unit = "degC"` declarations on channels with `scale = 1.0`
  were removed from `aisvn.temp_c` and `phumy2.temp_c`. `raw_unit` means the unit
  the sheet wrote *when it differs*; with no conversion it is noise, and the
  frontend's "logged in a different unit" note keys on `scale !== 1`.

### Added

- **`etl.catalog.Correction` -- a declared, time-scoped change to a channel.**
  `aisvn`'s current channel reads 6.6 A low from 2020-08-24 18:42 local, and its
  power channel's output is inverted and four times too large from the same
  instant. Both are the collector's dated fault, and both are now declarations:
  `add 6.6` and `factor -0.25`, applied once at ingest, after the scale, only where
  a declared window covers the reading's instant. Windows are half-open
  `[from_ts, to_ts)` so a boundary is not claimed twice, and `op` is deliberately
  only `add` and `factor` -- a correction needing a conditional is a symptom that
  the period is described wrongly, and the way to find that out is to make the
  declaration impossible to write.

  The windows pause inside the 2020-10-23 → 2020-10-30 reconfiguration period,
  which the collector describes as a half-built logger: readings there come off
  near zero rather than -6.0, so adding 6.6 A to them would invent a phantom load.
  They are flagged as part of that window instead. The power channel's corrected
  curve then tracks solar exactly -- 0 W at night, 34.5 W at noon -- which is the
  evidence for the sign. The current channel's does **not**: its corrected
  variation is about 0.3 A and does not follow the panel, so that correction fixes
  the sign without restoring a usable signal. Both are stated in `band_note`.
  0.8 needed 22 confirmed scale windows and left 8 more unconfirmed; there are two
  declarations here, both dated, both from the collector.
- **`scripts/release_notes.py` -- the version and its notes, from one place.**
  `package.json` is canonical; `etl/__init__.py` and `pyproject.toml` are *checked*
  against it and the `## [version]` section of this changelog is taken verbatim.
  `release.yml` had an inline `awk` that would produce notes for a version that had
  never been built, from a tag that did not match `package.json`, and publish it.
  `--check` exits 2 and names the disagreeing file, so a version bump that forgets
  the changelog cannot ship. Running it with no arguments prints the notes.
- **`make fresh`** -- `data_fresh.yml` from the command line: delete the database
  *and its `-wal`/`-shm` sidecars*, then `etl all` and `verify`. A surviving WAL
  makes SQLite replay the previous run's rows, which is the failure the workflow
  exists to rule out.
- **`docs/pipeline.md`** -- every transformation between a raw cell and a charted
  point, and all 51 channels across 8 stations, generated from the catalog and the
  built database so it cannot describe a pipeline that is not the one running.

### Changed

- **Bands, on the collector's figures.** Each is now a claim about a station and
  the hardware on it, and each moved for a stated reason:

  | channel | 0.9.0 | 0.10.0 | why |
  |---|---|---|---|
  | `aisvn.solar_v` | 0–60 V | **0–25 V** | the input saturates at 29.8 V |
  | `aisvn.solar2_v` | 0–60 V | **0–15 V** | 15 V is a 2S string's ceiling; the rail is 19.5 V |
  | `aisvn.load_v` | 0–60 V | **0–20 V** | the load rail saturates at 29.67 V |
  | `aisvn.lipo_v` | 0–8.7 V | **0–5 V** | a 1S cell's ceiling; the rail is 6.84 V |
  | `aisvn.temp_c` | 0–60 °C | **0–40 °C** | the probe stands in shadow at this site |
  | `aisvn.current_a` | none | **0–3 A** | the collector's figure for this panel |
  | `aisvn.power_w` | none | **0–50 W** | the collector's figure |
  | `aisvn.wind_v` | none | **0–50 W** | confirmed a power measurement, in watts |
  | `aisvn-solar.lipo_v` | 2.5–4.35 V | **0–5 V** | a 1S cell, not a 2S one; 3.532 V is the applet's rail |
  | `aisvn2.current_a_chA` | none | **≤ 500** | ceiling only: 55% of readings are negative |
  | `aisvn2.current_a_chB` | none | **≤ 500** | ceiling only: 17% of readings are negative |
  | `maker-webhooks.solar2_v` | 0–30 V | **0–15 V** | observed 0.735–12.944 V |
  | `maker-webhooks.wind_v` | none | **0–50 W** | confirmed a power measurement, in watts |

- **`wind_v` is charted, at two stations.** 0.9.0 excluded it everywhere on the
  reasoning that 29.8 V and 14,686 mV are not plausible generator outputs. The
  collector has confirmed the channel as a power measurement **in watts**, so both
  are now 0–29.8 W and 0–14.7 W. The old assertion was not wrong about the number
  and wrong about the unit -- it read a real reading in the wrong unit and
  concluded the hardware was unimplemented, which is 0.8's error in the direction
  that hides data. `aisvn-solar.wind_v` stays hidden, and now for a reason the
  data supports rather than one taken on trust: identically zero for all 13,788
  readings.
- **`out_of_range`: 3,695 → 40,393.** The total moved *up* deliberately. It is
  almost entirely two channels the collector tightened past the point 0.9.0 had
  widened to silence them:

  | | readings flagged | share |
  |---|---:|---:|
  | `aisvn.solar2_v`, 0–15 V | 14,107 | 18.2% |
  | `aisvn.lipo_v`, 0–5 V | 14,107 | 18.2% |
  | everything else | 12,179 | 1.7% of the archive |

  Both are a single exact value repeated -- 19.5 V and 6.84 V -- on 2020-07-16 →
  2020-08-05, so they are plateaus on a rail rather than a unit error, and a band
  that declines to ring them has been widened to be quiet. Each carries a
  `band_note` saying the fire *is* the finding, which `etl.audit` requires above
  `BAND_FIRE_FRACTION` and which `tests/test_catalog.py` pins by name. Readings are
  unchanged at **731,885**; 0.8 was 631,252, or 86% of the archive.

### Tests

- 148 → 149 test functions. Seven asserted decisions this release reverses, and
  each now asserts the new truth rather than being deleted: the per-station
  temperature ceiling, the six hidden channels, `wind_v` charted only where its unit
  is confirmed, the observed out-of-range counts, and the "not mostly flags"
  invariant, which is now stated over the *remainder* after the two declared
  plateaus -- the 1% ceiling kept, applied where it still means something.
- `check_frontend.mjs` gained the mirror of the same two checks, so a browser-side
  regression in either is caught without running Python.

## [0.9.0] - 2026-09-28

A rewrite of the whole pipeline around one idea: **a channel is a fact about a
station, not a fact about a column name.**

0.8 kept a plausibility band per *column* in `etl/normalize/metrics.py`, a
per-station unit override in `config.CHANNEL_UNITS`, and twenty-two confirmed
scale factors in `build_regimes.py`, and read them in seven places. The band and
the scale were each measured against the other, and both were wrong, so most of
what the pipeline flagged was arithmetic rather than a fact about the hardware.

**`out_of_range` flags: 631,252 → 3,682.** 99.4% of them were a unit mismatch.

| | 0.8 | 0.9 |
|---|---:|---:|
| readings | 731,885 | **731,885** |
| raw files | 364 | 364 |
| stations | 8 | 8 |
| hourly / daily buckets | 25,566 / 1,184 | 25,566 / 1,184 |
| **out-of-range channel values** | **631,252** | **3,695** |
| rejected cells | 224,297 | 259,463 |
| recovered notes | 11 | 11 |
| `public/data` | 5.6 MiB | 3.3 MiB |
| `solardata.db` | 182 MiB | 104 MiB |
| unit tests | 147, one taking 50 s | 132, slowest 0.07 s |

### Added

- **`etl/catalog.py` -- the station catalog, and the documentation.** One
  declaration per `(station, channel)`: label, description, kind, the unit, the
  confirmed scale, the plausibility band, which statistics the rollups carry,
  and whether the site charts it. 51 channels across 8 stations, and 9 layouts.
  It is the only place a band, a unit or a scale is written down, and the
  database, the report, `public/data/stations.json`, `public/data/metrics.json`
  and the browser are all generated from it.

- **Eight tables, one per station.** `s_aisvn`, `s_aisvn2`, `s_aisvn_solar`,
  `s_maker_webhooks`, `s_phumy2`, `s_solar_2020_05`, `s_test`, `s_voltage_phumy`.
  Each holds only the channels that station collects, `WITHOUT ROWID` with the
  instant as the whole primary key. 75% of the cells in 0.8's single 32-column
  `readings` table were NULL, and no reader could tell which NULLs meant "not
  connected" from which meant "not measured".

- **`channel_stats` -- the per-station documentation, measured.** One row per
  `(station, channel)`: start, stop, count, min, max, mean, the 1st/50th/99th
  percentiles that make a bimodal channel visible, how many readings are exactly
  zero, how many were sentinels, how many were null-windowed, and how many the
  band rejected. Measured rather than asserted, so a band that starts firing on a
  third of a record shows up as a number that moved.

- **`etl/audit.py` -- build-time checks against the real archive.** Four checks
  that only a real run can make, and each of which would have caught a specific
  0.8 failure: every `(station, width)` in the 364 files resolves to a declared
  layout; every band is quiet or explained; a row is flagged `out_of_range`
  exactly when one of its banded channels is out of band, in both directions; and
  an excluded channel is still excluded for the reason it was. `python -m etl
  audit` is a stage, and it runs in `data.yml` and `data_fresh.yml`.

- **`BAND_FIRE_FRACTION` -- a rule about bands.** A band is a claim about a
  sensor at a site, and a flag that fires on more than 1% of a channel's own
  record cannot mark a contaminated aggregate; it is reporting a unit mismatch.
  Anything above the threshold must carry a `band_note` saying why the fire is
  the finding, and the audit fails without one. 0.8 had four channels above 20%
  and no way to know that was wrong.

- **`.github/workflows/data_fresh.yml` -- a from-scratch rebuild, on demand.**
  `workflow_dispatch` with a required reason, plus a weekly confirmation run. It
  deletes `solardata.db` and its `-wal`/`-shm` sidecars first, so the build
  starts from nothing, then runs `etl all` and `verify`. It does not commit; a
  human reads the summary and the report diff and decides.

- **`stations.json` carries each station's channels.** Label, unit, band, kind,
  decimals, the observed range, and the reason for every exclusion, so the
  picker, the axis unit and the band the chart rings a value against all come
  from the declaration the ingest applied to the raw cell.

- **A "recorded but not charted" panel on every station.** Eight channels are in
  the database and not on the chart, each with a reason and a sentence: `wind_v`
  at three stations, `phumy2.power_w`, two `aisvn-solar` load rails, and
  `solar-2020-05.event`. A reader who knows `phumy2` has a power pin would
  otherwise conclude the site dropped a column.

### Changed

- **One unit, everywhere.** The confirmed scale is applied once, at ingest, so
  the database, the rollups, the Parquet, the CSV and the browser all hold the
  same number in the same unit. There is no regime table, no `scaled_channels`
  column, no stored-vs-display unit split, and no out-of-range count that has to
  be recomputed after the fact. `_rescale_oor_counts` is gone, and so is the
  disagreement it existed to paper over.

- **A headerless file's column meanings come from the catalog, keyed on
  `(station, width)`.** The archive contains exactly nine such pairs and no
  station has two layouts of the same width, so the lookup is total and an
  undeclared width is a loud build failure. 0.8 resolved it by finding the
  nearest preceding sibling with a header of the same width, ordering on the
  parsed timestamp -- 150 lines whose failure mode was to discard 90% of an
  archive's measurements while every timestamp still ingested and nothing
  reported a problem. 305 of the 364 files are headerless, so this is the path
  most of the archive takes.

- **A station's CSV carries only that station's channels.** The header is
  generated per station, so there is nothing to discover and nothing to keep in
  step. `phumy2` shipped 27 permanently-empty columns per station-year in 0.8 and
  the browser had to work out which were real.

- **`rejects` grew by 35,166 rows, all of them sentinels.** 0.8 counted a
  placeholder in a flag and did not record the cell; 0.9 records it, so every
  value that did not become a reading is in the rejects table with its sheet row
  and its raw text. `sentinel` rejects are 32,916 of the 259,463.

- **The report is organised per station**, not per column. The band, the unit and
  the observed range are three separate columns because they answer three
  different questions, and the two of them that disagree are the finding.

- **A null window only corrects a cell that had a value.** 0.8's
  `aisvn.temp_c` window covers 1,359 rows whose temp cell the collector's
  at-source repair left *empty*. Stamping `no_signal` on them asserted that the
  collector said the input was disconnected when the sheet says nothing at all,
  which is a different claim. The 220,069 `phumy2.solar2_v` cells it does
  correct were a flat 0.0 V and are still nulled.

- **Prose is recovered by a structural test, not a length threshold.** 0.8's
  20-character floor dropped four real notes -- `STROMAUSFALL!!`, `at Library
  ...`, `leave home`, `arrive at school`. The vocabulary of column names is now
  taken from the catalog, so a side block's repeated header is not prose either
  and the count stays at 11.

- **The rollups are derived, not recomputed.** `readings_daily` is an
  aggregation of `readings_hourly`, so "every day and every sample count matches
  across the pair" is structural rather than a test that has to be written.

- **`energy_wh` is gone.** 0.8 computed it as `avg_power * n_samples * 2 / 3600`
  for every station, which asserts a 2-minute cadence and multiplies it by a
  power channel six of the eight stations do not have -- and for `phumy2` by a
  channel that is not a measurement. A reading has a timestamp and nothing else.

- **Every station is published; two are grouped as not-production.** `test` and
  `voltage-phumy` were withheld from the rollups until 0.7.2 and then marked
  `published: false`, which made `published` mean "is solar production" and
  collided with "is there anything to draw". The two are now `is_production:
  false`, listed under their own heading, and their 38,930 readings are
  reachable.

- **The CLI takes its common flags on either side of the stage**, as the
  documentation has always claimed: `python -m etl -q ingest` and
  `python -m etl ingest -q` are the same command.

### Removed

- **`etl/build_regimes.py`, `etl/normalize/` and `etl/rollup_schema.py`.** The
  22 confirmed scales are declarations in `etl.catalog`, and every one of them
  turned out to cover its channel's entire extent -- the seven `aisvn` windows
  were `scale = 1.0`, kept as a record of a recompile boundary that applies no
  conversion. A window that covers everything is a constant, and a constant is
  one number rather than a window in a detector.

- **The scale-regime detector.** It proposed 11 windows and 2 remain unconfirmed;
  both are now open questions in `etl.catalog` and `docs/roadmap.md` rather than
  things a heuristic re-proposes on every build. Nothing was ever applied from an
  unconfirmed proposal, and `baseline.json` no longer carries an
  `unconfirmed_regimes` field.

- **The Parquet stage and `scripts/parquet_manifest.py`.** The committed Parquet
  tree is 0.9.0's to regenerate or not, but a fifth build stage with its own
  manifest comparison and its own `pyarrow` dependency is not a fifth thing this
  dataset needs. `data/processed/parquet/` and `scripts/parquet_manifest.py` are
  removed; the release asset is the SQLite file.

- **`etl/stations.py`.** The registry moved into `etl.catalog` next to the
  channels it describes, because a station and its channels are one declaration
  and a folder-to-station map on its own is not.

- **Four flags that were never written**: `clip`, `non_monotonic`,
  `schema_misaligned` and `free_text` were in the vocabulary and set zero times.
  `schema_misaligned` and `free_text` are still written and are now correct;
  `clip` and `non_monotonic` had detectors that were never wired in and are gone
  with them.

### Fixed

- **Three temperature channels were scaled by 10 or 100 and banded in the scaled
  unit.** The sheets write degrees -- `32.5`, `24.2`, `29.47` -- and 0.8 stored
  `325`, `291` and `2947`, each against a band (`50-900`, `50-900`, `2149-3131`)
  that was wrong by the same factor as the value. Two errors cancelling, so the
  record looked banded when it was only rescaled, and no flag fired anywhere.
  All three are now plain degrees banded 0-60 degC, and 585 readings above 60
  degC are flagged -- which is a finding about the hardware rather than about
  arithmetic.

- **`phumy2.current2_a` flagged on all 416,088 of its readings.** The channel is
  232 mA and the 0.8 band was +/-50 A, because the band belonged to the column
  name and the scale was only applied in the rollups. The station used that count
  to decide whether an aggregate was contaminated, so the whole station read as
  broken on a sensor measuring a quarter of an amp. The confirmed millivolt-
  equivalent scale is applied at ingest, the range is 0.155-1.997 A, and nothing
  is flagged.

- **`aisvn-solar.load1_v` and `load2_v` were published as volts.** They record
  0-1,598 and 0-3,026, which cannot be volts; at that magnitude a load rail is
  implausible by three orders. The millivolt reading is likely and unconfirmed,
  so neither is charted and neither is banded, and the reason ships with the
  station.

- **`maker-webhooks.lipo_v` and `aisvn.lipo_v` were banded as 1S cells.** Both
  are bimodal -- a 0.735 V and a 6.84 V plateau respectively -- and a 1S band
  fired on 19% and 20% of each record. Both are now bounded so the plateau is not
  a per-reading out-of-range, and the bimodality is recorded as an open question
  rather than as 15,000 flags.

- **`aisvn2.current_a_chA`/`current_a_chB` and `maker-webhooks`'s current
  channels had bands that could not be true.** They are still stored and still
  published, but with no band: the unit is unresolved, and a band would assert
  an amplitude the archive cannot support.

- **`test/IFTTT_test.xlsx` no longer exists.** 0.8's raw repair deleted it, so its
  `FILE_EXCLUSIONS` entry matched nothing. The dead entry is kept so the repair
  is visible in the file that records the decision, and the surviving entry's
  prose is corrected: `IFTTT_test (1).xlsx` is 4,121 rows of the same nix/temp/
  wifi probe as its neighbours, not the 11-column solar layout 0.8 described.
  The exclusion is kept on the collector's word and the overlap is still there.

- **The openpyxl reader returned cell objects, not values.** In
  `read_only=True` mode `iter_rows()` yields `ReadOnlyCell`, so passing the cell
  straight to `str()` made every cell the literal text `<ReadOnlyCell
  'Sheet1'.A1>`. Every column then looked populated, every headerless file
  measured 13 columns wide, and the build failed on an undeclared layout.

- **`%B` and `%p` in `strptime` need a locale that is not loaded on Windows.**
  Every timestamp cell in the archive failed to parse: zero readings and 738,358
  rejects, in a shape that reads as a data problem and is a locale one. The
  parser now reads the fields out of the regex it already matched, with the
  month names as a fixed tuple.

- **A null window flagged blank cells.** See "Changed" above.

### Open questions

Carried forward, and now recorded per station in `etl.catalog` and
`docs/roadmap.md` rather than only in `AGENTS.md`:

- `aisvn.battery_v` reaches 29.8 V in 2020 and 17.9 V in 2021 against a confirmed
  12 V lead-acid pack. 1,717 readings, 2.2% of the channel, and the band is
  right -- so the readings are the question.
- `aisvn.lipo_v` and `maker-webhooks.lipo_v` are bimodal. Nothing records a
  recompile at the change.
- `aisvn2.current_a_chA` and `current_a_chB` step by roughly 200x between 2021-04
  and 2021-10 with no recompile recorded, and it is not a clean factor.
- `aisvn-solar.solar_v` maxes at 3,532 mV where a panel should reach 15-20 V open
  circuit. The collector is asked.
- `phumy2.solar2_v` is a divider output after a bridge and load were fitted, so it
  is not a panel voltage and should not be charted as one. The bridge ratio is
  unknown.
- `aisvn-solar.load1_v` and `load2_v` are in an unestablished unit.
- `maker-webhooks`'s boot counter resets every 16 readings, 526 times in 8,535,
  523 of them with no gap in sampling.
- `phumy2.power_w` is not a power measurement. The hardware was never implemented.
- `wind_v` is wired and logs, and what it logs is not a plausible generator
  output. What it is connected to is asked of the collector.
- `aisvn`'s load rail's 0 V state changes behaviour on 2020-07-10 with nothing
  recorded to explain it.

## [0.8.0] — 2026-09-27

### Changed — the raw archive

The collector has started repairing the source XLSX instead of excluding whole
stretches in `config.py`. `data/raw/**` is the primary source of truth and, per
`AGENTS.md` rule 1, not a file to edit — so this entry records what the edit
changed, what it fixes, and the three things in it that are not settled. The
previous version of every one of these files is in git history, so nothing is
unrecoverable; what is not recoverable is the *meaning*, which is why the diff
below is in words rather than a row count.

- **`aisvn/IFTTT_aisvn.xlsx` is now in volts and amps.** `solar` 13558 → 13.558,
  `battery` 12844 → 12.844, `current` 1080 → 1.08, `solar2` 4616 → 4.616, `LiPo`
  4107 → 4.107; the placeholder `200` temperatures are blank; the redundant side
  block, which was already in volts, is gone. This is the one file in the archive
  that spanned both units — millivolts before the 2020-06-17 recompile and volts
  after it — so it is the one that no single scale window could describe. The
  repair takes `aisvn`'s out-of-band count from 832 readings to 1, and blanks the
  1,359 `no_signal` placeholders that `config.NULL_WINDOWS` existed to null.
- **`test`'s 11-column solar layout is gone from the archive.** One file deleted
  outright, 24 rows cut from another, which is the stretch
  `config.FILE_EXCLUSIONS` used to exclude. `station_setup` rejects drop from
  6,144 to 4,121, and the reading count does not move — those cells never became
  readings.
- **`aisvn/IFTTT_aisvn (8).xlsx` gained a header row.** 2,000 → 2,001 rows.

**Three things about this change are not settled, and one of them makes the
published data wrong right now.** Recorded in
[`docs/roadmap.md`](docs/roadmap.md) under "In progress"; summarised here because
a release that ships 0.005 V instead of 5 V has to say so:

1. **The confirmed regime now double-scales it.** `CONFIRMED_WINDOWS` still
   divides `aisvn`'s whole record by 1000, so the rollup for 2020-06-15 reads
   `solar_v_avg = 0.005` where it read `4.57`. The regime has to be narrowed to
   the files still in millivolts, with a reason.
2. **`power` is reconstructed for the pre-recompile window.** The station did
   not report power at all before 2020-06-17 — the old 10-column layout had no
   such column. The repair filled the gap with `solar_v × current_a × 0.85`,
   which holds for exactly 1,800 rows, from the first data row to
   `June 17, 2020 at 03:18PM`. From `03:20PM` — 15:20 local, the recompile — the
   values are the station's own: the ratio `power/(V·I)` scatters over
   −2.43 … +2.08 and only 0.24% of samples equal `V·I` exactly, which is what a
   real DC measurement looks like and not what a product does. So the 0.85 factor
   is a reconstruction over a window with no measurement in it, and it needs to
   be recorded as one.
3. **`load` had 195 real readings replaced by 0.** Not a unit conversion: the
   column spans two units and did so before the repair too — millivolts up to
   `June 17, 2020 at 12:09PM` (11,490–13,543) and volts from `June 17, 2020 at
   03:20PM` (4.95–17.06). Of the 198 volt readings, 195 are now 0 and three
   survive (11.90, 11.91, 11.95 on 2020-06-18). The millivolt half was left
   untouched.

The 10-column pre-recompile layout is also no longer in the raw archive, so
`metric_defs` now records a single 11-column layout for `aisvn` where there were
two, and the recompile finding is no longer checkable against the data.

**On `power_w` being a measurement.** It is not, and a first pass at this entry
said otherwise. Only two stations have a power column at all: `aisvn`
(75,527 readings) and `phumy2` (415,117). The other six have none, so
`power_w` is not a channel that "every other station" logs. `phumy2`'s is a pin
that reads exactly 0 for 415,112 of 415,117 readings, which is open question 3 in
`AGENTS.md` and was never a measurement. `aisvn`'s is a measurement from the
recompile onwards, and a reconstruction before it.

**Two more consequences of the same edit, found by auditing the built database.**
`CONFIRMED_WINDOWS` is not one window for the whole record — it is seven `aisvn`
rows at `×0.001` covering `2020-06-15T06:10:00Z .. 2020-06-17T08:20:00Z`, which
is precisely the part of the file the repair converted: **1,480 readings, 1.91%
of the station, all in `IFTTT_aisvn.xlsx`**. So the double-scaling is bounded,
and the fix is to delete those seven rows.

An eighth row, `aisvn temp_c ×0.1` over 04:12–08:20Z the same day, now runs the
other way: the committed build had 317–341 there (tenths) and the current one has
31.7–34.2 (degrees), so the rollup divides by ten again and those hours land at
0.33 °C. Every other reading of the record is in tenths, mean 323.3. That scale
needs inverting. (The count in the window also moved, 114 → 121, unexplained.)

**The audit.** Of 363 files, **304 still hold at least one millivolt channel**:

| station | files | channels still in millivolts | readings |
|---|---:|---|---:|
| `aisvn` | 1 | `solar_v`, `battery_v`, `lipo_v`, `solar2_v`, `load_v` | 1,480 / 757 / 334 / 236 |
| `aisvn-solar` | 7 | `battery_v`, `lipo_v` | 13,006 / 13,786 |
| `aisvn2` | 79 | `battery2_v`, `lipo2_v`, `solar3_v` | 164,096 / 161,790 / 65,490 |
| `maker-webhooks` | 5 | `battery_v`, `lipo_v`, `solar_v`, `load_v`, `solar2_v` | 8,527 / 8,467 / 4,372 / 1,320 / 515 |
| `phumy2` | 205 | `lipo2_v` | 415,112 |
| `solar-2020-05` | 7 | `lipo_v` | 12,791 |
| `test`, `voltage-phumy` | 0 | — | — |

Every one of those 304 is *uniformly* millivolt for the channel in question,
which is exactly what makes `CONFIRMED_WINDOWS` a correct description of them,
and all of them are already compensated for in the rollups. Converting them in
the raw would gain nothing and would remove the evidence that the firmware wrote
millivolts, so they are left alone deliberately rather than overlooked. The
answer to "should more raw files be updated" is: only where a file is **not**
uniform, and after this change there is none.

**The 100%-flagged channels, and the two that are not a scale question.**
Dividing by 1000 brings 98–100% inside the recorded band for most of them. Two
are not:

- **`aisvn2.lipo2_v` is a stuck input.** 17 distinct values in 164,097 readings,
  with 7,097 appearing 161,790 times. The same station's `solar3_v` has 2,131
  distinct values over the same rows. The ×0.01 regime proposed for it is
  fitting 2,307 excursions, and its window (2020-06-18 to 06-23) is wrong too —
  the low values run to 2021-10-30.
- **`aisvn-solar.battery_v` is a 2 V pack, not a 12 V bank.** Raw 0–2,574, mean
  2,060, 2020-05-21 → 2020-06-12; ÷1000 gives 1.98–2.57 V, consistent with that
  file's `solar_v` topping out at 3.5 V and its header being `time, solar,
  battery, load_1, load_2, LiPo, wind, dump, boot`. It needs a per-station
  *band*, not a scale — the `CHANNEL_UNITS` gap again.

### Added

- **`test` and `voltage-phumy` are on the site.** Both were excluded from
  `public/data/` entirely, which meant the rollups existed nowhere even though
  38,930 readings were in the database, in the Parquet export and in the quality
  report — the one place a reader goes to look showed six of the eight stations
  the project documents. `build_exports.build` now writes every station in the
  registry and marks the two with `published: false`, which is a *grouping* and
  not a filter: the site lists them under "Not solar production" and prints the
  station's own note on its panel. `--all-stations` only moves them into the main
  group; it no longer decides whether their CSVs exist.

  Publishing `test` exposed a defect that the exclusion had been hiding: it
  records `temp_c` in **hundredths** of a degree where every other station uses
  tenths, and the rollup column is `temp_deci_c_avg` either way, so the site was
  about to divide 2,807 hundredths by ten and print 280 °C for a warm afternoon.
  The per-station unit now travels in `stations.json` as `channel_units`, and
  `displayDivisor` reads the coefficient off the unit string rather than
  special-casing `'0.1 degC'`. Held by `tests/test_export_policy.py` and by
  `check_render.mjs`.
- **`station.channel_units` in the manifest** — the same override, for the same
  reason. Seven of the eight stations carry `{}`.
- **`monthBounds` and `rangeForLoad` in `src/components/StationExplorer.jsx`**,
  exported and tested. The range decision was a mutable ref read inside an effect
  and could not be asserted from outside; it is now a pure function, and the
  resolution is a parameter it ignores so that "a resolution switch keeps the
  range" is a question a test can ask.
- **`docs/format-design.md` documents `solardata_raw.db`.** It is a plan, it is
  not built, and no stage writes one. The only mention of the name anywhere was
  an aside in `etl/config.py` describing a layout the current database is
  "heading towards", which read as though the file existed. The section states
  that plainly and lists the four questions that have to be answered first.
- **`docs/roadmap.md`** — what is not finished, in one place: the planned
  `solardata_raw.db`, the eight questions only the collector can answer, the
  known debt, and what has deliberately not been started. Until now the
  equivalent of that list was the open-questions section of `AGENTS.md` plus
  scattered asides, and "is X planned?" had no answer that did not involve a
  grep through all three.

### Fixed

- **Switching Day to Hour threw the reader's date range away.** The range reset
  was keyed on station, year *and resolution*, so picking November and pressing
  Hour to see the dawn jumped the chart back to the whole year. Day and Hour are
  two samplings of the same days; a From/To is a statement about which days you
  want, not how finely to draw them.
- **`battery_v` was described as a 3S LiPo.** The collector confirms `aisvn`'s is
  a lead-acid car battery. The description is now just "Battery bank voltage",
  and the comment that justified the 9-16 V band no longer claims it was derived
  from LiPo chemistry — 9-16 V is the right band for a 12 V lead pack, which
  rests at 12.4-12.8 V, charges to 14.4 V and reads ~10.1 V flat, and it is what
  makes `aisvn`'s 17.9 V daily peaks show up as `out_of_range` rather than
  blending in. The real LiPo packs are `lipo_v`/`lipo2_v` at 2.5-4.35 V.
- **`wind_v` was described as "unused, reads 0", which is false.** `aisvn`
  records 0-13.3 V hourly in 2021, non-zero in 176 of November's 696 hours, and up
  to 12,784 V in 2020; it is exactly 0 for all of 2022 and for `aisvn-solar`. The
  description is now just the name of the input. It still has **no band**,
  deliberately: the values are not a plausible generator output either, so a band
  would have to be a guess. What the input is connected to is asked of the
  collector.
- **`pages.yml` asserted 26 CSVs and the export writes 30.** Publishing the two
  bench stations would have failed every deploy. The count is now compared with
  `public/data/` by `tests/test_workflows.py`, so the next station added cannot
  break the deploy by being added to one place only.
- **Every release page said the same two sentences.** `release.yml` wrote a fixed
  heredoc into the notes, so a release never said what it changed — v0.7.1
  shipped with notes that never mention the white page it fixed. The notes are
  now extracted from this file's section for the tag, which is also the first
  thing that has ever been a *contract* rather than a convention: the tag is
  `v0.8.0` and the heading is `## [0.8.0]`, so the `v` has to come off before
  matching, and `tests/test_changelog.py` runs the workflow's own awk program
  against this file from the other side.
- **`gh release create` had no `--target`.** It creates the tag at the *default
  branch's* HEAD when the tag does not exist, so a manual run could publish a
  database built from one commit under a tag pointing at another. And
  `gh release edit` did not run on a re-run, because `upload --clobber` replaces
  the assets and leaves the notes as they were — a re-run used to be incapable of
  updating anything but the binaries. The asset sizes in the notes are measured
  with python rather than `du`, which reports allocated blocks and rounds the
  7.5 MiB Parquet directory up to 8.
- **`CHANGELOG.md` had two `## [0.7.1]` sections.** `5f4ecc6` added one without
  noticing `db13d57` had already used that version, which put a third of that
  release's work under a heading the extractor skips. Both halves are now one
  `## [0.8.0]` section, and `tests/test_changelog.py` fails on a duplicate.
- **`v0.7.2` was published by hand.** Tagged, released, with a hand-written title
  and GitHub's generated pull-request list, and it never ran `release.yml` — so
  nothing verified the database it would have shipped. It had no changelog
  section at all; there is now, recorded from what the tag contains.
- **`v0.2` is not `v0.2.0`.** The tag does not follow the convention every later
  tag does, and this file calls that release 0.2.0. Retagging a published release
  to agree with a file written afterwards is worse than recording the mismatch,
  so the mismatch is recorded in `docs/roadmap.md` and
  `tests/test_changelog.py` exempts tags that are not `X.Y.Z` rather than
  pretending the problem is not there.
- **The README described v0.1.0 of the website**, in a second `## Website`
  section that promised "a lightweight landing page, station overview cards, and
  a roadmap". It also said 734,908 readings (730,914 since the `test` station's
  solar layout was excluded), 89 tests (137), 1887 KB of site data (5.6 MiB), and
  said nothing about the `test` exclusion. `docs/format-design.md` and
  `docs/data-dictionary.md` had the same reading count, and `format-design.md`
  claimed 6.8 MiB of Parquet against its own diagram's 7.5 MiB.
- **The site was a white screen: `metrics` was renamed to `channels` on the
  child's destructuring and not at the call site**, so `TimeControls` read
  `undefined.length` and threw on every render. `npm run build` and
  `npm run dev` both started cleanly, because nothing in this project checks a
  prop name — the frontend is plain JSX with no types, and a component that
  throws at render time still builds perfectly.

  This is the **third** time the same defect has shipped, and the first two were
  not caught either:

  | | what | symptom |
  |---|---|---|
  | v0.7.0 | parent passed metric objects, child tested strings | every control disabled, chart still drew |
  | v0.7.1 | prop renamed on one side of the boundary | white screen |
  | merge `a60fd68` | reintroduced the second | white screen, on `main` and the deployed site |

  All three are a component's props disagreeing with its call site, and
  `check_frontend.mjs` cannot see any of them — it exercises the pure helpers in
  `src/data.js` and the CSV files. So `scripts/check_render.mjs` now renders the
  tree. It builds with `vite build --ssr` (plain node cannot import `.jsx` or
  resolve `import.meta.env`), renders `App` and the leaf components against the
  real committed CSVs, and asserts seven things: the app mounts, the inspector
  mounts and the payload it reads is present, **`TimeControls` accepts the props
  `StationExplorer` passes it**, the chart draws a path, the tiles render, a
  zero-length channel list renders instead of throwing, and the `METRICS`
  compatibility shim stays empty so nobody re-introduces the fixed six-channel
  list. It runs in `ci.yml` and `pages.yml`, and `npm run build` depends on it.

  It immediately found a second defect while being written: `rejects.by_reason`
  contains `repeated header row`, which had no entry in the inspector's meaning
  dictionary, so that row rendered a bare dash in the Meaning column. Fixed, and
  the check now fails the build if a new reject reason appears without an
  explanation.

- **The `aisvn` applet was recompiled mid-record and the scale window said
  otherwise.** The collector confirms the change at **2020-06-17 15:20 local**
  (08:20Z), and the sheet proves it: `IFTTT_aisvn.xlsx` row 1481 reads 03:18PM
  with ten columns and `solar 13964, battery 13814, current 398, temp 336`; row
  **1482 is a second header row** reading `time, solar, battery, current, power,
  load, wind, temp, solar2, LiPo`; and row 1483 reads 03:20PM with eleven columns
  and `solar 13.61, battery 13.54, current 0.36, temp 31.3`. Five channels step
  by ~1000× in the same two-minute sample.

  The detector had proposed `valid_to = 2020-06-18T01:48:00Z` for four of them —
  **17 h 28 min too late**. Confirming them as proposed would have scaled 17 hours
  of volts data a thousand times too small. An unconfirmed regime is at least
  visibly unconfirmed; a confirmed one with a boundary 17 hours wrong is worse
  than no regime at all.

  Two published days were in the wrong unit as a result: `aisvn` 2020-06-15 and
  2020-06-16 published `4,570` and `6,539` in a column documented as volts, and
  now read **4.570 V** and **6.539 V**.

- **A bucket that straddles a scale boundary is scaled by neither side.** The
  08:00 hourly bucket on 2020-06-17 holds 20 minutes of millivolts and 40 of
  volts; the daily bucket for the same day holds both. Both are left raw with an
  empty `scaled_channels` and the site flags them — visibly wrong beats
  confidently wrong. The containment test uses the extent of the *data* in a
  bucket, not the bucket's nominal edges, which is why 2020-06-15 scales at all
  when its daily slot spans 24 hours but no reading exists before 06:10.

- **`temp_c` held two different units in one column, and the plausibility band
  was wrong for all of it.** The applet wrote **tenths of a degree before the
  2020-06-17 recompile and plain degrees after**, so a genuine 33.5 °C reading was
  stored as `335` and compared against a 5–45 band. Every real measurement on
  the station was flagged. Three populations, all now accounted for:

  | n | raw | what it is |
  |---:|---|---|
  | 1,359 | `200.0` | a placeholder for "no temperature recorded" → **NA** |
  | 114 | `317`–`341` | genuine tenths, 31.7–34.1 °C — **left exactly as they are** |
  | 55,261 | `31.3`, `63.3`, … | plain degrees → **×10** |

  The 114 are the check that this is the right reading. A rule that scaled them
  too would put 33.5 °C at 3,350 °C, so their survival is evidence rather than an
  omission.

  **The ×10 is applied at ingest, not in the aggregate**, and that placement is
  the whole fix: the band is tested per raw cell in `coerce_cell`, so a unit
  correction made afterwards means the flags were already wrong, and rule 2 says
  a flag a reader cannot trust is worse than no flag. `config.UNIT_FIXES` is a new
  mechanism for this, distinct from both `NULL_WINDOWS` and from a regime.
  `phumy2.temp_c` goes from being flagged on **415,112 of 415,117 readings** to
  **0**; `aisvn` from most of the station to 501 of 77,526 hours (0.6%), and
  those 501 are the genuine 0 °C disconnected-sensor readings.

- **`temp_c`'s unit is not the same for every station, and one band could not
  describe that.** `test` logs hundredths of a degree — the collector asked for
  that resolution specifically, and the raw values are 2,149–3,131 — where every
  other station uses tenths. A band is keyed by column, so all **33,377** of
  `test`'s temperatures were flagged against a range they cannot satisfy.
  `config.CHANNEL_UNITS` now carries a per-station unit and range, applied in
  **both** places the band is tested: `coerce_cell` at ingest and the per-metric
  counts in the aggregate. Applying it in one place is the same bug one level up.

- **The rollup column is now named for the unit it holds.** `temp_c_avg` →
  `temp_deci_c_avg`, and likewise `_min` and `_max`. Scaling a column in place
  while leaving its name saying degrees would have reproduced exactly the mismatch
  `metric_defs` had, which cost two commits to unpick. The count columns keep the
  plain channel name because a count has no unit, and the browser divides by the
  unit string from `metrics.json` for display.

- **`temp_c` is no longer a channel the scale detector watches.** Its unit is
  settled on the collector's word, and left in, the detector misfired: the band
  is in tenths, `test`'s values are in hundredths, so it proposed `test.temp_c
  ×0.1`, which would turn a 27.63 °C reading into 276.3 °C.

- **`rejects.reason` was a sentence, on 220,074 rows.** Rule 2 says to keep it a
  stable category, and the archive is where ignoring that shows: the
  `NULL_WINDOWS` path stored the collector's ~300-character note as the reason on
  every cell it nulled. That is **80.6 MiB of one paragraph, repeated**, and it
  made `rejects` (96.1 MiB) as large as `readings`.

  | | before | after |
  |---|---:|---:|
  | `rejects.reason` for those rows | 300 chars | `null_window`, 11 chars |
  | `rejects` table | 96.1 MiB | **15.8 MiB** |
  | `solardata.db`, VACUUMed | 329.2 MiB | **166.7 MiB** |
  | gzipped, the Release asset | 20.0 MiB | 18.7 MiB |

- **`null_window` rejects had an empty `column_name`.** The code derived it by
  iterating `row_flags` for `no_signal:` prefixes, but `row_flags` is a merged
  *string*, so the loop walked its characters and matched nothing.

- **`metric_defs` could only describe one layout per folder, and the wrong one
  won.** Keyed on `(station_id, source_dir, col_index)`, a folder held one meaning
  per column index and a second layout overwrote the first: `aisvn` column 4 was
  recorded as `load`/`load_v` for all 39 files when it is `load` in one and
  `power`/`power_w` in the other 38. **The ingest was never wrong** — each file
  maps with its own width-matched header — but this is the table the report and
  the channel-coverage tab present as the schema.

- **An implausible reading could be averaged into a plausible number.** `phumy2`
  2020-11-27 16:00 UTC holds one sample of `power_w = 19877` where its neighbours
  are 0; averaged with 29 good zeros it becomes 662.57 W, *inside* the ±2000 W
  band. The row-level `n_out_of_range` reads 30 of 30 and cannot help, because
  `current2_a` reads ~232 against a ±50 A band for the whole period. Both
  rollups now carry `<channel>_n_oor`: that hour reads `power_w_n_oor = 1`,
  `solar2_v_n_oor = 1` and `current2_a_n_oor = 30`.

- **The channel picker was a fixed list of six metrics.** `aisvn2` logs
  `solar3_v` and no `power_w`; `phumy2` logs `lipo2_v`; `aisvn-solar` logs
  `load1_v`/`load2_v`. All were in the rollups with nowhere to appear, which is
  why selecting AISVN #2 offered nothing that worked. Channels are now discovered
  from the rollup the station ships.

- **`boot_count` was described as an uptime counter. It is not.** It is the
  number of successful submissions since the last reboot: 667,040 of 680,672
  consecutive pairs step by exactly +1 (98.0%), with an interval of 121.9 s for
  `aisvn`, 120.3 s for `aisvn2`, 123.5 s for `phumy2`, and 62.3 s for
  `maker-webhooks` and `test`. There are ~650 resets and **no timestamp anywhere
  carries two different values**. An earlier "zero reboots" claim was wrong: it
  required a reset to follow a gap in sampling, and 523 of `maker-webhooks`' 526
  resets have no gap at all.

- **A merge left the tree inconsistent and all three workflows red.**
  `etl/build_aggregate.py` carried its import block twice — harmless to read, six
  F811s to `ruff` — and the merge took the stale side of the committed
  `aisvn` CSVs, putting 06-15 and 06-16 back to 4,570 and 6,539 "V". The
  assertion named for that case caught it, and regenerating the artefacts from
  the code fixed it.

### Changed

- **The `test` station is now probe-only.** The collector's account is that its
  11-column solar layout was system setup rather than measurement. `test` keeps
  **33,377 readings**, 2020-07-05 to 2020-08-21, with no solar channel at all.

  The exclusion is by **source file, not by date**, and it has to be.
  `IFTTT_test (1).xlsx` *starts* on 2020-06-14, but its June rows duplicate
  `IFTTT_test.xlsx` and were absorbed by the primary key, so everything it
  uniquely contributes is **4,120 readings dated 2020-07-01 18:18 to 2020-07-08
  12:12** — after the probe had already begun, and still not measurements. A
  timestamp cut-off at 2020-07-01 would have kept all of them.

  Removing them takes **3,994 readings** and a further **2,150 rows that were
  already duplicates** — which is why `duplicate_ts` falls by 2,150 while the
  recorded exclusion is **6,144 rows**. What left the readings and what left the
  archive are different questions and the baseline records both. **Nothing
  vanished**: every data row goes into `rejects` with `reason = 'station_setup'`,
  its sheet row and its timestamp.

- Confirmed scale windows can now carry an explicit instant boundary, so a
  mid-day recompile is expressible. `CONFIRMED` still pins whole channels for the
  collector's "logged in millivolts for the whole record" cases.
- The rollup column list is declared once, in `etl/rollup_schema.py`, and
  imported by the aggregate stage and the export stage. Three places need to
  agree on it — schema, SQL, CSV header — and they had already drifted once.
- Per-metric out-of-range counts read their bounds from `METRIC_BY_COLUMN` and
  `CHANNEL_UNITS` as bound SQL parameters, so the plausibility table has one home
  and the site cannot drift from the criterion the ingest applied.
- Size claims in `AGENTS.md`, `README.md`, `docs/data-dictionary.md`,
  `docs/format-design.md` and `.gitignore` are updated to the measured figures.

### Added

- The **uplink duration** channel `wifi_tx_ms` — the milliseconds the station
  needs to connect to wifi and transmit — previously mislabelled `wifi_raw` and
  read as a counter.
- `channel_ranges` in `quality.json`: what every channel a station actually
  recorded, beside the band the pipeline expects. Each disagreement is a second
  pack, an unconfirmed scale, or a band wrong for that installation, and the
  archive cannot say which. `aisvn2.lipo2_v` is the one that remains, at
  6.3–7.1 V against a band written for a single cell.
- Three levels of "this number is not to be trusted", as counts rather than
  verdicts: `clean`, `partial` (some samples flagged, so the aggregate is
  neither), `contaminated` (every sample flagged). Separately, a value inside
  the band but outside what that station has recorded is reported against the
  station and never called a flag. Nothing is removed at any level.
- `check_frontend.mjs` asserts seven invariants by name, each failing the build
  if its regression returns, and `check_render.mjs` renders the tree so a prop
  drift fails the build instead of the site.

---

The rest of 0.8.0, in commit order. This half is the temperature-unit work,
and it is recorded here rather than under a version of its own because it was
written while 0.7.1 was still the current one -- which is how the changelog
came to have two `## [0.7.1]` sections and how `release.yml` ended up reading
the wrong one. `tests/test_changelog.py` now fails on a duplicated version.

- **The `aisvn` applet was recompiled mid-record and the scale window said
  otherwise.** The collector confirms the change at **2020-06-17 15:20 local**
  (08:20Z), and the sheet proves it: `IFTTT_aisvn.xlsx` row 1481 reads 03:18PM
  with ten columns and `solar 13964, battery 13814, current 398, temp 336`; row
  **1482 is a second header row** reading `time, solar, battery, current, power,
  load, wind, temp, solar2, LiPo`; and row 1483 reads 03:20PM with eleven columns
  and `solar 13.61, battery 13.54, current 0.36, temp 31.3`. Five channels step
  by ~1000x in the same two-minute sample, which is a firmware change and not
  five coincidences.

  The detector had proposed `valid_to = 2020-06-18T01:48:00Z` for four of them --
  **17 h 28 min too late**. Everything after 08:20 was already in volts, so
  confirming those windows as proposed would have scaled 17 hours of volts data a
  thousand times too small. An unconfirmed regime is at least visibly
  unconfirmed; a confirmed one with a boundary 17 hours wrong is worse than no
  regime at all. Eight windows are now confirmed with explicit instant
  boundaries and `unconfirmed_regimes` goes 11 -> 3.

- **Two published days were in the wrong unit.** `aisvn` 2020-06-15 and
  2020-06-16 are entirely inside the millivolt window, and the rollups were
  publishing `4,570` and `6,539` in a column documented as volts -- a 4.6 V panel
  and a 6.5 V panel rendered as 4.6 kW and 6.5 kW. They now read **4.570 V** and
  **6.539 V**, which is where a panel sits. Two band checks changed as a
  consequence and were updated with the reason rather than the number: days
  flagged on `solar_v` go 4 -> 2, and on `battery_v_min` 23 -> 21, because the
  scaled battery now lands at 12.385 V and 12.483 V where a 3S LiPo belongs. The
  21 that remain are the real question -- a battery reading 29.6 V is a second
  pack or an unconfirmed scale, and the band is what keeps that visible.

- **A bucket that straddles a scale boundary is no longer scaled by either
  side.** The 2020-06-17 hourly bucket at 08:00 holds 20 minutes of millivolts
  and 40 of volts; the 2020-06-17 daily bucket holds both too. Scaling either
  would publish a number no single unit describes, so both are left raw with an
  empty `scaled_channels` and the site flags them. The containment test uses the
  extent of the **data** in the bucket rather than the bucket's nominal edges,
  which is why 2020-06-15 scales at all when its daily slot spans 24 hours but no
  reading exists before 06:10.

- **`rejects.reason` was a sentence, on 220,074 rows.** Rule 2 says to keep it a
  stable category, and the archive is where ignoring that shows: the
  `NULL_WINDOWS` path stored the collector's ~300-character note as the reason on
  every cell it nulled. That is **80.6 MiB of one paragraph, repeated**, and it
  made `rejects` (96.1 MiB) as large as `readings`.

  | | before | after |
  |---|---:|---:|
  | `rejects.reason` for those rows | 300 chars | `null_window`, 11 chars |
  | `rejects` table | 96.1 MiB | **15.8 MiB** |
  | `solardata.db`, VACUUMed | 329.2 MiB | **166.7 MiB** |
  | gzipped, the Release asset | 20.0 MiB | 18.7 MiB |

  **No data moved.** The counts are identical; only `unconfirmed_regimes` changed
  in the baseline. The prose now lives once, in `config.py`, republished to
  `quality.json` as `null_windows` and `bad_windows` paired with the rows each
  explains, and shown on a dedicated **Windows** tab.

- **`null_window` rejects had an empty `column_name`.** The code derived it by
  iterating `row_flags` for `no_signal:` prefixes, but `row_flags` is a merged
  *string*, so the loop walked its characters and matched nothing. All 220,074
  rows said nothing about which channel they were about.

- **`metric_defs` could only describe one layout per folder, and the wrong one
  won.** Keyed on `(station_id, source_dir, col_index)`, a folder held one meaning
  per column index and a second layout overwrote the first: `aisvn` column 4 was
  recorded as `load`/`load_v` for all 39 files when it is `load` in one and
  `power`/`power_w` in the other 38. **The ingest was never wrong** -- each file
  maps with its own width-matched header -- but this is the table the report and
  the channel-coverage tab present as the schema.

- **An implausible reading could be averaged into a plausible number.** `phumy2`
  2020-11-27 16:00 UTC holds one sample of `power_w = 19877` where its neighbours
  are 0; averaged with 29 good zeros it becomes 662.57 W, *inside* the +/-2000 W
  band. The row-level `n_out_of_range` reads 30 of 30 and cannot help, because
  `current2_a` reads ~232 against a +/-50 A band for the whole period. Both
  rollups now carry `<channel>_n_oor`: that hour reads `power_w_n_oor = 1`,
  `solar2_v_n_oor = 1` and `current2_a_n_oor = 30`.

- **The channel picker was a fixed list of six metrics.** `aisvn2` logs
  `solar3_v` and no `power_w`; `phumy2` logs `lipo2_v`; `aisvn-solar` logs
  `load1_v`/`load2_v`. All were in the rollups with nowhere to appear, which is
  why selecting AISVN #2 offered nothing that worked. Channels are now discovered
  from the rollup the station ships.

- **`boot_count` was described as an uptime counter. It is not.** It is the
  number of successful submissions since the last reboot: 667,040 of 680,672
  consecutive pairs step by exactly +1 (98.0%), and the interval when they do is
  121.9 s for `aisvn`, 120.3 s for `aisvn2`, 123.5 s for `phumy2`, and 62.3 s for
  `maker-webhooks` and `test`, which really do submit twice as often. There are
  ~650 resets and **no timestamp anywhere carries two different values**. An
  earlier claim of "zero reboots" was wrong: it required a reset to follow a gap
  in sampling, and 523 of `maker-webhooks`' 526 resets have no gap at all, which
  is what a reboot looks like when the station keeps sampling.
  `maker-webhooks` resets every 16 readings, which the archive does not explain.

### Added

- The **uplink duration** channel `wifi_tx_ms` -- the milliseconds the station
  needs to connect to wifi and transmit -- previously mislabelled `wifi_raw` and
  read as a counter.
- `channel_ranges` in `quality.json`: what every channel a station actually
  recorded, beside the band the pipeline expects. `phumy2.temp_c` reads
  15.5-80.6 C against a 5-45 C band, and `aisvn.battery_v` up to 29.6 V against
  9-16 V. Each is a second pack, an unconfirmed scale, or a band wrong for that
  installation, and the archive cannot say which -- so both are reported and the
  disagreement is the finding.
- Three levels of "this number is not to be trusted", as counts rather than
  verdicts: `clean`, `partial` (some samples flagged, so the aggregate is
  neither), `contaminated` (every sample flagged). Separately, a value inside
  the band but outside what that station has recorded is reported against the
  station and never called a flag. Nothing is removed at any level.
- `check_frontend.mjs` asserts six invariants by name, each failing the build if
  its regression returns: *a contaminated aggregate is distinguishable from a
  clean one*, *every channel a station records is offered, not a fixed six*, *a
  folder that changed layout keeps both layouts, not the last one*, *a reject
  reason is a category, never a sentence*, *a day the collector scaled is no
  longer published in the wrong unit*, and *the uptime counter is published*.

### Changed

- Confirmed scale windows can now carry an explicit instant boundary, so a
  mid-day recompile is expressible. `CONFIRMED` still pins whole channels for the
  collector's "logged in millivolts for the whole record" cases, which remains
  the common one.
- The rollup column list is declared once, in `etl/rollup_schema.py`, and
  imported by the aggregate stage and the export stage. Three places need to
  agree on it -- schema, SQL, CSV header -- and they had already drifted once.
- Per-metric out-of-range counts read their bounds from `METRIC_BY_COLUMN` as
  bound SQL parameters, so the plausibility table has one home and the site
  cannot drift from the criterion the ingest applied.
- Size claims in `AGENTS.md`, `README.md`, `docs/data-dictionary.md` and
  `docs/format-design.md` are updated to the measured figures.

## [0.7.2] — 2026-09-26

Published by hand rather than by `release.yml`: the release notes were the
generated pull-request list, and this section did not exist. Recorded now from
what the tag contains, which is 0.7.1 plus these four.

- **The collector-confirmed unit scales are applied to the published rollups**
  (#9), so the site stops showing a channel 1000x too large where the firmware
  writes millivolts as integers.
- **CI split by cost**, and each raw sheet is parsed once instead of three times
  (#10): the build went from ~3 min to ~1.
- **Hourly rollups are published** (#11), so the chart stops averaging away the
  dawn and dusk of every day.
- **Every layout a folder had is recorded**, and the uptime counter is exported
  (#13), which is the only reboot evidence in the archive.

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

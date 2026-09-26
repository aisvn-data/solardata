# Data dictionary

Every table and column in `data/processed/solardata.db`. Read
`etl/schema.sql` for the DDL and `AGENTS.md` for the rules that govern edits.

## Reading the clock

| Column | Meaning |
|---|---|
| `ts_utc` | **The primary key clock.** RFC 3339, UTC, `Z` suffix: `2020-07-14T03:12:00Z`. Use for anything analytical. |
| `ts_local` | Naive local wall clock, `2020-07-14T10:12:00`. Display only. |
| `tz` | IANA zone assumed for the station, `Asia/Ho_Chi_Minh`. |

Raw column A is US-locale text with no offset, so `ts_utc` is derived. The
offset is UTC+07:00 with no DST, which is correct for Vietnam, but it is an
assumption — see `AGENTS.md` open question 1.

## Tables

### `stations`

One row per physical instrument, not per folder. `source_dirs` is a JSON array
because `phumy2`/`phumy2a`/`phumy2b` are three archive chunks of one station.

| Column | Meaning |
|---|---|
| `station_id` | Stable slug used in every other table |
| `is_production` | 0 for `test` and `voltage-phumy`; excluded from exports |
| `first_ts_utc`, `last_ts_utc`, `n_readings` | Observed coverage, filled in after ingest |

### `source_files`

Provenance for all 364 raw files. `sha256` lets a rebuild prove it read the
same bytes.

| Column | Meaning |
|---|---|
| `has_header` | 1 if row 1 is a header row (59 of 364 files) |
| `schema_donor` | For a headerless file, the sibling whose header supplied the column names; NULL if the file had its own |
| `inferred` | 1 when the column meaning was borrowed rather than read |
| `extra_blocks` | Redundant side-by-side column blocks found to the right |
| `repeated_headers` | Sheet rows where a header repeats mid-file |
| `n_ingested`, `n_duplicate_ts`, `n_rejected` | Per-file outcome |

### `readings`

One row per station per instant, wide and sparse. **A channel is NULL when the
station did not have it, and NULL is not the same as 0.**

| Column | Type | Unit | Notes |
|---|---|---|---|
| `solar_v`, `solar2_v`, `solar3_v` | REAL | V | Panel/collector voltage |
| `battery_v`, `battery2_v` | REAL | V | Bank voltage; banded 9–16 V (3S LiPo) |
| `current_a`, `current2_a` | REAL | A | Banded ±50 A |
| `current_a_chA`, `current_a_chB` | REAL | A | Headers `currentA`/`curA` and `currentB`/`curB` |
| `power_w` | REAL | W | Banded ±2000 W |
| `load_v`, `load1_v`, `load2_v` | REAL | V | Meaning disputed — see open question 5 |
| `wind_v` | REAL | V | Reads 0 throughout; no plausible band, so never flagged |
| `temp_c` | REAL | degC | Banded 5–45 degC |
| `lipo_v`, `lipo2_v` | REAL | V | Single-cell pack, banded 2.5–4.35 V |
| `adc_raw`, `voltage_adc`, `digital_adc`, `dump_adc` | REAL | count | Uncalibrated, no band, never flagged |
| `boot_count` | INTEGER | count | Monotonic logger counter; **resets on reboot** |
| `millis_ms` | INTEGER | ms | `millis()` since boot |
| `nix_raw`, `wifi_raw` | REAL | count | `test` bench only |
| `event` | TEXT | — | IFTTT event name, `solar-2020-05` only |
| `quality_flags` | TEXT | — | Comma-separated, see below |
| `source_file_id`, `sheet_row` | — | — | Exact provenance for the row |

Bands only ever set a flag. No value is ever clipped, nulled, or rescaled
because of them. The same table is published to the browser as
`public/data/metrics.json`, so the site's chart applies the identical criterion
to a rollup value that the ingest applied to the raw cell.

### `quality_flags`

Comma-separated, so one row can carry several at once. Two families are
parameterised by column — `bad_window:<column>` and `no_signal:<column>` — which
is why a flat lookup is not enough to explain every flagged row.

| Flag | Rows | Set when |
|---|---|---|
| `out_of_range` | 638,555 | Value kept, but outside the metric's band |
| `no_signal:<column>` | 220,069 | A `NULL_WINDOWS` entry: the input was disconnected, so the stored number is a false reading. Nulled, with a `rejects` row carrying the prose |
| `sentinel` | 30,599 | Raw cell was `-992`, `-1`, `342.0` or `342.1`; stored as NULL |
| `bad_window:<column>` | 6,303 | A `BAD_WINDOWS` entry: kept, but a human has said not to believe it |
| `schema_misaligned` | 0 | Row did not match the donor schema. Unreachable now that donors match on width |
| `clip` | 0 | Detector implemented and unit-tested, never wired into the ingest |
| `non_monotonic` | 0 | No reboot detector runs, despite `boot_count` being a usable one |
| `free_text` | 0 | Unmapped columns are skipped before this could be set |

`duplicate_ts` appears **only** as a `rejects.reason`, never in
`readings.quality_flags`: the primary key absorbs the second copy, so no
`readings` row exists to carry the flag.

### `metric_defs`

What each raw column of each station means, and how confident we are. One row per
`(station_id, source_dir, n_columns, col_index)`.

| Column | Meaning |
|---|---|
| `n_columns` | Width of the raw layout this row describes |
| `col_index` | 0-based index in the raw sheet |
| `raw_name` | Header text, `''` when the file had no header |
| `canonical_col` | Target column in `readings`, NULL when unmapped |
| `confidence` | `high` for an exact header match, else lower |
| `inferred` | 1 when the names were borrowed from a donor file |
| `reason` | Why it mapped, or why it did not |
| `n_files` | Files in the folder using this layout |

`n_columns` is part of the key because a folder is a chronological run of chunks
from one applet and the applet may change its columns partway through. `aisvn`
went from 10 columns to 11 on 2020-06-17 when a `power` channel was added, so
column 4 is `load` in one file and `power` in the other 38; `test` holds two
unrelated schemas (4 columns of nix/temp/wifi probe for 16 of its 19 files, 11
columns of solar channels for 2). Keyed on the column index alone, a folder can
hold only one meaning per index and the second layout overwrites the first.

`confidence` is the column to filter on before trusting an automatic analysis.

### `regimes`

Windows over which a column's scaling is believed constant. Written by
`etl.normalize.units` with `status='unconfirmed'` and the evidence as JSON.

| Column | Meaning |
|---|---|
| `scale` | Proposed multiplier, e.g. `0.001` for millivolts |
| `valid_from`, `valid_to` | Half-open UTC window; `valid_to` NULL means open |
| `status` | `unconfirmed` \| `confirmed` \| `rejected` |
| `detected_by` | `range` (heuristic) or `manual` |
| `confidence` | `high` \| `medium` \| `low` |

**No code applies an unconfirmed regime.** A human promotes one to `confirmed`
after checking it against the hardware.

### `rejects` and `notes`

`rejects` holds every cell that did not become a reading, with `sheet_row`,
`raw_value` and `reason`. `reason` is a stable category so the report can group
by it; currently `repeated header row` (6) and `duplicate_ts` (4,403).

A same-station duplicate timestamp lands here too. The `readings` primary key
keeps the first copy and absorbs the second, and the absorbed row is recorded
here so it stays inspectable. This is distinct from the ~121,000 timestamps
shared *between* stations, which are separate instruments sampling the same
instant and are both kept as normal readings.

`notes` holds human prose found in a data cell, anchored to `ts_utc` and the
spreadsheet column it came from. Currently 10 rows; see `CHANGELOG.md` F4.

### `readings_hourly` and `readings_daily`

Pre-aggregated so the website never scans the raw table. `readings_daily` is
derived from `readings_hourly`, so the two cannot disagree — `check_frontend.mjs`
asserts that every day and every sample count matches across the pair.

- `day` is the **local** calendar day; `ts_utc_day` is the UTC midnight of that
  local day. They are not the same instant and both are provided.
- `n_out_of_range` counts samples in the bucket that carried the flag on **any**
  channel. It is a row-level count, so a day can be flagged because of a channel
  the chart is not drawing; the site therefore applies the band per drawn value
  rather than trusting this count to identify which value is suspect.
- `energy_wh` assumes a 2-minute nominal cadence
  (`avg_power * n_samples * 2 / 3600`), which matches 357 of 364 files.
- `boot_count_min` / `boot_count_max` carry the logger's own monotonic read
  counter, which resets on reboot. Min and max, never a mean: a mean across a
  reboot averages two boot sessions into a number that never happened. A bucket
  whose min is 1 restarted; the max is how long it had been up. This is the only
  channel recording the hardware's view of its own uptime, and it is absent for
  `solar-2020-05` and `voltage-phumy`, whose sheets have no such column.
- The two tables do not carry the same statistics. `readings_hourly` has
  `battery_v_avg` and `readings_daily` does not, so the daily battery column is
  `battery_v_min` — the day's lowest. The site names the statistic in its
  readout, because both appear under one "Battery" label.

## Artefacts

| Path | Format | Size | In git? | Purpose |
|---|---|---|---|---|
| `data/processed/solardata.db` | SQLite | 167 MiB | no | Canonical store, query in place. 19 MiB gzipped as a Release asset |
| `data/processed/parquet/` | Parquet, `station=X/year=Y` | 7.5 MiB | **yes** | Interchange; pandas/duckdb/dask. All 734,908 readings at the native 119 s cadence |
| `public/data/{station}/daily/{year}.csv` | CSV | 0.1 MiB | **yes** | Daily rollups the site's Day view fetches |
| `public/data/{station}/hourly/{year}.csv` | CSV | 1.7 MiB | **yes** | Hourly rollups the site's Hour view fetches |
| `public/data/stations.json` | JSON | 5 KB | **yes** | Station metadata, coverage, which rollups exist |
| `public/data/metrics.json` | JSON | 5 KB | **yes** | The plausibility bands, verbatim from the ETL |
| `public/data/quality.json` | JSON | 80 KB | **yes** | The whole report, for the inspector tab |
| `data/processed/quality_report.md` | Markdown | ~10 KB | **yes** | The review artefact |
| `data/processed/quality_report.json` | JSON | ~80 KB | **yes** | Machine-readable form of the same |
| `data/baseline.json` | JSON | ~1 KB | **yes** | Expected counts, enforced by CI |

`public/data/` is committed because the site is static: GitHub Pages serves the
files straight from a clone, and a frontend-only change must not need the ingest.
`solardata.db` is gitignored — at 167 MiB it is over GitHub's 100 MiB
per-file limit. `release.yml` ships it VACUUMed and gzipped (19 MiB); the
committed Parquet is 7.5 MiB for the same 734,908 rows and needs no download
at all.
Everything is rebuilt with `make build`; the database is also distributed as a
Release asset.

## `data/baseline.json`

The expected output of a build, enforced by `python -m etl verify` and by CI.

| Field | Guards against |
|---|---|
| `readings` | The headline number. Any drift means a change in what was ingested. |
| `files` | A raw file disappeared or was skipped. |
| `stations` | The station registry changed. |
| `duplicate_ts` | `INSERT OR IGNORE` dedupe changed behaviour. |
| `malformed_rejects` | The timestamp parser got stricter or looser. |
| `notes` | Note recovery changed; these are human context, not noise. |
| `unconfirmed_regimes` | The scale detector moved, which may mean a new finding. |
| `headerless_without_donor` | **Pinned to 0.** Non-zero means a headerless file's measurements were discarded. |
| `hourly_buckets`, `daily_buckets` | The aggregate rollups changed shape. |

`recorded.reason` is required and travels with the file, so a future diff
explains itself without re-running anything.

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
because of them.

### `quality_flags`

| Flag | Set when |
|---|---|
| `sentinel` | Raw cell was `-992` or `-1`; stored as NULL |
| `out_of_range` | Value kept, but outside the metric's band |
| `duplicate_ts` | Row lost a `(station_id, ts_utc)` collision |
| `clip` | Repeated identical value long enough to be a rail artefact |
| `non_monotonic` | Counter went backwards |
| `free_text` | Prose found in a numeric cell; the text is in `notes` |

### `metric_defs`

What each raw column of each folder means, and how confident we are. One row per
`(station_id, source_dir, col_index)`.

| Column | Meaning |
|---|---|
| `col_index` | 0-based index in the raw sheet |
| `raw_name` | Header text, `''` when the file had no header |
| `canonical_col` | Target column in `readings`, NULL when unmapped |
| `confidence` | `high` for an exact header match, else lower |
| `inferred` | 1 when the names were borrowed from a donor file |
| `reason` | Why it mapped, or why it did not |

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

Pre-aggregated so the website never scans the raw table.

- `day` is the **local** calendar day; `ts_utc_day` is the UTC midnight of that
  local day. They are not the same instant and both are provided.
- `energy_wh` assumes a 2-minute nominal cadence
  (`avg_power * n_samples * 2 / 3600`), which matches 357 of 364 files.

## Artefacts

| Path | Format | Size | Purpose |
|---|---|---|---|
| `data/processed/solardata.db` | SQLite | 158 MiB | Canonical store, query in place |
| `data/processed/parquet/` | Parquet, `station=X/year=Y` | 6.8 MiB | Interchange; pandas/duckdb/dask |
| `data/exports/{station}/daily/{year}.csv` | CSV | 0.1 MiB | What the frontend fetches |
| `data/exports/stations.json` | JSON | — | Station metadata and coverage |
| `data/processed/quality_report.md` | Markdown | — | The review artefact |

All are gitignored and rebuilt with `make build`.

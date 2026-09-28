# Data dictionary

Every table in `data/processed/solardata.db`, and the four files under
`public/data` the site fetches. Read `etl/catalog.py` for the declarations this
describes, `etl/schema.sql` for the DDL of the shared tables, and `AGENTS.md` for
the rules that govern edits.

## The one idea

**A channel is a fact about a station, not a fact about a column name.**

`solar_v` is 0-29.8 V at `aisvn`, 0-3,532 at `aisvn-solar` (millivolts), 0-23,860
at `aisvn2` (millivolts) and -984-34,034 at `maker-webhooks` (millivolts). A
plausibility band is a claim about a sensor at a site, so it belongs to a
`(station, channel)` pair and nowhere else. 0.8 carried one band per column name
and tested raw millivolt cells against it, which marked 631,252 readings out of
range on arithmetic rather than on the hardware -- all 416,088 of `phumy2`'s
samples, on a sensor measuring a quarter of an amp.

The second consequence: **one unit, everywhere.** The confirmed scale is applied
once, at ingest, so the database, the rollups, the CSVs and the browser all hold
the same number in the same unit. There is no regime table, no stored-vs-display
split, and no out-of-range count that has to be recomputed after the fact.

## Reading the clock

| Column | Meaning |
|---|---|
| `ts_utc` | **The primary key.** RFC 3339, UTC, `Z` suffix: `2020-07-14T03:12:00Z` |
| `ts_local` | Naive local wall clock, `2020-07-14T10:12:00`. Display only. |
| `tz` (in `stations`) | IANA zone assumed for the station, `Asia/Ho_Chi_Minh` |

Raw column A is US-locale text with no offset, so `ts_utc` is derived. The offset
is UTC+07:00 with no DST, which is correct for Vietnam and an assumption about
every reading in the archive. `etl/readers/times.py` parses it with a fixed
month table rather than `strptime`, because `%B` and `%p` need a locale that is
not loaded on Windows and every cell then fails to parse.

**Never compare these as strings.** The sheets write `July 4` and `July 14`
unpadded, so string order puts the fourteenth before the fourth. Every
comparison, `GROUP BY` and donor-equivalent goes through the parsed clock.

## The station tables

Eight tables, one per station, named `s_<station_id>` with hyphens replaced by
underscores:

| Station | Table | Readings | Coverage (UTC) |
|---|---|---:|---|
| AISVN #1 | `s_aisvn` | 77,526 | 2020-06-15 to 2022-02-22 |
| AISVN #2 | `s_aisvn2` | 164,098 | 2020-06-18 to 2021-11-01 |
| AISVN Solar (archived) | `s_aisvn_solar` | 13,788 | 2020-05-21 to 2020-06-12 |
| Maker Webhooks (archived) | `s_maker_webhooks` | 8,535 | 2020-05-30 to 2020-06-12 |
| Phu My Hung #2 | `s_phumy2` | 416,088 | 2020-06-15 to 2026-09-27 |
| Solar bench (2020-05-16) | `s_solar_2020_05` | 12,920 | 2020-05-16 to 2020-06-15 |
| Test bench | `s_test` | 33,377 | 2020-07-05 to 2020-08-21 |
| Voltage calibration | `s_voltage_phumy` | 5,553 | 2020-07-04 to 2020-07-12 |

Each holds only the channels that station collects, so there is no 32-column grid
in which 75% of every row is NULL and no reader can tell which NULLs mean "not
connected" from which mean "not measured". **A channel is NULL when the station
did not have it, and NULL is not 0.**

| Column | Type | Notes |
|---|---|---|
| `ts_utc` | TEXT | `PRIMARY KEY`, `WITHOUT ROWID` |
| `ts_local` | TEXT | Display only |
| *one per channel* | REAL / INTEGER / TEXT | See `etl/catalog.py`. INTEGER for the uptime counters. |
| `flags` | TEXT | Comma-separated, see below. `''` means no flag. |
| `source_file_id`, `sheet_row` | INTEGER | Exact provenance. `sheet_row` is 1-based as openpyxl reports it, so it is the number a person counting the file would say. |

`WITHOUT ROWID` with a TEXT key stores the rows in key order, which is both
smaller than a rowid table plus an index and free to range-scan.

### The channels

| Channel | Unit | Scale | Stations | Band | Note |
|---|---|---:|---|---|---|
| `solar_v`, `solar2_v`, `solar3_v` | V | 1 / 0.001 | varies | 0-30 V | volts at `aisvn`; millivolts elsewhere |
| `battery_v` | V | 1 | `aisvn` | 9-16 V | a 12 V lead-acid car battery, collector-confirmed |
| `battery2_v` | V | 0.001 | `aisvn2` | 9-16 V | millivolts, same bank design |
| `current_a` | A | 1 | `aisvn` | ±50 A | recorded −12.81 to 3.68 |
| `current2_a` | A | 0.001 | `phumy2` | ±5 A | milliamps; 0.155-1.997 A |
| `current_a_chA`, `current_a_chB` | — | 1 | `aisvn2`, `maker-webhooks` | none | **unit unresolved**, see open questions |
| `power_w` | W | 1 | `aisvn` | ±2000 W | a real measurement there |
| `power_w` | W | 1 | `phumy2` | none | **not a measurement.** The hardware was never implemented. |
| `load_v` | V | 1 | `aisvn` | 0-60 V | the load/dump rail |
| `load_v` | — | 1 | `aisvn2` | 0-1 | a 0/1 logic level, not a voltage |
| `load1_v`, `load2_v` | — | 1 | `aisvn-solar` | none | **unit unresolved.** 0-1,598 and 0-3,026 cannot be volts. |
| `wind_v` | W | 1 / 0.001 | `aisvn`, `maker-webhooks` | 0-50 W | confirmed a power measurement, so charted at two stations. `aisvn-solar`'s is identically 0 and is hidden |
| `temp_c` | degC | 1 | `aisvn`, `phumy2`, `test` | 0-40 / 0-60 degC | all three write plain degrees. The ceiling is per station: `aisvn`'s probe stands in shadow, the other two are left at 60 |
| `lipo_v`, `lipo2_v` | V | 1 / 0.001 | varies | see below | 1S and 2S packs |
| `*_adc`, `nix_raw` | count | 1 | varies | none | uncalibrated, so no plausible range |
| `wifi_raw` | ms | 1 | `test` | none | the collector's `wifi_tx_ms`, a WiFi transmit time. Not a count, and no hardware ceiling exists to band it against, so none is asserted |
| `boot_count` | count | 1 | 5 stations | none | the logger's monotonic counter, reset by a reboot |
| `millis_ms` | ms | 1 | `voltage-phumy` | none | `millis()` since boot |
| `event` | TEXT | 1 | `solar-2020-05` | none | the string `solar_reading` |

**Every temperature is plain degrees.** 0.8 multiplied `aisvn.temp_c` and
`phumy2.temp_c` by 10 and `test.temp_c` by 100, and banded each in the multiplied
unit — 50-900 and 2149-3131 — so `32.5 degC` was stored as `325` and sat inside a
band that was wrong by the same factor. Two errors cancelling, the record looking
banded when it was only rescaled, and no flag firing anywhere. The sheets write
`32.5`, `24.2` and `29.47`.

**LiPo bands differ by pack, not by column name.** `aisvn.lipo_v` and
`maker-webhooks.lipo_v` are bounded 0-8.7 V and 0-4.35 V respectively, because
the first is bimodal with a 6.84 V plateau and banding it as a 1S cell flagged 20%
and 19% of each record. `aisvn2.lipo2_v` is a 2S pack and is bounded 0-8.7 V.

## `station_channels` and `station_layouts`

The catalog, as it landed in the database. Written verbatim from
`etl/catalog.py` by `etl.db.write_catalog`, so a query can answer "what does this
station collect, in what unit, banded how" without importing the Python package.
`public/data/stations.json` and the quality report are generated from these
tables, so there is one answer rather than three.

`station_channels` has one row per `(station_id, channel)`: `label`, `kind`,
`description`, `unit`, `raw_unit`, `scale`, `band_lo`, `band_hi`, `band_note`,
`stats`, `published`, `exclude_reason`, `exclude_note`, `is_counter`, `decimals`.

**`band_note` is not decoration.** `etl.audit` fails the build if a band fires on
more than `BAND_FIRE_FRACTION` (1%) of its channel's own record without one. A
band is a claim about a sensor, and a flag that fires on a fifth of a channel
cannot mark a contaminated aggregate — it is reporting a unit mismatch.

`station_layouts` has one row per `(station_id, n_columns)`: the raw header, the
channel order, and the file count. **Width alone identifies a layout.** The
archive contains exactly nine such pairs and no station has two layouts of the
same width, so a headerless file's column meanings come from here rather than
from whichever sibling happened to have a header first. An undeclared width is a
hard build failure, not a fallback.

`maker-webhooks` is the only station with two layouts (10 and 11 columns), and
the added `solar2` sits between `dump` and `LiPo` — so reading the 11-column
files with the 10-column order would put LiPo's value in `solar2` and wind's in
`dump_adc`, both banded, both plausible, both wrong.

## `flags`

Comma-separated, so one row can carry several. Two families are parameterised by
channel, `no_signal:<channel>` and `bad_window:<channel>`.

| Flag | Meaning |
|---|---|
| `out_of_range` | Outside **this station's** band for **this channel**, in the unit the value is stored in. Kept, always. |
| `sentinel` | The sheet wrote a placeholder. Stored as NULL; the raw cell is in `rejects`. |
| `no_signal:<channel>` | A `NULL_WINDOWS` entry. Stored as NULL; the raw cell is in `rejects`. |
| `bad_window:<channel>` | A `BAD_WINDOWS` entry. Kept and flagged: the sample is real even if the level is not. |
| `schema_misaligned` | The row's width did not match the catalog's layout for it. |
| `free_text` | The cell held prose. Recovered into `notes`. |

## `rejects`

Every cell that did not become a reading, with `sheet_row`, `raw_value` and
`reason`. **`reason` is a stable category, never a sentence.** This is not a style
preference: 0.8 stored the collector's ~300-character note as the reason on each
of the 220,069 cells a window nulled, which put one paragraph into 220,069 rows,
cost 80.6 MiB, made `rejects` larger than `readings`, and turned 4,403 duplicate
timestamps into 4,361 singleton groups in the report because the timestamp was
interpolated into the text. The category goes in the row; the prose lives once in
`etl/config.py` and the report republishes it.

| `reason` | Rows | Meaning |
|---|---:|---|
| `null_window` | 220,074 | A `NULL_WINDOWS` entry: the sheet logged a placeholder rather than a measurement |
| `sentinel` | 32,916 | A placeholder cell. 0.8 counted these in a flag and did not record them |
| `station_setup` | 4,121 | A `FILE_EXCLUSIONS` entry: the file is the collector's setup, not measurement |
| `duplicate_ts` | 2,250 | Absorbed by the primary key; the instant is in `raw_value` |
| `pre_reinstall` | 100 | A `ROW_EXCLUSIONS` entry: rows before a confirmed hardware reinstall |
| `repeated_header` | 2 | A header row repeated inside a headerless chunk |

A same-station duplicate timestamp lands here. The ~121,000 timestamps shared
*between* stations are separate instruments sampling the same instant, and both
are kept.

`notes` holds human prose found in a data cell or a side block, anchored to
`ts_utc` and the spreadsheet column. Currently 11. The side-block test is
structural — not a number, not a timestamp, not a header word from the catalog,
not a clock or a date — because a 20-character length threshold drops
`STROMAUSFALL!!`, which is 14 characters of genuine prose.

## `channel_stats`

One row per `(station, channel)`, measured on the real archive: `n_values`,
`n_nulls`, `first_ts_utc`, `last_ts_utc`, `min`, `max`, `mean`, `p01`, `p50`,
`p99`, `n_zero`, `n_sentinel`, `n_null_window`, `n_out_of_range`, `n_constant`.

This is the per-station documentation the project exists to produce, and it is
*measured* rather than asserted — so a band that starts firing on a third of a
record shows up as a number that moved. The percentiles are nearest-rank, which
matters: an interpolated percentile over a channel that is 99% one value invents
a value the channel never recorded.

## `readings_hourly` and `readings_daily`

Pre-aggregated so the website never scans a raw table. Both are keyed by
`station_id` because the tables are shared, and their value columns are the union
over stations of the published channels' statistics. `readings_daily` is derived
from `readings_hourly`, so the two cannot disagree — there is no second
aggregation to reach a different answer.

| Column | Meaning |
|---|---|
| `ts_utc` / `day` | The UTC hour, or the **local** calendar day |
| `ts_utc_day` (daily) | The UTC midnight of that day label. Not the same instant, and both are provided. |
| `n_samples` | Raw readings in the bucket; `n_hours` is how many hourly buckets the day was derived from |
| `n_out_of_range` | Samples in the bucket with any out-of-band value |
| `<channel>_<stat>` | `avg`, `min` or `max`, chosen per channel: a LiPo pack's health is its lowest reading, a power spike is a peak |
| `<channel>_n_oor` | Samples in the bucket **that channel's** band rejected |

**Per-channel counters, and why.** The row-level count cannot say *which* channel
broke. 0.8's worked example was `phumy2`: every sample of every hour was flagged,
so 30-of-30 carried no information, while the same hour's `power_w_n_oor` of 1 was
the entire finding. A channel with **no** band gets no counter — it would only
ever ship zeros, and a zero that means "not measured" is worse than no column.

**Counters are `min` and `max`, never a mean.** A mean across a reboot averages
two boot sessions into a number that never happened. A bucket whose min is 1
restarted; the max is how long it had been up.

**There is no `energy_wh`.** 0.8 computed it as
`avg_power * n_samples * 2 / 3600` for every station, which asserts a 2-minute
cadence and multiplies it by a power channel six of the eight stations do not
have — and for `phumy2` by a channel that is not a measurement. 731,885 readings
have a timestamp and nothing else.

## The site data

`public/data/` is 3.3 MiB, committed, and generated by `python -m etl export`.

| Path | Contents |
|---|---|
| `{station}/hourly/{year}.csv` | `ts, n_samples`, then that station's channels' statistics, then their `_n_oor` |
| `{station}/daily/{year}.csv` | The same, plus `n_hours` |
| `stations.json` | Every station, its channel list with unit/band/decimals/observed range, the excluded channels with reasons, its years and its table name |
| `metrics.json` | The same bands, keyed `"<station>.<channel>"` |
| `quality.json` | The whole report, for the inspector tab |

32 CSVs: 16 station-years at two resolutions.

**A station's CSV carries only the channels that station collects.** The header
is generated per station, so there is nothing to discover and nothing to keep in
step. 0.8 wrote 32 statistic columns into every CSV for all 16 station-years, so
`phumy2` shipped 27 columns of NULL and the browser had to work out which were
real. An empty column is allowed — a null window can empty one for a whole file
— and `scripts/check_frontend.mjs` requires it to have a documented reason.

An empty cell is a gap, never a 0. `0 W` at midnight is a measurement.

## Artefacts

| Path | In git? | Purpose |
|---|---|---|
| `data/processed/solardata.db` | no | Canonical store. ~85 MiB VACUUMed; a Release asset |
| `data/processed/quality_report.md` / `.json` | **yes** | The review artefact, per station |
| `public/data/` | **yes** | What the site fetches |
| `data/baseline.json` | **yes** | Expected counts, enforced by CI |
| `data/raw/**` | **yes** | The primary source of truth. Never edit. |

0.9 removed the committed `data/processed/parquet/` tree along with the stage
that wrote it and `scripts/parquet_manifest.py`. A fifth build stage with its own
manifest comparison and its own `pyarrow` dependency is not a fifth thing this
dataset needs; the SQLite file is the distribution channel.

## `data/baseline.json`

The expected output of a build, enforced by `python -m etl verify` and by
`data.yml`.

| Field | Guards against |
|---|---|
| `readings` | The headline. A sum over the eight station tables, because there is no longer one `readings` table to `COUNT(*)`. |
| `files`, `stations` | A raw file disappeared, or the registry changed. |
| `duplicate_ts`, `rejects`, `sentinels`, `null_windows` | The dedupe, the coercion or the windows changed behaviour. |
| `notes` | Note recovery changed; these are human context, not noise. |
| `excluded_files` | A file exclusion stopped matching. |
| `out_of_range` | **The number 0.9 exists for.** 631,252 → 40,393, of which 28,214 is two declared plateaus. A band firing on most of a record moves this, and so does a band the collector tightened. |
| `channel_stats` | The per-station measurement pass ran on every channel. |
| `hourly_buckets`, `daily_buckets` | The rollups changed shape. |
| `undeclared_layouts` | **Pinned to 0.** Non-zero means a raw file was ingested with no column meanings. |

`recorded.reason` is required and travels with the file, so a future diff explains
itself without re-running anything. Never loosen the file by hand: a failing
build is the point.

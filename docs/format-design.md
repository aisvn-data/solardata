# Why SQLite and CSV — and How Raw and Curated Telemetry are Layered

## The question

The raw archive is 364 XLSX files, 30.4 MiB, 731,981 readings, with
US-locale text timestamps, absent header rows, drifting column meanings, mixed
scaling, and sentinel values. What should it become so it can be processed
efficiently and queried without anyone having to re-derive those judgements?

## What was rejected, and why

**More CSV as the primary store.** The obvious move given spreadsheet exports.
It is the wrong answer because the dominant failure mode of this dataset is
*semantic ambiguity*, and CSV cannot carry semantics. A CSV column named
`battery` says nothing about whether the value is 12.6 V, 12597 mV, or the `29.12`
that no calibration explains. CSV also cannot distinguish `NULL` from an empty
string, cannot record where a row came from, and cannot carry a unit. Converting
XLSX → CSV directly launders ambiguity rather than resolving it.

**Parquet as the primary or committed interchange format.** In 0.8, Parquet was
maintained as an interchange partition tree (`data/processed/parquet/`). It was
retired in 0.9 because the extra build stage, separate `pyarrow` dependency, and
committed artifact overhead were not worth maintaining alongside a rich SQLite
database. SQLite already provides fast, typed, standard SQL access with zero
server setup.

**Long/EAV format** (`station, ts, metric, value`). Fully general — it absorbs
schema drift without new columns. Rejected on practicality: 731k rows becomes
over 5 million rows, every chart query becomes an expensive pivot, and per-metric
sparse patterns are much clearer in structured per-station tables.

**A pure time-series database server** (TimescaleDB, InfluxDB). Wrong shape of
tool. 731k rows is compact; there is no operational streaming load to justify
running a background database daemon, and it would make the archive harder to
inspect, audit, and distribute.

## What was chosen: The Two-Tier SQLite Architecture

### 1. `solardata_raw.db` — Verbatim unscaled raw store (Built in 0.11)

`solardata_raw.db` stores every reading exactly as logged in the XLSX sheets,
unscaled and uncurated:
- Eight raw tables: `r_aisvn`, `r_aisvn2`, `r_aisvn_solar`, `r_maker_webhooks`,
  `r_phumy2`, `r_solar_2020_05`, `r_test`, `r_voltage_phumy`.
- Integer epoch seconds `ts` as primary key: compact, fast numeric indexing.
- Unscaled values in the collector's original logged units (e.g. millivolts or
  microamps prior to catalog scaling).
- Provenance tables: `raw_source_files`, `raw_rejects`, `raw_notes`,
  `raw_manifest`, `raw_config_manifest`.

At ~25 MiB uncompressed (~8.5 MiB gzipped), `solardata_raw.db` is copied to
`public/data/solardata_raw.db` during export and loaded directly in the browser
using SQLite Wasm (`sql.js`). This enables:
- **Native Raw Resolution**: Browser queries 1–2 minute raw telemetry on demand.
- **Diff Inspector**: Side-by-side comparison between raw sensor inputs and
  curated outputs, along with interactive inspection of normalization steps.

### 2. `solardata.db` — Curated analytical store

The canonical curated store:
- Eight curated station tables: `s_aisvn`, `s_aisvn2`, `s_aisvn_solar`,
  `s_maker_webhooks`, `s_phumy2`, `s_solar_2020_05`, `s_test`,
  `s_voltage_phumy`.
- Confirmed hardware scales applied at ingest, so every channel is stored in its
  published, standard unit.
- Time-scoped corrections (e.g. hardware calibration offsets) declared in
  `etl/catalog.py` applied deterministically.
- `readings_hourly` and `readings_daily` rollups with sample counts and
  out-of-range breach counters.
- Provenance tracking (`source_files`, `rejects`, `notes`) and audit views.

At ~85 MiB after `VACUUM` (~104 MiB unvacuumed), it is published as a release
asset in GitHub Releases.

### 3. CSV and JSON for static web delivery

- `public/data/<station>/<resolution>/<year>.csv`: Pre-computed hourly and daily
  rollups per station and year. Lightweight and instant to fetch on initial page load.
- `stations.json`: Catalog metadata, channel definitions, confirmed scales,
  plausibility bands, and recorded statistics.
- `quality.json`: Audit metrics, file manifests, reject categorizations, and
  hardware notes.
- `diff_manifest.json` & `pipeline_config.json`: Curation diff summaries and
  channel pipeline step declarations.

## The layered result

```
data/raw/**.xlsx                  364 files, 30.4 MiB   immutable, committed
  |
  +-- data/processed/solardata_raw.db  ~25 MiB          raw verbatim store
  |     +-> public/data/solardata_raw.db                Wasm in-browser querying
  +-- data/processed/solardata.db      ~85 MiB          curated store (release asset)
  +-- data/processed/quality_report.*                   committed review artefact
  +-- data/baseline.json                                committed CI guard
  +-- public/data/                     ~3.3 MiB CSV     committed static web rollups
```

## Why SQLite in the browser?

In earlier versions (0.8–0.10), browsers only loaded pre-aggregated CSV files
because running SQL in a browser was considered too heavy or complex.
In 0.11, modern WebAssembly (`sql.js`) allows running SQLite directly in the user's
browser thread. Because `solardata_raw.db` is under 26 MiB, loading it asynchronously
takes only a moment and eliminates the need for generating thousands of individual
raw CSV slices. The user gets full, queryable 1–2 minute resolution telemetry
interactively on static hosting without a backend server.

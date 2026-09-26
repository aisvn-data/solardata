# Why SQLite, Parquet and CSV — in that order

## The question

The raw archive is 364 XLSX files, 30.4 MiB, 734,908 readings, with
US-locale text timestamps, absent header rows, drifting column meanings, mixed
scaling, and sentinel values. What should it become so it can be processed
later without anyone re-deriving those judgements?

## What was rejected, and why

**More CSV.** The obvious move given the input is a spreadsheet export, and the
project README suggests it. It is the wrong answer here specifically because the
dominant failure mode of this dataset is *semantic ambiguity*, and CSV cannot
carry semantics. A CSV column named `battery` says nothing about whether the
value is 12.6 V, 12597 mV, or the `29.12` that no calibration explains. CSV
also cannot distinguish `NULL` from empty string, cannot record where a row came
from, and cannot carry a unit. Converting xlsx → csv launders the ambiguity
rather than resolving it, and makes the next person re-do the same archaeology
with less information than we have now.

**Parquet as the only format.** Genuinely good at the analysis half — typed,
columnar, 6.8 MiB for the whole dataset, and it preserves NULL properly. But it
is poor at *auditing*: no natural place to record "this file had no header, and
I borrowed the layout from file X", and mutating or amending a partition means
rewriting it. And a browser cannot read it, so the website would still need a
CSV export beside it.

**Long/EAV format** (`station, ts, metric, value`). Fully general — it absorbs
any schema drift without new columns. Rejected on practicality: 734,908 rows
becomes ~5M, every chart query becomes a pivot, and the per-metric NULL pattern
is what makes the wide form compress so well in Parquet anyway.

**A pure time-series database** (TimescaleDB, InfluxDB). Wrong shape of tool.
735k rows is a rounding error; there is no operational load to justify a server,
and it would make the archive harder to diff in git and to hand to a
statistician.

## What was chosen

### SQLite as the canonical store

One file. Real types, so `NULL` survives and is distinguishable from `0`. A
foreign key from every reading to the file and row it came from, so any
decision can be traced. Views and rollups for the common queries. And
`regimes`/`metric_defs` as *tables*, which is the part that matters: the archive
contains at least one stretch where `battery` and `temp` are not volts and
degrees, and the schema has somewhere to record that without lying in a column
name.

At 329 MiB it is bigger than the Parquet output and much bigger than the CSV
exports, which is the cost of carrying provenance and rejected-row records for
all 734,908 readings. That is a trade this dataset should make.

### Parquet as the interchange format

Partitioned `station=X/year=Y`, zstd, dictionary-encoded. 6.8 MiB for the whole
dataset, ~23× smaller than the CSV equivalent, and `pyarrow`/duckdb/dask read
it with no custom parser. Row-group statistics make predicate pushdown work, so
"Iris-style" windowed queries stay fast. This is the format to hand to someone
doing statistics.

### CSV only for the website

1,127 daily rows across 14 station-years, 0.1 MiB total, written from the
pre-aggregated tables so the browser never touches 734,908 rows. Plus
`stations.json` for the station cards. Browsers read CSV; they do not read
SQLite or Parquet, and a GitHub Pages site cannot run a query server.

## The layered result

```
data/raw/**.xlsx                  364 files, 30.4 MiB   immutable, committed
  |
  +-- data/processed/parquet/       7.5 MiB  committed: interchange
  +-- data/processed/quality_report.*         committed: the review artefact
  +-- data/baseline.json                      committed: the guard CI enforces
  +-- data/processed/solardata.db   329 MiB   not committable (>100 MiB)
  +-- data/exports/                  0.1 MiB  for the browser
```

The Parquet output, the report and the baseline are committed, so the processed
data and the record of what the build should produce are both available from a
clone without running anything. `solardata.db` is not, because at 329 MiB it
exceeds GitHub's 100 MiB per-file limit for a git blob; it ships as a Release
asset instead. `data/exports/` stays out of git until the frontend reads it.

The archive is the only thing in the repository that cannot be regenerated, so
it is the only thing that must be committed at full size.

## Why the SQLite file is larger than the raw archive

`data/raw` is 30.4 MiB and `solardata.db` is 329.2 MiB, which looks alarming
until you account for the format. XLSX is a ZIP of XML:

| | |
|---|---|
| raw archive on disk (ZIP) | 30.4 MiB |
| the same files uncompressed | **251.4 MiB** (deflate ratio 12.1%) |
| `solardata.db` | 329.2 MiB |
| DB ÷ uncompressed XML | **0.63×** |

The database is **37% smaller than the raw XML text it came from**. The 5.2×
against the on-disk figure is entirely deflate.

Measured attribution of the 183 bytes per reading:

| Part | Size | Note |
|---|---|---|
| `readings` table | 128.5 MiB | includes the `WITHOUT ROWID` key tree |
| indexes and rollups | 29.7 MiB | `ix_readings_ts`, `ix_readings_file`, hourly, daily |
| `ts_local` | 15.9 MiB | derivable: `ts_utc + 7h` |
| `tz` | 14.0 MiB | constant per station, already on `stations` |
| key only | 25.8 MiB | `station_id` + `ts_utc` |

**The values are already 8-byte float64.** 23 of the 33 columns are `REAL`, and
`typeof()` on a stored reading confirms `real`. Four are `INTEGER`
(`boot_count`, `millis_ms`, and two provenance ids) and six are `TEXT`. So
there is no float-versus-int decision left to make on the measurements
themselves.

What is genuinely wasteful is the six `TEXT` columns, and 30 MiB of that is
`ts_local` and `tz` — derivable data stored 734,908 times.

### Why it is left alone for now

- Dropping both would take the file to ~128 MiB, which is **still over
  GitHub's 100 MiB limit**, so it would not make the database committable. The
  Release-asset arrangement stands either way.
- It is a breaking schema change for anyone who has started querying, and it
  would require re-recording the baseline.
- `ts_local` is used by `readings_daily` to define the local calendar day,
  which is not the same as a UTC day. Making it a computed column means
  encoding the UTC+07:00 assumption into the schema, which is worse than
  storing it.

If it is done later, the order of value is: `tz` first (pure redundancy, −14 MiB),
then `ts_utc` as an integer epoch if lexicographic sortability is not needed
(−10 MiB, and it costs readability), and `ts_local` last or never.

## The principle

The dataset's difficulty is not volume — 735k rows is small. It is that the same
column name can mean different things at different times, and half the files do
not say what their columns mean at all. A format is a good fit for this dataset
when it can hold *uncertainty* as first-class data: which file a value came
from, which header described it, how confident that mapping is, and what a human
still has to check. That is the property SQLite-with-metadata-tables buys, and
it is not something a spreadsheet-shaped format can express.

The baseline guard extends the same idea to the *build*: if a change to the
pipeline alters what gets ingested, the recorded counts stop matching and CI
fails. Without it, a refactor that quietly drops a channel would be invisible —
the unit tests would still pass, because they exercise fixtures rather than the
archive.

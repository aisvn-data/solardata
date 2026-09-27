# Why SQLite, Parquet and CSV — in that order

## The question

The raw archive is 364 XLSX files, 30.4 MiB, 730,914 readings, with
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
columnar, 7.5 MiB for the whole dataset, and it preserves NULL properly. But it
is poor at *auditing*: no natural place to record "this file had no header, and
I borrowed the layout from file X", and mutating or amending a partition means
rewriting it. And a browser cannot read it, so the website would still need a
CSV export beside it.

**Long/EAV format** (`station, ts, metric, value`). Fully general — it absorbs
any schema drift without new columns. Rejected on practicality: 730,914 rows
becomes ~5M, every chart query becomes a pivot, and the per-metric NULL pattern
is what makes the wide form compress so well in Parquet anyway.

**A pure time-series database** (TimescaleDB, InfluxDB). Wrong shape of tool.
731k rows is a rounding error; there is no operational load to justify a server,
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

At 166 MiB it is bigger than the Parquet output and much bigger than the CSV
exports, which is the cost of carrying provenance and rejected-row records for
all 730,914 readings. That is a trade this dataset should make.

### Parquet as the interchange format

Partitioned `station=X/year=Y`, zstd, dictionary-encoded. 7.5 MiB for the whole
dataset, ~23× smaller than the CSV equivalent, and `pyarrow`/duckdb/dask read
it with no custom parser. Row-group statistics make predicate pushdown work, so
"Iris-style" windowed queries stay fast. This is the format to hand to someone
doing statistics.

### CSV only for the website

Both resolutions, across every station in the registry: 15 station-years at
hourly (25,528 buckets, 5.2 MiB) and the same 15 at daily (1,181 buckets,
0.28 MiB), plus `stations.json` and `metrics.json`. Written from the
pre-aggregated tables so the browser never touches 730,914 rows. Browsers read
CSV; they do not read SQLite or Parquet, and a GitHub Pages site cannot run a
query server.

The hourly CSV is the reason the export is 5.6 MiB rather than the 0.3 MiB it was
before hourly rollups were published. That is the trade: the site's opening view
is an intraday curve, and 5.2 MiB of CSV is what that costs a static host.

## The layered result

```
data/raw/**.xlsx                  364 files, 30.4 MiB   immutable, committed
  |
  +-- data/processed/parquet/       7.5 MiB  committed: interchange
  +-- data/processed/quality_report.*         committed: the review artefact
  +-- data/baseline.json                      committed: the guard CI enforces
  +-- public/data/                  5.6 MiB  committed: what the site fetches
  +-- data/processed/solardata.db   166 MiB   not committable (>100 MiB)
```

The Parquet output, the report, the baseline and the site's data files are
committed, so the processed data and the record of what the build should produce
are both available from a clone without running anything. `solardata.db` is not,
because at 166 MiB it exceeds GitHub's 100 MiB per-file limit for a git blob; it
ships as a Release asset instead.

The export used to be a gitignored `data/exports/` "until the frontend reads it".
The frontend reads it, so it moved to `public/data/` where Vite serves it and the
site works from a plain clone.

The archive is the only thing in the repository that cannot be regenerated, so
it is the only thing that must be committed at full size.

## Why the SQLite file is larger than the raw archive

`data/raw` is 30.4 MiB and `solardata.db` is 165.6 MiB after the `VACUUM` in
`release.yml` (181.4 MiB as the ingest leaves it), which looks alarming until you
account for the format. XLSX is a ZIP of XML:

| | |
|---|---|
| raw archive on disk (ZIP) | 30.4 MiB |
| the same files uncompressed | **251.4 MiB** (deflate ratio 12.1%) |
| `solardata.db`, VACUUMed | 165.6 MiB |
| DB ÷ uncompressed XML | **0.66×** |

The database is **34% smaller than the raw XML text it came from**. The 5.2×
against the on-disk figure is entirely deflate. Gzipped — the form `release.yml`
actually publishes — it is 18.7 MiB, against 30.4 MiB of XLSX for the same rows.

The per-part attribution below was measured before `rejects.reason` stopped being
a sentence. That change removed 80.6 MiB of one ~300-character note repeated on
each of 220,074 rows, so `rejects` is now the fourth-largest table rather than the
second and the totals no longer add up to 166 MiB; the proportions are what
matters here, and `AGENTS.md` carries the current breakdown.

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
`ts_local` and `tz` — derivable data stored 730,914 times.

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

## `solardata_raw.db` — planned, not built

**No such file is produced by any command in this repository, and no stage
writes one.** If you have been looking for it after running `python -m etl all`,
it is not missing; it has never existed. The only mention of the name anywhere in
the tree until this section was an aside in `etl/config.py`, describing a layout
the current database is *heading towards* rather than one it has.

The full entry — what it would have to settle first, and the four questions that
are open — is in [`roadmap.md`](roadmap.md) under "Planned". In short: the
intention is a second store holding each cell as the sheet gave it, next to the
decision that was made about it. `config.py` names the specific gap:

That gap is real and still open. `metrics.METRICS` describes a band per *column*,
so it can only say one unit for every station that logs that column, and the two
exceptions have to be carried separately:

| Where | What it costs today |
|---|---|
| `config.CHANNEL_UNITS` | a per-station band override, applied in two places (`coerce_cell` and the aggregate) because applying it in one is the same bug one level up |
| `build_db._band_override` | the override resolved per row at flag time |
| `stations.json` → `channel_units` | the same override shipped to the browser, so the site divides `test`'s hundredths by 100 and the other seven stations' tenths by 10 |

Three copies of one idea, which is what the planned table would collapse into
one `(station_id, column)` key.

### What building it would have to settle first

Recorded rather than decided, in the spirit of `AGENTS.md`'s open questions:

1. **What "raw" means when a cell is a sentinel.** `-992` is an IFTTT
   disconnected-sensor marker. A verbatim layer would carry it as `-992` with a
   flag, and the question is whether the derived store reads *from* it or keeps
   being built from the sheet in parallel. The second is what happens now, and it
   is why the two can never be diffed against each other.
2. **Whether it is a second file or a second schema.** Two files means two
   places for the readings to disagree, which is a new failure mode rather than a
   smaller one. A schema in the same database costs nothing to keep consistent.
3. **What it is worth.** 730,914 rows already have a home, with provenance down
   to the file and sheet row (`rejects` for what did not make it, `metric_defs`
   for how each column was read, `regimes` for every proposed scale). The case
   for a verbatim layer is auditability — the ability to re-derive the derived
   store from something nobody has to trust. Whether anyone will do that is not
   established.
4. **Who asks for it.** The name appears in no issue, no commit message and no
   design note; it was introduced in `5f4ecc6` as a phrase for a future table.
   If a fuller description exists outside this repository, it should be pasted
   here before anyone starts building, because the four questions above are not
   answerable from what is written down.

Until then: one store, `solardata.db`, and the decisions recorded beside the data
they were made about. That is enough to disagree with any of it and see exactly
which rows it touched, which was the whole point of choosing it.

## The principle

The dataset's difficulty is not volume — 731k rows is small. It is that the same
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

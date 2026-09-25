# Why SQLite, Parquet and CSV — in that order

## The question

The raw archive is 364 XLSX files, 30.4 MiB, 735,004 readings, with
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
any schema drift without new columns. Rejected on practicality: 735,004 rows
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

At 158 MiB it is bigger than the Parquet output and much bigger than the CSV
exports, which is the cost of carrying provenance and rejected-row records for
all 735,004 readings. That is a trade this dataset should make.

### Parquet as the interchange format

Partitioned `station=X/year=Y`, zstd, dictionary-encoded. 6.8 MiB for the whole
dataset, ~23× smaller than the CSV equivalent, and `pyarrow`/duckdb/dask read
it with no custom parser. Row-group statistics make predicate pushdown work, so
"Iris-style" windowed queries stay fast. This is the format to hand to someone
doing statistics.

### CSV only for the website

1,127 daily rows across 14 station-years, 0.1 MiB total, written from the
pre-aggregated tables so the browser never touches 735,004 rows. Plus
`stations.json` for the station cards. Browsers read CSV; they do not read
SQLite or Parquet, and a GitHub Pages site cannot run a query server.

## The layered result

```
data/raw/**.xlsx                  364 files, 30.4 MiB   immutable, committed
  |
  +-- data/processed/solardata.db          158 MiB   canonical, query in place
  +-- data/processed/parquet/             6.8 MiB     interchange, for analysis
  +-- data/processed/quality_report.md                 the review artefact
  +-- data/exports/                      0.1 MiB     for the browser
```

Everything after `data/raw` is gitignored and rebuilt by `make build`. The
archive is the only thing in the repository that cannot be regenerated, so it is
the only thing that is committed.

## The principle

The dataset's difficulty is not volume — 735k rows is small. It is that the same
column name can mean different things at different times, and half the files do
not say what their columns mean at all. A format is a good fit for this dataset
when it can hold *uncertainty* as first-class data: which file a value came
from, which header described it, how confident that mapping is, and what a human
still has to check. That is the property SQLite-with-metadata-tables buys, and
it is not something a spreadsheet-shaped format can express.

# solardata

![GitHub License](https://img.shields.io/github/license/aisvn-data/solardata)
![GitHub Release](https://img.shields.io/github/v/release/aisvn-data/solardata)
![GitHub package.json version (branch)](https://img.shields.io/github/package-json/v/aisvn-data/solardata/main)

Analyze, clean and display collected solar data.

Six years of telemetry from eight solar stations in Nha Be and Phu My Hung, Ho
Chi City, Vietnam (May 2020 – September 2026): **731,885 readings across 8
stations**, forwarded to Google Sheets by IFTTT and exported as 364 XLSX files.

## The one idea

**A channel is a fact about a station, not a fact about a column name.**

The same input called `solar` is volts at AISVN #1 and millivolts at three other
stations. A plausible range is a claim about a sensor at a site, so it belongs to
a `(station, channel)` pair. Version 0.8 carried one range per *column name* and
tested raw millivolt cells against it, which marked **631,252 readings out of
range on arithmetic rather than on the hardware** — including all 416,088 of
`phumy2`'s readings, on a current sensor measuring a quarter of an amp, so the
site reported the whole station as broken.

It is now **3,695**, and every one of those is a question about a sensor.

## Status

| | |
|---|---|
| Raw archive | 364 files, 30.4 MiB, committed and immutable |
| ETL pipeline | `etl/`, `make build`, ~2 min, 159 tests (slowest 0.07 s) |
| Canonical store | `data/processed/solardata.db` — **eight tables, one per station** — 104 MiB as built / ~85 MiB VACUUMed / 18 MiB gzipped |
| Site data | `public/data/` (3.3 MiB, **committed**) — one CSV set per station |
| Quality report | `data/processed/quality_report.md` (**committed**) |
| Baseline guard | `data/baseline.json` (**committed**), enforced by CI |
| Website | Vite + React at `src/` — station explorer and data-quality inspector |
| CI | `.github/workflows/` — lint, test, full build, from-scratch rebuild, deploy, release |

## What each station has

| Station | Table | Readings | Coverage (UTC) | Charted channels |
|---|---|---:|---|---:|
| AISVN #1 | `s_aisvn` | 77,526 | 2020-06-15 → 2022-02-22 | 9 of 10 |
| AISVN #2 | `s_aisvn2` | 164,098 | 2020-06-18 → 2021-11-01 | 7 of 7 |
| AISVN Solar | `s_aisvn_solar` | 13,788 | 2020-05-21 → 2020-06-12 | 4 of 8 |
| Maker Webhooks | `s_maker_webhooks` | 8,535 | 2020-05-30 → 2020-06-12 | 9 of 10 |
| Phu My Hung #2 | `s_phumy2` | 416,088 | 2020-06-15 → 2026-09-27 | 5 of 6 |
| Solar bench | `s_solar_2020_05` | 12,920 | 2020-05-16 → 2020-06-15 | 3 of 4 |
| Test bench | `s_test` | 33,377 | 2020-07-05 → 2020-08-21 | 3 of 3 |
| Voltage calibration | `s_voltage_phumy` | 5,553 | 2020-07-04 → 2020-07-12 | 3 of 3 |

A station table holds only the channels that station collects, and a station's
CSV holds only its own columns. The 8 channels that are recorded but not charted
are listed on the site with the reason: a wired input nobody can explain
(`wind_v`), a power pin the hardware was never implemented on
(`phumy2.power_w`), two load rails whose unit nobody has established, and one text
label. Their values are all in the database.

## The archive is messy in ways that matter

305 of the 364 files have **no header row**. Several sheets carry redundant
side-by-side column blocks. The same column name means different things at
different times. `-992` is a disconnected-sensor placeholder rather than a
number, and `NULL` is never `0`. All of it is catalogued in
[`CHANGELOG.md`](CHANGELOG.md) and handled explicitly rather than smoothed over.

Two decisions do the heavy lifting. A headerless file's column meanings come
from `etl/catalog.py`, keyed on `(station, width)` — the archive has nine such
pairs and no station has two layouts of the same width, so the lookup is total
and an undeclared width is a loud build failure rather than a silent loss of 90%
of a station's measurements. And the confirmed unit conversions are applied once,
at ingest, so the database, the rollups, the CSVs and the browser all hold the
same number in the same unit.

## Quick start

```bash
pip install -r requirements.txt
python -m etl all          # ingest, aggregate, export, report, audit
python -m etl verify       # fail if the build is not what the baseline says

npm install
npm run dev                # http://localhost:5173/solardata/
npm run build              # -> dist/, deployed to GitHub Pages on push to main
```

`solardata.db` is not committed — at ~85 MiB VACUUMed it is over GitHub's 100 MiB
per-file limit. `release.yml` VACUUMs it and gzips it to an 18 MiB Release asset,
which is the form worth downloading. The site data and the quality report *are*
committed, so a clone is immediately useful.

## Website

Two tabs:

- **Explore** — pick a station, a year, a resolution and a date range. The
  channel picker lists only what that station collects, with the unit, the
  plausibility band and the range it actually recorded. A flagged value is drawn
  and ringed, never dropped, and the number beside it says how many samples in
  that bucket were outside the band.
- **Data quality** — the same report the pipeline commits, per station: every
  channel's start, stop, min, median, mean and max, its band, how many readings
  the band rejected, the windows and file exclusions with their reasons, and the
  band audit that makes a range firing on most of a record visible.

Both read static files from `public/data/`. A missing value is a gap in the line,
never a zero.

## Reading the data

`data/processed/solardata.db` is a plain SQLite file:

```sql
-- Eight tables, one per station, each with only that station's channels
SELECT station_id, table_name, n_readings, first_ts_utc, last_ts_utc FROM stations;
SELECT * FROM s_phumy2 WHERE ts_utc BETWEEN '2020-06-15' AND '2020-06-16' LIMIT 5;

-- What each channel is, in what unit, banded how
SELECT channel, unit, scale, band_lo, band_hi FROM station_channels
  WHERE station_id = 'phumy2';

-- What each channel actually recorded
SELECT channel, n_values, min, p50, mean, max, n_out_of_range
  FROM channel_stats WHERE station_id = 'phumy2';

-- Every cell that did not become a reading, and why
SELECT reason, COUNT(*) FROM rejects GROUP BY reason;
```

`python -m etl query "SELECT ..."` is a read-only shortcut for the same thing.

## Documentation

| | |
|---|---|
| [`AGENTS.md`](AGENTS.md) | The rules that are not negotiable, and why each exists |
| [`docs/data-dictionary.md`](docs/data-dictionary.md) | Every table, column, flag and unit |
| [`docs/data-sources.md`](docs/data-sources.md) | Where the data came from |
| [`docs/format-design.md`](docs/format-design.md) | Why the XLSX export is shaped the way it is |
| [`docs/roadmap.md`](docs/roadmap.md) | The open questions a human still has to answer |
| [`CHANGELOG.md`](CHANGELOG.md) | Every version, including findings about the archive |

## Licence

MIT. The raw archive under `data/raw/` is the collector's and is committed
unchanged; see `AGENTS.md` rule 1 for why it is never edited.

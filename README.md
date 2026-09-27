# solardata

![GitHub License](https://img.shields.io/github/license/aisvn-data/solardata)
![GitHub Release](https://img.shields.io/github/v/release/aisvn-data/solardata)
![GitHub package.json version (branch)](https://img.shields.io/github/package-json/v/aisvn-data/solardata/main)

Analyze, clean and display collected solar data.

Four years of telemetry from several solar stations in Nha Be and Phu My Hung,
Ho Chi City, Vietnam (May 2020 – February 2024): **730,914 readings across 8
stations**, forwarded to Google Sheets by IFTTT and exported as 364 XLSX files.

## Status

| | |
|---|---|
| Raw archive | 364 files, 30.4 MiB, committed and immutable |
| ETL pipeline | `etl/`, `make build`, ~3 min, 137 tests |
| Canonical store | `data/processed/solardata.db` (SQLite, 181 MiB as built / 166 MiB VACUUMed / 19 MiB gzipped — see below) |
| Interchange | `data/processed/parquet/` (7.5 MiB, **committed**) |
| Site data | `public/data/` (5.6 MiB, **committed**) |
| Quality report | `data/processed/quality_report.md` (**committed**) |
| Baseline guard | `data/baseline.json` (**committed**), enforced by CI |
| Website | Vite + React at `src/` — station explorer and data-quality inspector |
| CI | `.github/workflows/` — frontend, lint, test, full build, baseline |

730,914 readings across 8 stations, May 2020 → February 2024. The archive is
messy in ways that matter: 305 of the 364 files have **no header row**, several
sheets carry redundant side-by-side column blocks, the same column name means
different things at different times, and `-992` is a disconnected-sensor
sentinel rather than a number. All of that is catalogued in
[`CHANGELOG.md`](CHANGELOG.md) and handled explicitly by the pipeline.

The count moved once on purpose. It was 734,908 until the collector's account of
the `test` station: two of its files hold an 11-column solar layout that the
collector identifies as system setup rather than measurement, so both are excluded
and every row of them is recorded in `rejects` with reason `station_setup` — 6,144
cells that are gone from `readings` and still individually inspectable. What
remains of `test` is 33,377 readings from a 4-column `nix`/`temp`/`wifi` probe.

**`solardata.db` is not committed** — at 166 MiB VACUUMed it is over GitHub's
100 MiB per-file limit. `release.yml` VACUUMs it and gzips it to a **19 MiB**
Release asset, which is the form worth downloading. (It is actually *smaller* than the raw XML: the archive is
30.4 MiB only because XLSX is deflate-compressed, at 251 MiB uncompressed. See
[`docs/format-design.md`](docs/format-design.md#why-the-sqlite-file-is-larger-than-the-raw-archive).)
The Parquet output, the site data and the quality report *are* committed, so a
clone is immediately useful; the SQLite file ships as a Release asset.

## Website

```bash
npm install
npm run dev        # http://localhost:5173/solardata/
npm run build      # -> dist/, deployed to GitHub Pages by CI on push to main
```

Two tabs:

- **Explore** — pick a station, a year, a resolution and a date range; chart any
  combination of solar voltage, battery, power, temperature and energy. It opens
  on AISVN #1 in November 2021 at hourly resolution with the battery, solar and
  wind-turbine channels, which is 696 hourly buckets and the one view where the
  dawn and dusk of the solar curve are visible rather than averaged away.
- **Data quality** — the database inspector: what the build inferred, which
  readings are flagged and why, which scale changes are still unconfirmed, the
  collector's own margin notes, and the rejected cells.

**Day or Hour.** `Day` plots a mean over the day's hourly buckets; `Hour` plots a
mean over the ~30 readings inside that hour, so the solar curve has a dawn and a
dusk instead of being a flat average. Switching between them keeps the date
range: they are two samplings of the same days, and a From/To is a statement
about which days you want, not how finely to draw them. Hour is as fine as the
site goes: the archive's native cadence is 119 seconds, and those 730,914
unaggregated readings are the Parquet export — a download, not something a
browser fetches.

**All eight stations, including the two that are not solar.** `test` (a WiFi and
temperature probe) and `voltage-phumy` (an ADC calibration sheet) are in the
database, in the Parquet export and in the quality report, so they are published
and listed — under their own heading, with the reason they are not production
stated on the panel. They were hidden from the site until 0.7.2, which made the
one place a reader goes to look show six of the eight stations the project
documents.

No chart library: the chart is hand-rolled SVG, because a rollup needs a line
chart and a package would be ~100 kB of JavaScript to draw two paths.

The site reads static files from `public/data/`, written by
`python -m etl export`. Three rules it inherits from the pipeline:

- **A missing value is a gap in the line, never a zero.** `0 W` at midnight and
  "the sensor was disconnected" are different facts, and conflating them would
  make outages look like measurements.
- **A flagged value is drawn, ringed and listed — never removed.** A value
  outside the band the pipeline records for its channel is marked on the chart
  and enumerated underneath it, because implausible is not the same as wrong and
  only a human can adjudicate that. The bands arrive in
  `public/data/metrics.json`, copied verbatim from `etl/normalize/metrics.py`, so
  the site applies the same criterion the ingest did.
- **A channel's unit belongs to the station that logged it.** `test` records
  `temp_c` in hundredths of a degree and every other station in tenths, so the
  unit travels in `stations.json` as `channel_units` and the site divides by what
  the pipeline stored. Assume tenths everywhere and the probe's 28 °C afternoon
  is drawn as 280 °C.

## Pipeline

```bash
pip install -r requirements.txt   # or: make setup
make build                        # ingest -> regimes -> parquet -> export -> report
```

Or stage by stage:

```bash
python -m etl ingest     # XLSX -> SQLite   (the slow part)
python -m etl regimes    # detect unit-scale changes
python -m etl parquet    # SQLite -> partitioned Parquet
python -m etl export     # SQLite -> the CSV/JSON rollups the site fetches
python -m etl report     # write the data-quality report
python -m etl verify     # fail if the build != data/baseline.json
python -m etl query "SELECT station_id, COUNT(*) FROM readings GROUP BY 1"
```

`python -m etl export` writes a rollup for every station in the registry,
including the two that are not solar production. `--all-stations` only moves
`test` and `voltage-phumy` out of the site's separate group, which it does not
need in order to be published.

Read the committed Parquet without building anything:

```python
import duckdb
duckdb.sql("SELECT station_id, COUNT(*) FROM 'data/processed/parquet/*/*/*.parquet' GROUP BY 1")
```

If a data change is intentional, re-record the baseline with a reason so the
diff shows up in the pull request:

```bash
python -m etl verify --update-baseline --reason "corrected tz for phumy2a"
```

Read [`AGENTS.md`](AGENTS.md) before changing anything under `etl/` — it lists
the rules that exist to stop plausible-looking but wrong data, and how CI
enforces them. What is **not** finished is in
[`docs/roadmap.md`](docs/roadmap.md): the planned `solardata_raw.db`, the
questions only the collector can answer, and the known debt. Design rationale is
in [`docs/format-design.md`](docs/format-design.md), the schema in
[`docs/data-dictionary.md`](docs/data-dictionary.md), and the station histories
in [`docs/data-sources.md`](docs/data-sources.md).

## Purpose

I collected a lot of data with several solar stations in Nha Be and Phu My Hung
in 2020, and some data in 2021. The data sits mostly in Google Sheets. This repository has three goals:

- Convert the raw data into structured data — **done**, see `etl/`
- Analyse and structure the data, clean up, label — **done**, see `data/processed/`
- Visualize the data on a website, make it searchable — **done**, see `src/` and
  the committed rollups in `public/data/`

## Data sources

Most data was forwared with the service [IFTTT.com](https://ifttt.com/explore) that was free in 2020 and could easily have 5 different services available over webhooks. In time it was reduced to three, and then even this service was put behind a Pro subscription. But the data is in the Google Sheets - now lets extract it. We have

- IFTTT_test 0-18 2020-07-08 - 2020-09-26
- Voltage_phumy 0-2 2020-07-10 - 20220-07-13
- IFTTT_AISVN_Solar 0-6 2020-06-13
- IFTTT_phumy2 0-39 2020-06-18 - 2020-12-21
- IFTTT_phumy2 40-75 2021-02-15 - 2021-11-14
- IFTTT_phumy2 76-101, 0-12 2022-03-06 - 2022-12-18
- IFTTT_phumy2 13-97 2023-01-02 - 2023-11-24
- IFTTT_phymy2 0-2, 98-99 2024-01-14 - 2024-02-02
- IFTTT_aisvn 0-38 2020-06-18 - 2022-02-23
- IFTTT_aisvn2 0-78 2020-06-23 - 2021-11-01

From IFTTT:

- **aisvn** run 94437 times from 2020-09-06 to 2022-02-23
- **solar_reading** un 428698 times from 2020-09-06 to 2024-02-02

Archived older Applets:

- phumy
- test
- aisvn2

`test` is the one that needs a note. Its archive is 19 files in two layouts: two
of them carry an 11-column solar layout that the collector identifies as system
setup rather than measurement, and the other 17 a 4-column `nix`/`temp`/`wifi`
probe. The two are excluded **by file** — a date cut-off would not do, because
`IFTTT_test (1).xlsx` starts 2020-06-14 but uniquely contributes 4,120 readings
after 2020-07-01 — and all 6,144 of their rows are recorded in `rejects` with
reason `station_setup`, with their sheet row and timestamp, so "we did not ingest
this file" is defensible rather than silent. What remains is 33,377 probe
readings, 2020-07-05 to 2020-08-21, and they are published and chartable.

Note that the folder names under `data/raw` are archive chunks rather than
stations: `phumy2`, `phumy2a` and `phumy2b` are one continuous station. See
[`docs/data-sources.md`](docs/data-sources.md).

## Related repositories

- [aisvn-data/solarpower](https://github.com/aisvn-data/solarpower) Some tinkering and documenting of early steps in May 2020
- [kreier/solarmeter](https://github.com/kreier/solarmeter) Software repository for the 4 collectors of data 2020-2021
- [hviovn/solarmeter](https://github.com/hviovn/solarmeter) New updated solarmeter without the IFTTT service, but using a Cloudflare worker collect the data and store values every two minutes, and find historical data
- [kreier/solar](https://github.com/kreier/solar) Endpoint for different measuring stations and point to visualize historic solar data back to 2020, and temperature data back to 2015 in Hofkoh

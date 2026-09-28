# The pipeline, stage by stage

Every transformation between a cell in `data/raw/**` and a number on the chart,
and every declaration behind it. Generated from `etl/catalog.py` and the built
database, so it cannot describe a pipeline that is not the one running.

## The short version

A raw cell becomes a charted point through exactly six steps, and only the
fourth and fifth of them can change its number:

| # | Step | Can it change the number? |
|---|---|---|
| 1 | Find the file, block and row | no |
| 2 | Map the column to a (station, channel) | no |
| 3 | Parse the timestamp | no |
| 4 | Drop sentinels and free text -> NULL + a flag | **yes** |
| 5 | Apply the confirmed scale, then any declared correction | **yes** |
| 6 | Test the band -> keep the value, add a flag | no, flags only |

Then aggregate hourly, aggregate daily, export a CSV, and render. Steps 4-6
happen once, at ingest. Nothing downstream re-derives them.

## The stages

`python -m etl all` runs these five, in this order:

```
ingest      XLSX -> 8 station tables, 731,885 readings from 364 files
aggregate   readings_hourly (25,566) + readings_daily (1,184) + channel_stats (51)
export      public/data: 32 CSVs, stations.json, metrics.json, quality.json
report      data/processed/quality_report.md and .json
audit       check the build against the raw archive; fail loudly
```

`python -m etl verify` then compares the result to `data/baseline.json` and
fails if a single number moved.

Each stage is also runnable on its own, which is how you see what one of them
changed:

| command | what it does |
|---|---|
| `python -m etl all` | every stage below, in order, then `verify` |
| `python -m etl fresh` | delete the database and its `-wal`/`-shm` sidecars first, then `all` — `data_fresh.yml` from a terminal |
| `python -m etl ingest` | XLSX → the eight station tables, from scratch |
| `python -m etl aggregate` | the two rollups and `channel_stats` |
| `python -m etl export` | `public/data`, one CSV set per station |
| `python -m etl report` | the quality report, Markdown and JSON |
| `python -m etl audit` | the checks that need the real archive |
| `python -m etl verify` | fail if the build does not match the baseline |
| `python -m etl query "SELECT ..."` | ad-hoc read-only SQL |

`--raw-dir` and `--out-dir` redirect the input and the output. Common flags work
on either side of the stage: `python -m etl -q ingest` and
`python -m etl ingest -q` are the same command.

### 1. ingest

For each of the 364 XLSX files:

1. **Locate the data block.** Sheets carry a header, a blank run, and sometimes
   a second block. `etl/readers/xlsx.py` finds the block and yields rows.
2. **Resolve the layout.** 305 of 364 files have no header row, so the columns
   are resolved by `(station, sheet width)` against a declared `Layout` in the
   catalog. The archive has exactly 9 `(station, width)` pairs and all 9 are
   declared. **An undeclared width is a hard build failure**, never a fallback
   to a neighbouring file: 0.8 fell back and discarded 90% of the archive's
   measurements while ingesting every timestamp and reporting no problem.
3. **Parse the timestamp.** Column A is US free text (`July 14, 2020 at
   10:12AM`). `etl/readers/times.py` reads the fields with its own regex rather
   than `strptime`, because `%B` and `%p` resolve out of the C library's
   `LC_TIME` tables, which are not loaded on Windows. With `strptime` every cell
   in the archive fails to parse: zero readings and 738,358 rejects, in a shape
   that reads as a data problem and is a locale one.
4. **Reject the primary-key collision.** A duplicate timestamp is discarded by
   the key but still gets a `rejects` row with `reason = 'duplicate_ts'`.
5. **Coerce each cell** (below).
6. **Write the row** into that station's table, with `flags`.

### 2. What one cell goes through

`etl/build_db.py::_coerce`, in this order, and the order is the point:

```
empty                  -> NULL, no flag        a gap is not a zero
not a number           -> NULL, free_text      long prose becomes a note
NaN / inf              -> NULL, flags=sentinel
a declared sentinel    -> NULL, flags=sentinel
                         ...tested in the RAW unit, before the scale.
                         -992 * 0.001 = -0.992 is not a sentinel, it is a
                         plausible-looking number, so this is not negotiable.
apply scale            value * scale           once, confirmed against hardware
apply correction       value + n | value * n    only if a declared window covers ts
test band              outside -> KEEP + flag=out_of_range
                         the value is never discarded, only marked
```

### 3. aggregate

- `readings_hourly`: `AVG`/`MIN`/`MAX` per (station, hour), plus `n_samples` and
  one `<channel>_n_oor` counter per banded channel.
- `readings_daily`: the same, derived from the hourly rows.
- `channel_stats`: per (station, channel) min/max/mean/p01/p50/p99 and the
  null, zero, sentinel and out-of-range counts over the whole record.

The `<channel>_n_oor` counter is tested **with the band of that row's own
station**, keyed on `station_id`. Three stations publish a `battery_v` and each
has its own band; one expression cannot serve all eight branches of the union,
and using the first station's band flagged all 13,788 of aisvn-solar's readings
and none of the 7 that are actually out of band.

### 4. export

`public/data/<station>/<resolution>/<year>.csv`, one set per station, carrying
only that station's channels, plus `stations.json` (unit, scale, band, band_note
and observed range per channel), `metrics.json` and `quality.json`.

### 5. the browser

`src/data.js` parses the CSV header, so a station's channels come from the file
it was given. `classify` compares the plotted value against `stations.json`'s
band -- the same declaration, in the same unit, that the ingest applied. The
value on screen is the value in the database; there is no divisor.

---

## Every station, every channel

`raw` is the unit the sheet wrote, `->` the unit published. `scale` is applied
once, at ingest. A dash means the value is published as it was logged.

### `aisvn` -> `s_aisvn`

39 files, 77,526 readings, 2020-06-15 to 2022-02-22 UTC. 22 duplicate timestamps, 14,242 rejected cells.

Layout: 11 columns, 39 files.

| channel | kind | raw -> published | scale | band | stored range | notes |
|---|---|---|---|---|---|---|
| `solar_v` | voltage | - -> V | 1 | 0 .. 25 | 0 .. 29.8 (77,526) |  |
| `solar2_v` | voltage | - -> V | 1 | 0 .. 15 | 0 .. 19.5 (77,526) |  |
| `battery_v` | voltage | - -> V | 1 | 9 .. 16 | -0.99 .. 29.8 (77,526) |  |
| `current_a` | current | - -> A | 1 | 0 .. 3 | -6.21 .. 6.65 (77,526) | 2 correction window(s) |
| `power_w` | power | - -> W | 1 | 0 .. 50 | -19.65 .. 76.64 (77,525) | 2 correction window(s) |
| `load_v` | voltage | - -> V | 1 | 0 .. 20 | 0 .. 29.67 (77,526) |  |
| `wind_v` | power | - -> W | 1 | 0 .. 50 | 0 .. 29.8 (77,526) |  |
| `temp_c` | temperature | - -> degC | 1 | 0 .. 40 | 14 .. 63.3 (62,060) |  |
| `lipo_v` | voltage | - -> V | 1 | 0 .. 5 | 0.26 .. 6.84 (77,526) |  |
| `boot_count` | count | - -> count | 1 | none | 1 .. 21660 (77,516) | counter, never banded |

Open questions:

- battery_v reaches 29.8 V in 2020 and 17.9 V in 2021 against a confirmed 12 V lead-acid pack. A second pack, a mis-scaled input, or a band wrong for what is installed.
- lipo_v sits on a rail at exactly 6.84 V for 18% of the record, and solar2_v on a rail at exactly 19.5 V. Saturation, a divider, or a real ceiling -- the archive cannot tell them apart.
- After the 2020-08-24 fault the current channel's variation is only about 0.3 A and does not track solar, so the +6.6 A correction removes the sign error without restoring a usable signal. The power channel's does track solar, which is why one correction looks like a repair and the other looks like a patch.
- temp_c has more than one source, at least in December 2021. Which readings are which, and what the shaded ceiling really is, is asked of the collector.
- load_v's full scale is unresolved, and the rail's 0 V state changes behaviour on 2020-07-10 with nothing recorded to explain it.
- No readings at all between 2020-10-25 and 2020-11-04, and September 2020 has 12 readings. The collector confirmed the collector was down and no data was lost in the export, so there is nothing to fix.

### `aisvn2` -> `s_aisvn2`

79 files, 164,098 readings, 2020-06-18 to 2021-11-01 UTC. 48 duplicate timestamps, 10,402 rejected cells.

Layout: 8 columns, 79 files.

| channel | kind | raw -> published | scale | band | stored range | notes |
|---|---|---|---|---|---|---|
| `solar3_v` | voltage | mV -> V | 0.001 | 0 .. 30 | 0 .. 23.86 (164,097) |  |
| `battery2_v` | voltage | mV -> V | 0.001 | 9 .. 16 | 0.456 .. 15.36 (164,098) |  |
| `current_a_chA` | current | - -> - | 1 | <= 500 | -307 .. 1616 (153,770) |  |
| `current_a_chB` | current | - -> - | 1 | none | -500 .. 1618 (164,097) |  |
| `lipo2_v` | voltage | mV -> V | 0.001 | 0 .. 8.7 | 0.258 .. 7.097 (164,097) |  |
| `load_v` | digital | - -> - | 1 | 0 .. 1 | 0 .. 1 (164,097) |  |
| `boot_count` | count | - -> count | 1 | none | 1 .. 25372 (164,078) | counter, never banded |

Open questions:

- current_a_chA and current_a_chB step by roughly 200x between 2021-04 and 2021-10, with no recompile recorded at the boundary. Not a clean factor, so no scale is applied and no band is asserted.
- lipo2_v is a 2S pack and pins at 7.097 V. Whether the pin is the pack's own plateau or a saturated input is not recorded.

### `aisvn-solar` -> `s_aisvn_solar`

7 files, 13,788 readings, 2020-05-21 to 2020-06-12 UTC. 6 duplicate timestamps, 9 rejected cells.

Layout: 9 columns, 7 files.

| channel | kind | raw -> published | scale | band | stored range | notes |
|---|---|---|---|---|---|---|
| `solar_v` | voltage | mV -> V | 0.001 | 0 .. 60 | 0 .. 3.532 (13,788) |  |
| `battery_v` | voltage | mV -> V | 0.002 | 0 .. 5.1 | 0 .. 5.148 (13,788) |  |
| `load1_v` | voltage | - -> - | 1 | none | 0 .. 1598 (13,788) | excluded: unresolved_unit; not charted |
| `load2_v` | voltage | - -> - | 1 | none | 0 .. 3026 (13,788) | excluded: unresolved_unit; not charted |
| `lipo_v` | voltage | mV -> V | 0.001 | 0 .. 5 | 0 .. 3.532 (13,788) |  |
| `wind_v` | voltage | - -> - | 1 | none | 0 .. 0 (13,788) | excluded: constant; not charted |
| `dump_adc` | raw | - -> count | 1 | none | 0 .. 0 (13,788) | excluded: constant; not charted |
| `boot_count` | count | - -> count | 1 | none | 1 .. 10112 (13,785) | counter, never banded |

Open questions:

- solar_v maxes at 3,532 mV. A photovoltaic panel should reach 15-20 V open circuit, so either the input is not a panel or the station never saw a real panel voltage.
- load1_v and load2_v are recorded in an unestablished unit. Millivolts would make them plausible; nothing confirms it.
- lipo_v and solar_v both pin at 3,532, which is this applet's ADC rail.

### `maker-webhooks` -> `s_maker_webhooks`

5 files, 8,535 readings, 2020-05-30 to 2020-06-12 UTC. 47 duplicate timestamps, 8,400 rejected cells.

Layout: 10 columns, 3 files.
Layout: 11 columns, 2 files.

| channel | kind | raw -> published | scale | band | stored range | notes |
|---|---|---|---|---|---|---|
| `solar_v` | voltage | mV -> V | 0.001 | 0 .. 30 | -0.984 .. 34.034 (6,649) |  |
| `solar2_v` | voltage | mV -> V | 0.001 | 0 .. 15 | 0.735 .. 12.944 (2,583) |  |
| `battery_v` | voltage | mV -> V | 0.001 | 9 .. 16 | 0.757 .. 14.353 (8,529) |  |
| `current_a_chA` | current | - -> - | 1 | none | 784 .. 4095 (8,535) |  |
| `current_a_chB` | current | - -> - | 1 | none | 0 .. 3967 (8,535) |  |
| `load_v` | voltage | mV -> V | 0.001 | 0 .. 30 | 0.067 .. 12.242 (5,686) |  |
| `wind_v` | power | mV -> W | 0.001 | 0 .. 50 | -0.984 .. 14.686 (4,935) |  |
| `dump_adc` | raw | - -> count | 1 | none | 0 .. 5075 (8,535) |  |
| `lipo_v` | voltage | mV -> V | 0.001 | 0 .. 4.35 | 0 .. 6.6 (8,535) |  |
| `boot_count` | count | - -> count | 1 | none | 1 .. 4331 (8,531) | counter, never banded |

Open questions:

- The boot counter resets every 16 readings -- 526 times in 8,535 readings, 523 of them with no gap in sampling. A genuine reboot looks like that when the station keeps sampling, but a counter that moves that fast may be something else. Unexplained.
- current_a_chA and current_a_chB have no established unit.

### `phumy2` -> `s_phumy2`

206 files, 416,088 readings, 2020-06-15 to 2026-09-27 UTC. 63 duplicate timestamps, 220,224 rejected cells.

Layout: 7 columns, 206 files.

| channel | kind | raw -> published | scale | band | stored range | notes |
|---|---|---|---|---|---|---|
| `solar2_v` | voltage | mV -> V | 0.001 | 0 .. 30 | 0 .. 5.899 (195,954) |  |
| `current2_a` | current | mA -> A | 0.001 | -5 .. 5 | 0.155 .. 1.9968 (416,088) |  |
| `power_w` | power | - -> W | 1 | none | 0 .. 19877 (416,088) | excluded: not_measurement; not charted |
| `temp_c` | temperature | - -> degC | 1 | 0 .. 60 | 15.5 .. 80.6 (416,083) |  |
| `lipo2_v` | voltage | mV -> V | 0.001 | 2.5 .. 4.35 | 1.98 .. 4.196 (416,083) |  |
| `boot_count` | count | - -> count | 1 | none | 1 .. 71854 (416,061) | counter, never banded |

Open questions:

- solar2_v is a divider output after a bridge and load were fitted, so it is not a panel voltage and should not be charted as one. The bridge ratio is unknown.
- power_w is not a power measurement. The hardware was never implemented.

### `solar-2020-05` -> `s_solar_2020_05`

7 files, 12,920 readings, 2020-05-16 to 2020-06-15 UTC. 39 duplicate timestamps, 40 rejected cells.

Layout: 5 columns, 7 files.

| channel | kind | raw -> published | scale | band | stored range | notes |
|---|---|---|---|---|---|---|
| `event` | text | - -> - | 1 | none | text | excluded: text_label; not charted |
| `digital_adc` | raw | - -> count | 1 | none | 0 .. 4962 (12,919) |  |
| `voltage_adc` | raw | - -> count | 1 | none | 0 .. 9828 (12,920) |  |
| `lipo_v` | voltage | mV -> V | 0.001 | 2.5 .. 4.35 | 0 .. 13.637 (12,920) |  |

Open questions:

- lipo_v reaches 13.637 V, which is three times a 1S cell's ceiling.

### `test` -> `s_test`

18 files, 33,377 readings, 2020-07-05 to 2020-08-21 UTC. 12 duplicate timestamps, 4,133 rejected cells. Bench station: not solar production.

Layout: 4 columns, 18 files.

| channel | kind | raw -> published | scale | band | stored range | notes |
|---|---|---|---|---|---|---|
| `nix_raw` | raw | - -> count | 1 | none | 70 .. 102 (33,377) |  |
| `temp_c` | temperature | - -> degC | 1 | 0 .. 60 | 21.49 .. 31.31 (33,377) |  |
| `wifi_raw` | raw | - -> count | 1 | none | 1605 .. 56708 (33,377) |  |

Open questions:

- IFTTT_test (1).xlsx is excluded whole on the collector's word, because its 4,120 unique readings are dated 2020-07-01 to 07-08 and duplicate the probe data IFTTT_test (2).xlsx carries. The collector described it as an 11-column solar layout; the 0.8.0 raw repair removed that stretch and the file is now the same probe as its neighbours. The exclusion is still defensible on the overlap, but the reason it was given no longer describes the file, and that is a question for the collector.

### `voltage-phumy` -> `s_voltage_phumy`

3 files, 5,553 readings, 2020-07-04 to 2020-07-12 UTC. 2,013 duplicate timestamps, 2,013 rejected cells. Bench station: not solar production.

Layout: 4 columns, 3 files.

| channel | kind | raw -> published | scale | band | stored range | notes |
|---|---|---|---|---|---|---|
| `adc_raw` | raw | - -> count | 1 | none | 2107 .. 3127 (5,553) |  |
| `voltage_adc` | raw | - -> count | 1 | none | 1862 .. 2511 (5,553) |  |
| `millis_ms` | count | - -> ms | 1 | none | 2204 .. 6.85487e+08 (5,553) | counter, never banded |

Open questions:

- The ADC-to-voltage conversion this sheet calibrates is not recorded here, so neither channel can be published as a voltage.

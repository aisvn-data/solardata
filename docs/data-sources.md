# Data sources

## Provenance chain

```
Arduino collector (kreier/solarmeter family)
  -> IFTTT webhook          (free tier 2020, then Pro)
  -> Google Sheets          (appends one row per reading)
  -> manual XLSX export     (split at 2000 rows per file)
  -> data/raw/**.xlsx       364 files, 30.4 MiB, committed to git
  -> python -m etl all      the store under data/processed
  -> data/exports           what the website fetches
```

Nothing between the Sheets export and `data/raw` is scripted, so the file
numbering is a Google Sheets artefact, not a meaningful index.
`IFTTT_phumy2.xlsx` followed by `IFTTT_phumy2 (1).xlsx` … is export order.

## Stations

All coordinates are in Ho Chi Minh City, Vietnam. `tz` is UTC+07:00 year round
(no DST), which is why the offset never changes in `ts_utc`.

| `station_id` | Folders | Applet | Coverage (UTC) | Rows | Production |
|---|---|---|---|---:|---|
| `phumy2` | `phumy2`, `phumy2a`, `phumy2b` | `IFTTT_phumy2` | 2020-06-15 → 2024-02-01 | 415,117 | yes |
| `aisvn2` | `aisvn2` | `IFTTT_aisvn2` | 2020-06-18 → 2021-11-01 | 164,098 | yes |
| `aisvn` | `aisvn` | `IFTTT_aisvn` | 2020-06-15 → 2022-02-22 | 77,526 | yes |
| `test` | `test` | `IFTTT_test` | 2020-07-05 → 2020-08-21 | 33,377 | **no** |
| `aisvn-solar` | `AISVN_Solar` | `IFTTT_AISVN_Solar` | 2020-05-21 → 2020-06-12 | 13,788 | yes |
| `solar-2020-05` | `Solar_2020-05-16` | `IFTTT_Solar_2020-05-16` | 2020-05-16 → 2020-06-15 | 12,920 | yes |
| `maker-webhooks` | `Maker_Webhooks_Events` | `IFTTT_Maker_Webhooks_Events` | 2020-05-30 → 2020-06-12 | 8,535 | yes |
| `voltage-phumy` | `Voltage_phumy` | `Voltage_phumy` | 2020-07-04 → 2020-07-12 | 5,553 | **no** |

"Production" is not the same as "published". All eight are written to
`public/data/` and all eight are on the site; the two marked **no** are listed
under "Not solar production" and say what they are. The column answers "is this
solar production data", not "is this data withheld".

`test`'s 33,377 rows and its 2020-07-05 start date are the result of the
whole-file exclusion described below: its archive runs from 2020-06-12 and
carried 37,371 rows before the collector identified two of its nineteen files as
system setup.

### Notes on individual stations

**`phumy2` — Phu My Hung #2.** The main production record and the only
continuous one: June 2020 to February 2024 across three archive chunks. Header
is `time, solar2, current2, power, temp, LiPo2, boot`, so `solar_v` and
`battery_v` are legitimately NULL here — the channels are named `solar2` and
there is no battery channel. `current2` reads 210–270 against a ±50 A band, and
this station is entirely in the `out_of_range` flag for `current2_a`; that is the
flag doing its job, not a data error.

The scale for `current2` is **not settled and the band cannot decide it**:
÷1000 gives 0.21–0.27 A, which is too small for a station that also reports
~12 V on the panel, while ÷100 gives 2.1–2.7 A, which is plausible for it. Both
fall inside ±50 A, so the flag is 100% either way and only the collector can say
which is the unit. `AGENTS.md` open question 3 assumes milliamps; that is an
assumption, not a finding, and is now recorded as one. `lipo2_v` here is
unambiguous: 1,980–4,200 against a 2.5–4.35 V band is millivolts, and ÷1000 puts
415,107 of 415,112 readings inside it.

**`aisvn` — AISVN #1.** The most interesting station and the one with the most
unresolved questions. Eleven channels, and the column *meaning* changes during
the record even though the header text does not (`CHANGELOG.md` F3). Carries
the 9 German-language lab annotations that explain the October 2020 hardware
work (F4).

**`aisvn2` — AISVN #2.** Eight channels, values in millivolts throughout
(`battery2` = 12597 is 12.597 V). No temperature channel. Ends 2021-11-01.

**`aisvn-solar` and `maker-webhooks` — archived applets.** Both superseded by
`aisvn` in June 2020 and cover only a few weeks. All 7 `AISVN_Solar` and all 5
`Maker_Webhooks_Events` files have header rows, and both carry a redundant
second column block. `maker-webhooks` is the main source of the `-992` sentinel
(8,366 cells). Because these are in raw millivolts while `aisvn` is in volts,
they must not be charted together without applying a confirmed regime.

**`solar-2020-05` — bench sheet.** May–June 2020. The only station with an
`event` column (always `solar_reading`), so it records *that* a reading was
triggered rather than only the values. Partly out of production.

**`test` — not a solar station.** Mixes at least three unrelated schemas at the
same column count: a solar channel block, a `time, nix, temp, wifi` probe, and a
`millis` counter. Contributes 2,390 of the 4,403 duplicate timestamps. The solar
block is excluded **by file** — the collector's account of it is system setup
rather than measurement — which is why the row count is 33,377 rather than
37,371. Published and chartable, as a probe.

**`voltage-phumy` — calibration bench.** Not a station; it is a
characteristic of the ADC→voltage conversion. Columns are `time, raw, voltage,
millis()`, plus a three-block side table and the discharge-test annotation
(`CHANGELOG.md` F7). The unit mapping is unconfirmed. Published and chartable,
as a calibration record.

## IFTTT service history

From the project README: five services in early 2020, reduced to three, then
moved behind a Pro subscription. The applet run counts recorded there are
94,437 for `aisvn` (2020-09-06 → 2022-02-23) and 428,698 for `solar_reading`
(2020-09-06 → 2024-02-02). The ingested `phumy2` count of 415,117 is close to
the latter and reconciles once the 4,403 chunk-boundary duplicates are removed,
which supports the reading that the archive is essentially complete.

## External references

- `kreier/solarmeter` — the 2020–2021 collector firmware for four stations.
- `hviovn/solarmeter` — later collector, no IFTTT, Cloudflare Worker backend.
- `aisvn-data/solarpower` — the original May 2020 experiments.
- `aisvn-data/solar`, `kreier/solar` — endpoints and visualisation of the
  historic record, including pre-2020 temperature data.

These are the places to look to *resolve* the open questions in `AGENTS.md`
rather than guess at them. The firmware in particular would settle the
`load_v` and `aisvn (25).xlsx` channel meanings.

# Data quality report -- solardata 0.11.0

**731,981 readings** from 8 raw files across 8 stations. 25,579 hourly and 1,187 daily rollup buckets. 254,327 rejected cells, 0 recovered notes, 0 files excluded whole.

Every station is one table in `solardata.db`, holding only the channels that station collects. Bands belong to a (station, channel) pair, not to a column name, and are tested in the unit the value is stored in.

## Stations

| Station | Table | Readings | Coverage (UTC) | Channels | Published |
|---|---|---:|---|---:|---:|
| AISVN #1 | `s_aisvn` | 77,622 | 2020-06-15 to 2022-02-22 | 10 | 10 |
| AISVN Solar (archived applet) | `s_aisvn_solar` | 13,788 | 2020-05-21 to 2020-06-12 | 8 | 4 |
| AISVN #2 | `s_aisvn2` | 164,098 | 2020-06-18 to 2021-11-01 | 7 | 7 |
| Maker Webhooks (archived applet) | `s_maker_webhooks` | 8,535 | 2020-05-30 to 2020-06-12 | 10 | 10 |
| Phu My Hung #2 | `s_phumy2` | 416,088 | 2020-06-15 to 2026-09-27 | 6 | 5 |
| Solar bench (2020-05-16 sheet) | `s_solar_2020_05` | 12,920 | 2020-05-16 to 2020-06-15 | 4 | 3 |
| Test bench | `s_test` | 33,377 | 2020-07-05 to 2020-08-21 | 3 | 3 |
| Phu My Hung voltage calibration | `s_voltage_phumy` | 5,553 | 2020-07-04 to 2020-07-12 | 4 | 4 |

## AISVN #1 (`aisvn`)

11-channel logger. The applet was recompiled twice and the collector converted the sheet at source, so the whole record is volts and amps with no scale window to apply -- except for two hardware faults the collector dates exactly: the current channel gained a permanent offset at 2020-08-24 18:42 local, and the power channel's output was inverted and four times too large from the same instant. Both are declared as corrections rather than absorbed into the channel.

77,622 readings in `s_aisvn`, 2020-06-15T06:10:00Z to 2022-02-22T21:42:00Z, timezone Asia/Ho_Chi_Minh, applet `IFTTT_aisvn`.

### What it collects

| Channel | Unit | Band | n | min | p01 | median | mean | p99 | max | zeros | out of range | shown |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| `solar_v` | V | 0 to 25 | 77,622 | 0 | 0 | 4.6 | 8.029 | 29.06 | 29.8 | 36,121 | 1,687 | yes |
| `solar2_v` | V | 0 to 15 | 77,622 | 0 | 0 | 4.123 | 6.032 | 19.5 | 19.5 | 31,460 | 14,107 | yes |
| `battery_v` | V | 9 to 16 | 77,622 | -0.99 | 10.59 | 12.79 | 13.34 | 29.2 | 29.8 | 0 | 1,813 | yes |
| `current_a` | A | 0 to 3 | 77,622 | -6.21 | -0.01 | 0.12 | 0.3356 | 2.7 | 6.66 | 620 | 1,486 | yes |
| `power_w` | W | 0 to 50 | 77,621 | -22.4 | 0 | 0.2112 | 7.689 | 40.53 | 76.64 | 36,307 | 907 | yes |
| `load_v` | V | 0 to 20 | 77,622 | 0 | 0 | 11.88 | 8.138 | 29.45 | 29.73 | 28,408 | 1,687 | yes |
| `wind_v` | W | 0 to 50 | 77,622 | 0 | 0 | 0 | 1.572 | 29.79 | 29.8 | 67,765 | 0 | yes |
| `temp_c` | degC | 0 to 40 | 62,156 | 14 | 16 | 32.5 | 32.31 | 37 | 63.3 | 0 | 156 | yes |
| `lipo_v` | V | 0 to 5 | 77,621 | 0.26 | 0.34 | 4.07 | 16.32 | 585 | 1,361.00 | 0 | 15,460 | yes |
| `boot_count` | count | -- | 76,254 | 1 | 40 | 5,647.00 | 6,711.14 | 19,699.00 | 21,660.00 | 0 | 0 | yes |

### Flags

| Flag | Rows |
|---|---:|
| `out_of_range` | 17,848 |
| `sentinel` | 14,116 |
| `bad_window:solar_v` | 1,641 |
| `bad_window:battery_v` | 1,641 |
| `bad_window:temp_c` | 1,641 |
| `no_signal:temp_c` | 1,359 |

### Rejected cells

| Reason | Cells |
|---|---:|
| `sentinel` | 14,118 |
| `null_window` | 1,359 |

### Open questions

- battery_v reaches 29.8 V in 2020 and 17.9 V in 2021 against a confirmed 12 V lead-acid pack. A second pack, a mis-scaled input, or a band wrong for what is installed.
- lipo_v sits on a rail at exactly 6.84 V for 18% of the record, and solar2_v on a rail at exactly 19.5 V. Saturation, a divider, or a real ceiling -- the archive cannot tell them apart.
- After the 2020-08-24 fault the current channel's variation is only about 0.3 A and does not track solar, so the +6.6 A correction removes the sign error without restoring a usable signal. The power channel's does track solar, which is why one correction looks like a repair and the other looks like a patch.
- temp_c has more than one source, at least in December 2021. Which readings are which, and what the shaded ceiling really is, is asked of the collector.
- load_v's full scale is unresolved, and the rail's 0 V state changes behaviour on 2020-07-10 with nothing recorded to explain it.
- No readings at all between 2020-10-25 and 2020-11-04, and September 2020 has 12 readings. The collector confirmed the collector was down and no data was lost in the export, so there is nothing to fix.

## AISVN Solar (archived applet) (`aisvn-solar`)

Superseded by aisvn. May 2020 only. 9 columns, of which two load rails have no established unit and two inputs never move.

13,788 readings in `s_aisvn_solar`, 2020-05-21T02:52:00Z to 2020-06-12T05:04:00Z, timezone Asia/Ho_Chi_Minh, applet `IFTTT_AISVN_Solar`.

### What it collects

| Channel | Unit | Band | n | min | p01 | median | mean | p99 | max | zeros | out of range | shown |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| `solar_v` | V | 0 to 60 | 13,788 | 0 | 0 | 2.236 | 2.626 | 3.532 | 3.532 | 222 | 0 | yes |
| `battery_v` | V | 0 to 5.1 | 13,788 | 0 | 0 | 4.322 | 4.126 | 4.978 | 5.148 | 284 | 7 | yes |
| `load1_v` | -- | -- | 13,788 | 0 | 305 | 553 | 552.1 | 969 | 1,598.00 | 1 | 0 | **no** |
| `load2_v` | -- | -- | 13,788 | 0 | 0 | 216 | 327.7 | 2,225.00 | 3,026.00 | 711 | 0 | **no** |
| `lipo_v` | V | 0 to 5 | 13,788 | 0 | 3.532 | 3.532 | 3.531 | 3.532 | 3.532 | 1 | 0 | yes |
| `wind_v` | -- | -- | 13,788 | 0 | 0 | 0 | 0 | 0 | 0 | 13,788 | 0 | **no** |
| `dump_adc` | count | -- | 13,788 | 0 | 0 | 0 | 0 | 0 | 0 | 13,788 | 0 | **no** |
| `boot_count` | count | -- | 13,785 | 1 | 37 | 3,099.00 | 3,945.12 | 9,968.00 | 10,112.00 | 0 | 0 | yes |

### Recorded but not shown

- **`load1_v`** (Load 1) -- *unresolved_unit*. Excluded from the site and the CSVs. The header says load_1 and 0.8 stored it as volts, which would have published 1,598 V. The millivolt reading is likely but unconfirmed, and publishing a guessed unit is the same error as publishing a guessed scale. The values are in the database. If the collector confirms millivolts this becomes scale 0.001 and unit V.
- **`load2_v`** (Load 2) -- *unresolved_unit*. Excluded from the site and the CSVs, for the same reason as load 1: 3,026 V is not a rail and 3.026 V is a guess.
- **`wind_v`** (Wind) -- *constant*. Excluded because it never varies, not because of what it measures. etl.audit re-checks the constancy on every build, so this exclusion cannot go stale.
- **`dump_adc`** (Dump ADC) -- *constant*. Excluded because it never varies. etl.audit re-checks the constancy on every build.

### Flags

| Flag | Rows |
|---|---:|
| `out_of_range` | 7 |
| `sentinel` | 3 |

### Rejected cells

| Reason | Cells |
|---|---:|
| `sentinel` | 3 |

### Open questions

- solar_v maxes at 3,532 mV. A photovoltaic panel should reach 15-20 V open circuit, so either the input is not a panel or the station never saw a real panel voltage.
- load1_v and load2_v are recorded in an unestablished unit. Millivolts would make them plausible; nothing confirms it.
- lipo_v and solar_v both pin at 3,532, which is this applet's ADC rail.

## AISVN #2 (`aisvn2`)

8-channel logger recording millivolts throughout. The LiPo channel is a 2S pack, not the 1S cell the archive once assumed.

164,098 readings in `s_aisvn2`, 2020-06-18T09:45:00Z to 2021-11-01T02:49:00Z, timezone Asia/Ho_Chi_Minh, applet `IFTTT_aisvn2`.

### What it collects

| Channel | Unit | Band | n | min | p01 | median | mean | p99 | max | zeros | out of range | shown |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| `solar3_v` | V | 0 to 30 | 164,097 | 0 | 0 | 2.414 | 6.278 | 16.69 | 23.86 | 5,817 | 0 | yes |
| `battery2_v` | V | 9 to 16 | 164,098 | 0.456 | 9.588 | 12.22 | 12.18 | 13.13 | 15.36 | 0 | 1,070 | yes |
| `current_a_chA` | -- | -- to 500 | 153,770 | -307 | -70 | -3 | 32.87 | 1,230.00 | 1,616.00 | 12,052 | 2,915 | yes |
| `current_a_chB` | -- | -- | 164,097 | -500 | -267 | 64 | 50.6 | 196 | 1,618.00 | 7 | 0 | yes |
| `lipo2_v` | V | 0 to 8.7 | 164,097 | 0.258 | 0.3 | 7.097 | 7.002 | 7.097 | 7.097 | 0 | 0 | yes |
| `load_v` | -- | 0 to 1 | 164,097 | 0 | 0 | 0 | 0.1887 | 1 | 1 | 133,138 | 0 | yes |
| `boot_count` | count | -- | 164,078 | 1 | 80 | 6,793.00 | 7,950.16 | 23,721.00 | 25,372.00 | 0 | 0 | yes |

### Flags

| Flag | Rows |
|---|---:|
| `sentinel` | 10,348 |
| `out_of_range` | 3,983 |

### Rejected cells

| Reason | Cells |
|---|---:|
| `sentinel` | 10,348 |

### Open questions

- current_a_chA and current_a_chB step by roughly 200x between 2021-04 and 2021-10, with no recompile recorded at the boundary. Not a clean factor, so no scale is applied and no band is asserted.
- lipo2_v is a 2S pack and pins at 7.097 V. Whether the pin is the pack's own plateau or a saturated input is not recorded.

## Maker Webhooks (archived applet) (`maker-webhooks`)

Superseded by aisvn. June 2020 only. Two sheet widths, 10 and 11 columns, and the 11-column files carry a second solar input.

8,535 readings in `s_maker_webhooks`, 2020-05-30T06:03:00Z to 2020-06-12T15:21:00Z, timezone Asia/Ho_Chi_Minh, applet `IFTTT_Maker_Webhooks_Events`.

### What it collects

| Channel | Unit | Band | n | min | p01 | median | mean | p99 | max | zeros | out of range | shown |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| `solar_v` | V | 0 to 30 | 6,649 | -0.984 | 0 | 11.97 | 8.289 | 14.71 | 34.03 | 1,901 | 17 | yes |
| `solar2_v` | V | 0 to 15 | 2,583 | 0.735 | 0.735 | 0.735 | 3.615 | 12.73 | 12.94 | 0 | 0 | yes |
| `battery_v` | V | 9 to 16 | 8,529 | 0.757 | 4.095 | 11.93 | 11.84 | 13.94 | 14.35 | 0 | 138 | yes |
| `current_a_chA` | -- | -- | 8,535 | 784 | 820 | 998 | 1,023.56 | 2,885.00 | 4,095.00 | 0 | 0 | yes |
| `current_a_chB` | -- | -- | 8,535 | 0 | 956 | 1,009.00 | 1,092.40 | 2,732.00 | 3,967.00 | 4 | 0 | yes |
| `load_v` | V | 0 to 30 | 5,686 | 0.067 | 2.096 | 3.3 | 4.923 | 11.9 | 12.24 | 0 | 0 | yes |
| `wind_v` | W | 0 to 50 | 4,935 | -0.984 | 0.419 | 0.947 | 1.615 | 11.56 | 14.69 | 1 | 22 | yes |
| `dump_adc` | count | -- | 8,535 | 0 | 559 | 714 | 2,088.15 | 4,413.00 | 5,075.00 | 6 | 0 | yes |
| `lipo_v` | V | 0 to 4.35 | 8,535 | 0 | 0.735 | 4.044 | 3.411 | 4.176 | 6.6 | 65 | 4 | yes |
| `boot_count` | count | -- | 8,531 | 1 | 81 | 2,434.00 | 2,440.23 | 4,246.00 | 4,331.00 | 0 | 0 | yes |

### Flags

| Flag | Rows |
|---|---:|
| `sentinel` | 4,039 |
| `out_of_range` | 178 |

### Rejected cells

| Reason | Cells |
|---|---:|
| `sentinel` | 8,342 |

### Open questions

- The boot counter resets every 16 readings -- 526 times in 8,535 readings, 523 of them with no gap in sampling. A genuine reboot looks like that when the station keeps sampling, but a counter that moves that fast may be something else. Unexplained.
- current_a_chA and current_a_chB have no established unit.

## Phu My Hung #2 (`phumy2`)

7-channel logger and by far the longest record: 416,088 readings from June 2020 to September 2026. Split across three archive folders because Google Sheets split the sheet at 2000 rows; they are one instrument.

416,088 readings in `s_phumy2`, 2020-06-15T02:29:00Z to 2026-09-27T07:10:00Z, timezone Asia/Ho_Chi_Minh, applet `IFTTT_phumy2`.

### What it collects

| Channel | Unit | Band | n | min | p01 | median | mean | p99 | max | zeros | out of range | shown |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| `solar2_v` | V | 0 to 30 | 195,954 | 0 | 0 | 0 | 0.6787 | 5.061 | 5.899 | 121,830 | 0 | yes |
| `current2_a` | A | -5 to 5 | 416,088 | 0.155 | 0.2 | 0.231 | 0.2305 | 0.283 | 1.997 | 0 | 0 | yes |
| `power_w` | W | -- | 416,088 | 0 | 0 | 0 | 0.1912 | 0 | 19,877.00 | 416,083 | 0 | **no** |
| `temp_c` | degC | 0 to 60 | 416,083 | 15.5 | 20.8 | 29.1 | 29.49 | 47.2 | 80.6 | 0 | 466 | yes |
| `lipo2_v` | V | 2.5 to 4.35 | 416,083 | 1.98 | 3.219 | 3.922 | 3.879 | 4.146 | 4.196 | 0 | 5 | yes |
| `boot_count` | count | -- | 416,061 | 1 | 58 | 11,430.00 | 15,735.93 | 67,607.00 | 71,854.00 | 0 | 0 | yes |

### Recorded but not shown

- **`power_w`** (Power) -- *not_measurement*. Excluded from the site and the CSVs, and the reason is that it is not a measurement rather than that it is implausible. 0.8 published it banded at +/-2,000 W, where five readings were flagged and 415,112 zeros were presented as a power curve. The values are in the database, with no band, because a band would assert a quantity the pin does not carry. Open question 3.

### Flags

| Flag | Rows |
|---|---:|
| `no_signal:solar2_v` | 220,069 |
| `out_of_range` | 471 |
| `sentinel` | 87 |

### Rejected cells

| Reason | Cells |
|---|---:|
| `null_window` | 220,069 |
| `sentinel` | 87 |

### Open questions

- solar2_v is a divider output after a bridge and load were fitted, so it is not a panel voltage and should not be charted as one. The bridge ratio is unknown.
- power_w is not a power measurement. The hardware was never implemented.

## Solar bench (2020-05-16 sheet) (`solar-2020-05`)

Bench sheet rather than an instrument, but it logged a real panel for a month alongside aisvn's first weeks, so it is kept as a station. The only source of the 'event' label in the archive.

12,920 readings in `s_solar_2020_05`, 2020-05-16T15:52:00Z to 2020-06-15T02:51:00Z, timezone Asia/Ho_Chi_Minh, applet `IFTTT_Solar_2020-05-16`.

### What it collects

| Channel | Unit | Band | n | min | p01 | median | mean | p99 | max | zeros | out of range | shown |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| `event` | -- | -- | 12,920 | -- | -- | -- | -- | -- | -- | 0 | 0 | **no** |
| `digital_adc` | count | -- | 12,919 | 0 | 0 | 0 | 534.6 | 4,095.00 | 4,962.00 | 8,388 | 0 | yes |
| `voltage_adc` | count | -- | 12,920 | 0 | 0 | 450 | 1,660.06 | 9,789.60 | 9,828.00 | 721 | 0 | yes |
| `lipo_v` | V | 2.5 to 4.35 | 12,920 | 0 | 1.094 | 3.64 | 3.499 | 4.152 | 13.64 | 118 | 148 | yes |

### Recorded but not shown

- **`event`** (Event) -- *text_label*. The only text channel in the archive, and the only channel with no statistics. It is stored on every row so a reader can see which applet produced a reading, and it is not charted because there is nothing to plot.

### Flags

| Flag | Rows |
|---|---:|
| `out_of_range` | 148 |
| `sentinel` | 1 |

### Rejected cells

| Reason | Cells |
|---|---:|
| `sentinel` | 1 |

### Open questions

- lipo_v reaches 13.637 V, which is three times a 1S cell's ceiling.

## Test bench (`test`)

Not a solar station. What remains after the collector's exclusion is a 4-column nix/temp/wifi probe, July to August 2020. Published under 'Not solar production' and labelled with this note wherever it appears.

33,377 readings in `s_test`, 2020-07-05T04:40:00Z to 2020-08-21T06:36:00Z, timezone Asia/Ho_Chi_Minh, applet `IFTTT_test`.

### What it collects

| Channel | Unit | Band | n | min | p01 | median | mean | p99 | max | zeros | out of range | shown |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| `nix_raw` | count | -- | 33,377 | 70 | 77 | 93 | 90.23 | 99 | 102 | 0 | 0 | yes |
| `temp_c` | degC | 0 to 60 | 33,377 | 21.49 | 23.64 | 28.55 | 27.7 | 30.39 | 31.31 | 0 | 0 | yes |
| `wifi_raw` | ms | -- | 33,377 | 1,605.00 | 1,623.00 | 4,041.00 | 4,630.19 | 9,334.00 | 56,708.00 | 0 | 0 | yes |

### Open questions

- IFTTT_test (1).xlsx is excluded whole on the collector's word, because its 4,120 unique readings are dated 2020-07-01 to 07-08 and duplicate the probe data IFTTT_test (2).xlsx carries. The collector described it as an 11-column solar layout; the 0.8.0 raw repair removed that stretch and the file is now the same probe as its neighbours. The exclusion is still defensible on the overlap, but the reason it was given no longer describes the file, and that is a question for the collector.

## Phu My Hung voltage calibration (`voltage-phumy`)

A bench calibration of the ADC-to-voltage conversion, not a station. It carries the hand-made discharge summary in its second column block and the lab's own annotations, which are recovered into notes.

5,553 readings in `s_voltage_phumy`, 2020-07-04T21:08:00Z to 2020-07-12T18:03:00Z, timezone Asia/Ho_Chi_Minh, applet `Voltage_phumy`.

### What it collects

| Channel | Unit | Band | n | min | p01 | median | mean | p99 | max | zeros | out of range | shown |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| `adc_raw` | count | -- | 5,553 | 2,107.00 | 2,166.00 | 2,495.00 | 2,506.53 | 3,019.00 | 3,127.00 | 0 | 0 | yes |
| `voltage_adc` | count | -- | 5,553 | 1,862.00 | 1,909.00 | 2,155.00 | 2,158.42 | 2,481.00 | 2,511.00 | 0 | 0 | yes |
| `millis_ms` | ms | -- | 5,553 | 2,150.00 | 5,895,329.00 | 342,461,573.00 | 342,533,468.17 | 678,611,242.00 | 685,364,571.00 | 0 | 0 | yes |
| `solar_v` | V | -- | 5,553 | 10.61 | 10.88 | 12.28 | 12.3 | 14.14 | 14.31 | 0 | 0 | yes |

### Open questions

- The ADC-to-voltage conversion is applied as solar_v in the consolidated sheet.

## Band audit

How much of each channel's own record its own band rejects. A band that fires on more than 1.0% of a channel is not detecting contamination, it is reporting a unit mismatch; every one above the line carries a note saying why the fire is the finding.

| Station | Channel | Unit | Band | n | Out of range | Share | Note |
|---|---|---|---|---:|---:|---:|---|
| `aisvn` | `lipo_v` | V | 0 to 5 | 77,621 | 15,460 | 19.917% | 0-5 V, the collector's figure |
| `aisvn` | `solar2_v` | V | 0 to 15 | 77,622 | 14,107 | 18.174% | 15 V, below this channel's own 19.5 V saturation, so the rail is flagged -- and that is 18.2% of the record, which is why it needs a note rather than silence |
| `aisvn` | `battery_v` | V | 9 to 16 | 77,622 | 1,813 | 2.336% | Fires on 1,717 of 77,526 readings (2.2%) and that is the finding, not a false positive: the bank reaches 29.8 V in 2020 and 17.9 V in 2021, which is a second p… |
| `aisvn` | `solar_v` | V | 0 to 25 | 77,622 | 1,687 | 2.173% | 0.8 used 0-60 V for every station's solar_v, which flagged nothing here because 0.8's scale never reached the stored value |
| `aisvn` | `load_v` | V | 0 to 20 | 77,622 | 1,687 | 2.173% | 20 V, below the rail |
| `aisvn` | `current_a` | A | 0 to 3 | 77,622 | 1,486 | 1.914% | 0-3 A, the collector's figure for this panel |
| `aisvn2` | `current_a_chA` | -- | -- to 500 | 153,770 | 2,915 | 1.896% | Upper limit 500, from the collector; no lower bound, because 55% of this channel's readings are negative and a floor at zero would flag more than half the reco… |
| `maker-webhooks` | `battery_v` | V | 9 to 16 | 8,529 | 138 | 1.618% | Fires on 138 of 8,529 readings (1.6%), all of them below 9 V, down to 0.757 V |
| `aisvn` | `power_w` | W | 0 to 50 | 77,621 | 907 | 1.168% | 0-50 W, the collector's figure |
| `solar-2020-05` | `lipo_v` | V | 2.5 to 4.35 | 12,920 | 148 | 1.145% | Fires on 148 of 12,920 readings (1.15%): 139 below 2.5 V and 9 above 4.35 V, the largest of which is 13.637 V |
| `aisvn2` | `battery2_v` | V | 9 to 16 | 164,098 | 1,070 | 0.652% | Fires on 1,070 of 164,098 readings (0.65%), all of them below 9 V |
| `maker-webhooks` | `wind_v` | W | 0 to 50 | 4,935 | 22 | 0.446% | 0-50 W, the collector's estimate for this generator |
| `maker-webhooks` | `solar_v` | V | 0 to 30 | 6,649 | 17 | 0.256% | Fires on 17 of 6,649 readings: a -984 mV reading and readings up to 34.0 V |
| `aisvn` | `temp_c` | degC | 0 to 40 | 62,156 | 156 | 0.251% | 0-40 degC, the collector's ceiling for a probe in shadow at this site |
| `phumy2` | `temp_c` | degC | 0 to 60 | 416,083 | 466 | 0.112% | Fires on the readings above 60 degC, reaching 80.6 |
| `aisvn-solar` | `battery_v` | V | 0 to 5.1 | 13,788 | 7 | 0.051% | 0-5.1 V, the documented 0-5,100 mV ceiling |
| `maker-webhooks` | `lipo_v` | V | 0 to 4.35 | 8,535 | 4 | 0.047% | Upper bound at the 1S cell ceiling of 4.35 V, lower bound at 0 rather than 2.5 V |
| `phumy2` | `lipo2_v` | V | 2.5 to 4.35 | 416,083 | 5 | 0.001% | Fires on 5 of 416,083 readings, all of them below 2.5 V |
| `aisvn` | `wind_v` | W | 0 to 50 | 77,622 | 0 | 0.000% | 0-50 W, the collector's estimate for this generator |
| `aisvn2` | `solar3_v` | V | 0 to 30 | 164,097 | 0 | 0.000% | Recorded 0-23,860 mV, 16.7 V at the 99th percentile |
| `aisvn2` | `lipo2_v` | V | 0 to 8.7 | 164,097 | 0 | 0.000% | Banded 0-8.7 V, the full range of a 2S pack, rather than the 2.5-4.35 V of a 1S cell |
| `aisvn2` | `load_v` | -- | 0 to 1 | 164,097 | 0 | 0.000% | 0-1 is the channel's whole domain, so nothing is flagged. |
| `aisvn-solar` | `solar_v` | V | 0 to 60 | 13,788 | 0 | 0.000% | Nothing is flagged |
| `aisvn-solar` | `lipo_v` | V | 0 to 5 | 13,788 | 0 | 0.000% | 0-5 V, the collector's figure, which replaces the 2.5-4.35 V of a 1S cell |
| `maker-webhooks` | `solar2_v` | V | 0 to 15 | 2,583 | 0 | 0.000% | 0-15 V, the collector's figure |
| `maker-webhooks` | `load_v` | V | 0 to 30 | 5,686 | 0 | 0.000% | Recorded 0.067-12.242 V |
| `phumy2` | `solar2_v` | V | 0 to 30 | 195,954 | 0 | 0.000% | Nothing is flagged, and nothing should be: 0.8 nulled 220,069 readings over 2022-10 to 2024-01 on the grounds that the input was disconnected, and then banded… |
| `phumy2` | `current2_a` | A | -5 to 5 | 416,088 | 0 | 0.000% | This is the single largest correction in 0.9 |
| `test` | `temp_c` | degC | 0 to 60 | 33,377 | 0 | 0.000% | Recorded 21.49-31.31 degC, median 27.70 |

## Rejected cells by reason

`reason` is a stable category, never a sentence. The prose for each window
is published once, above and in `config.py`.

| Reason | Cells |
|---|---:|
| `null_window` | 221,428 |
| `sentinel` | 32,899 |

## Windows whose values were nulled

- **phumy2** `solar2_v`, 2022-10-01T00:00:00Z to 2024-01-01T00:00:00Z, 220,069 cells. collector: the collector input was disconnected and the sheet logged a flat 0.0 V for the whole window. Every one of the 220,069 readings is that placeholder, so there is no measurement to store. The window is half-open: the channel reads normally again from 2024-01-01.
- **aisvn** `temp_c`, 2020-06-15T06:10:00Z to 2020-06-17T04:14:00Z, 1,359 cells. collector: a pre-recompile stretch where the sheet logged 200 as a stand-in for 'no temperature'. The window ends at 04:14 UTC precisely so the 114 genuine tenths that follow survive. Nothing is nulled today: the collector resolved the placeholder at source, and the window is kept because the archive still carries the boundary.

## Windows whose values were kept and flagged

- **aisvn** `solar_v`, `battery_v`, `temp_c`, 2020-10-23T00:00:00Z to 2020-10-30T00:00:00Z. the collector was being reconfigured: the station kept sampling while the applet and its wiring were being changed, so the levels in this window describe a half-built logger. Kept and flagged, because the samples are real; the good window starts at valid_to.

## Exclusions

- **file `test/IFTTT_test.xlsx`** -- collector: the test station's 11-column solar layout is system setup, not measurement. 0.8.0's raw repair deleted this file from the archive, so the entry no longer matches anything; it is kept so the repair is visible here rather than silently forgotten
- **file `test/IFTTT_test (1).xlsx`** -- collector: the 4,120 readings this file uniquely contributes are dated 2020-07-01 to 07-08, after the probe had already begun and after IFTTT_test (2).xlsx had started covering the same stretch, so they are not measurements. 0.8 described this file as an 11-column solar layout; the raw repair removed that stretch, and what is left is 4,121 rows of the same nix/temp/wifi probe the neighbouring files carry. The exclusion is kept on the collector's word and the overlap is still there; the description is corrected
- **rows before 102 of `aisvn/IFTTT_aisvn (25).xlsx`** -- pre-reinstall window; collector confirmed rows from here on are usable

## Sampling interval

| Station | Readings | Median gap | p90 gap |
|---|---:|---:|---:|
| `aisvn` | 77,622 | 120 s | 120 s |
| `aisvn2` | 164,098 | 120 s | 120 s |
| `aisvn-solar` | 13,788 | 120 s | 120 s |
| `maker-webhooks` | 8,535 | 60 s | 60 s |
| `phumy2` | 416,088 | 120 s | 120 s |
| `solar-2020-05` | 12,920 | 120 s | 120 s |
| `test` | 33,377 | 120 s | 120 s |
| `voltage-phumy` | 5,553 | 120 s | 120 s |


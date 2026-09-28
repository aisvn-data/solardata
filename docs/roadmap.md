# Roadmap — what is not finished

Nothing here is a task with an owner. It is the list of things this project
knows it has not done, kept in one place so that "is X planned?" has an answer
that is not a grep through twelve open questions in `AGENTS.md`.

Three kinds of entry, and the distinction matters:

- **Planned** — someone decided it should be built, and the reason is written
  down.
- **Blocked** — cannot be finished without something from outside the
  repository, usually an answer from the collector. These are *not* bugs, and
  closing them in code would be inventing data.
- **Known debt** — the thing works, and it works in a way that is worse than it
  could be. Recorded so nobody discovers it as a surprise.

Last reviewed: 0.9.0.

---

## Blocked: the collector has to answer these

Each of these is a question about the hardware, not about the data. The archive
records the numbers; nothing records what the numbers mean. Every one of them is
carried as an `open_questions` entry on the station it belongs to in
`etl/catalog.py`, and `tests/test_catalog.py` asserts that no station's list is
empty — so one cannot be quietly dropped.

### phumy2

- **`power_w` is not a power measurement.** 415,112 of 415,117 readings are
  exactly 0 and the other five are 13,810-19,877 W. The collector describes the
  hardware as never having been implemented on that pin. The channel is in the
  database, unbanded, and not on the chart. *What is the pin connected to?*
- **`solar2_v` is a divider output after a bridge and load were fitted.** The
  level steps from about 5,000 mV to about 1,200 mV somewhere in 2022, so after
  that date it is not a panel voltage and should not be charted as one. *What is
  the bridge ratio?* The values are published in volts because the millivolt
  scale is confirmed, but the label is wrong and the label is the thing that is
  wrong.
- **The `temp_c` window** (2022-10 to 2024-01) is nulled because the collector
  says the input was disconnected. Was the sheet fixed afterwards, or is the
  channel still writing placeholders?

### aisvn

- **`battery_v` reaches 29.8 V in 2020 and 17.9 V in 2021** against a 12 V
  lead-acid car battery the collector confirmed. 1,717 readings, 2.2% of the
  channel, and the 9-16 V band is right — so the readings are the question.
  *A second pack? A mis-scaled input? A band wrong for what is installed?* In
  0.8 this was invisible: the temperature was rescaled by 10 and banded in the
  rescaled unit, so the record looked banded when it was only multiplied.
- **`lipo_v` is bimodal**: 3.98-4.13 V for most of the record and a flat 6.84 V
  for 14,107 readings. 6.84 V is about two cells. *Is this a 2S pack for part of
  the record, a scaled input, or a hardware change?* Nothing in the archive
  records a recompile at the change.
- **`load_v`'s 0 V state changes behaviour on 2020-07-10** — before that date the
  rail never reads 0, after it reads 0 with no load present. *What changed?* The
  rail's full scale is also unrecorded, which is why the band is the generic
  0-60 V rather than a measured ceiling.
- **No readings at all between 2020-10-25 and 2020-11-04**, and September 2020
  has 12. The collector confirmed the collector was down and that no data was
  lost in the export, so there is nothing to fix. Recorded so nobody spends time
  on it.

### aisvn2

- **`current_a_chA` and `current_a_chB` step by roughly 200×** between 2021-04
  and 2021-10, where channel A pins at exactly 1240 for 1,207 readings. The
  factor is not a clean power of ten, so no scale is applied and no band is
  asserted — the values are stored and published, with no unit. *Which is right:
  the pre-April scale or the post?* This is the one channel where 0.8's detector
  proposed a scale it could not confirm, and the proposal was never applied. It
  is now an open question rather than a proposal the detector re-makes on every
  build.
- **`lipo2_v` is a 2S pack and pins at 7.097 V** for 161,790 of 164,097 readings.
  *Is the pin the pack's own plateau or a saturated input?*

### aisvn-solar

- **`solar_v` maxes at 3,532 mV** where a photovoltaic panel should reach 15-20 V
  open circuit. Either the input is not a panel or the station never saw a real
  panel voltage. *What was on the collector input?*
- **`load1_v` and `load2_v` are in an unestablished unit.** They record 0-1,598
  and 0-3,026, which cannot be volts; at that magnitude a load rail is implausible
  by three orders, and millivolts would put them at a plausible 0-1.6 V. *Are
  they millivolts?* If the collector confirms it, this becomes `scale = 0.001` and
  `unit = V` and two channels return to the chart.
- **`lipo_v` and `solar_v` both pin at 3,532**, which is this applet's ADC rail
  (0.8's `CLIP_CANDIDATES` entry). *Is the pin a full pack or a saturated input?*
- **`test/IFTTT_test (1).xlsx` is excluded on a stale reason.** The exclusion is
  still defensible — its 4,120 unique readings are dated 2020-07-01 to 07-08 and
  duplicate the probe data `IFTTT_test (2).xlsx` carries — but 0.8 described the
  file as an 11-column solar layout, and the 0.8.0 raw repair removed that
  stretch, so what is left is the same probe as its neighbours. *Does the
  collector still want it excluded?*

### maker-webhooks

- **`lipo_v` is bimodal** too: a 0.735 V plateau for about a fifth of the record
  and 4.04-4.13 V for the rest. 0.735 V is below a 1S cell's floor. *Is that a
  disconnected pack, a different cell, or an input problem?*
- **The boot counter resets every 16 readings** — 526 times in 8,535, and 523 of
  those resets have no gap in sampling. A genuine reboot looks like that when the
  station keeps sampling, but a counter that moves that fast may be something
  else. *What is it?*
- **`current_a_chA` and `current_a_chB` have no established unit.** Milliamps would
  put the median at 1.0 A, which is plausible. Nothing confirms it, so no scale
  and no band. 0.8 tested them against a ±50 A band, which flagged all 8,535
  readings — the band was asserting an amplitude the archive cannot support.

### wind_v, everywhere

Three stations wire a `wind` input and it logs. `aisvn` records 0-13.3 V hourly
in 2021, up to 29.8 V in 2020, and exactly 0 for all of 2022.
`maker-webhooks` reaches 14.7 V. `aisvn-solar`'s is exactly 0 for all 13,788 of
its readings. 12,784 V is not a generator output, and 0 for a whole record is not
a measurement either.

*What is the input connected to?* Until then the channel is stored, unbanded, and
not on the chart — and `etl.audit` re-checks `aisvn-solar`'s on every build, so if
it ever starts moving the site says the exclusion is stale rather than quietly
carrying a flat line at zero.

---

## Known debt

The thing works, and it works in a way that is worse than it could be.

- **The rollup tables carry a lot of NULL.** They are shared across stations, so
  their value columns are the union of every station's published channels, and
  `phumy2` leaves 59 of the 64 empty. The CSVs the site fetches are per station
  and carry none of it, so nothing user-visible is wrong — but the database is
  bigger than it needs to be for the same reason. It is 104 MiB and nobody has
  complained, so this is a note rather than a task.

- **`solar2_v` is published as volts when it is a divider output.** See above.
  The scale is confirmed (the sheet writes millivolts) and the *label* is wrong
  after the bridge was fitted. Fixing the label is not a scale change, and no
  amount of analysis of the archive can establish the ratio.

- **The temperature band is 0-60 °C for all three stations.** 585 readings
  exceed it, reaching 63.3 °C at `aisvn` and 80.6 °C at `phumy2`. Ho Chi City
  ambient does not reach either, so the flag is right — but a wider band would
  flag nothing and a narrow one would be a guess. 60 °C is the documented
  ceiling for a shaded probe in the tropics and is recorded as such.

- **The hour bucket is UTC.** A reader asking for "November" gets a range bounded
  by the first and last *local* day with samples, bucketed by *UTC* hour. At
  UTC+07:00 the first and last hour of a month are partial, and a reader looking
  at the dawn of the first morning sees one sample in it. The alternative is a
  local-time bucket, which makes the primary key offset-dependent and the rollups
  much harder to reason about. The site states the choice rather than hiding it.

- **`test/IFTTT_test.xlsx` has a dead exclusion entry.** 0.8.0's raw repair
  deleted the file, so the entry matches nothing. It is kept in `FILE_EXCLUSIONS`
  so the repair is visible in the file that records the decision, and an exclusion
  that matches nothing rejects nothing.

- **`FLAG_MISALIGNED` is computed but should be unreachable.** A headerless
  file's layout is keyed on its own measured width, so the width a block reports
  and the width the catalog declares cannot differ for an ingested file. It is
  written anyway, because it is the first sign that `detect_block` and the
  catalog have drifted, and a check that cannot fail is not a check.

---

## Planned

- **A band per (station, channel) is manual, and always will be.** The 0.8
  detector proposed 11 windows and 2 stayed unconfirmed forever. 0.9 deleted it.
  What is wanted instead is the collector confirming the open questions above,
  at which point each answer becomes one more entry in `etl/catalog.py` with a
  note explaining it. A detector cannot answer any of these questions; it can only
  notice that a number looks unusual, and it reported 631,252 of those.

- **The `n_samples`-only CSV leaves the `n_hours` column out of the hourly
  files.** It is derivable — an hourly bucket is one hour wide by construction —
  and including it would have been a second number to keep in step. If a
  resolution other than `daily`/`hourly` is ever added, the conditional in
  `build_exports.csv_header` is the place that has to change.

- **`docs/format-design.md` still describes the 0.8 export.** The reasoning about
  why Google Sheets chunks at 2,000 rows and why the side blocks exist is
  unchanged; the column lists in it are not.

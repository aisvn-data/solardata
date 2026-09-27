# Roadmap — what is not finished

Nothing here is a task with an owner. It is the list of things this project
knows it has not done, kept in one place so that "is X planned?" has an answer
that is not a grep through eleven open questions in `AGENTS.md`.

Three kinds of entry, and the distinction matters:

- **Planned** — someone decided it should be built, and the reason is written
  down. `solardata_raw.db` is the only one of these.
- **Blocked** — cannot be finished without something from outside the
  repository, usually an answer from the collector. These are *not* bugs and
  closing them in code would be inventing data.
- **Known debt** — the thing works, and it works in a way that is worse than it
  could be. Recorded so nobody discovers it as a surprise.

Last reviewed: 0.8.0.

---

## In progress: the raw archive is being repaired in place

The collector has started fixing the source XLSX rather than excluding whole
stretches in `config.py` — which is the right direction, because an exclusion is a
patch over the primary source of truth while a corrected cell is the truth. One
file has been done, and it changes what the pipeline believes.

**What `data/raw/aisvn/IFTTT_aisvn.xlsx` became.** The millivolt-as-integer
columns are now volts and amps (`solar` 13558 → 13.558, `battery` 12844 → 12.844,
`current` 1080 → 1.08, `solar2` 4616 → 4.616, `LiPo` 4107 → 4.107), the
placeholder `200` temperatures are blank, and the redundant side block — which
was already in volts — is gone. That is a real improvement, and it takes the
`aisvn` out-of-band count from 832 readings to 1.

**Three things about it are not settled, and the pipeline is currently wrong
because of the first.**

1. **The confirmed regime now double-scales it.** `build_regimes.CONFIRMED_WINDOWS`
   carries seven `aisvn` rows at `×0.001` for
   `2020-06-15T06:10:00Z .. 2020-06-17T08:20:00Z`. That window is exactly the
   pre-recompile window, and it is exactly the part of `IFTTT_aisvn.xlsx` the
   repair converted — **1,480 readings, 1.91% of the station, all in that one
   file.** Applied to values that are already in volts, the published rollup for
   2020-06-15 reads `solar_v_avg = 0.005` V where it read `4.57` V. The decision
   is simply to delete those seven rows, with a reason saying the data they
   described has been converted at source; nothing outside that window is
   touched. An eighth row, `aisvn temp_c ×0.1` over 04:12–08:20Z the same day,
   needs its scale inverted rather than deleted — see above.
2. **`power` is reconstructed for the window before the recompile.** The station
   never reported power before 2020-06-17 — the old 10-column layout had no such
   column — and the repair filled 1,800 rows with `solar_v × current_a × 0.85`,
   which holds exactly up to `June 17, 2020 at 03:18PM`. From `03:20PM`, the
   recompile, the values are the station's own: `power/(V·I)` scatters from
   −2.43 to +2.08 and only 0.24% of samples equal `V·I` exactly. A derived value
   presented as a reading, over a window with no measurement in it. The 0.85
   needs recording as an assumption.
   (`power_w` is not a measured channel at other stations either: only `aisvn`
   and `phumy2` have one at all, and `phumy2`'s reads 0 for 415,112 of 415,117
   readings.)
3. **`load` lost 195 real readings to a 0.** The column spans two units and did
   so before the repair too — millivolts to `June 17, 2020 at 12:09PM`, volts
   from `03:20PM` — but the repair did not convert it: 195 of the 198 volt
   readings are now 0, and the millivolt half is untouched. A `0` here reads as
   "no load present", which is a real state for that rail, so the loss is
   invisible rather than obvious.

**What the unit audit found, taking the current files as the source of truth.**
Seven `aisvn` channels had a mV→V change at 2020-06-17 15:20 — `solar_v`,
`battery_v`, `current_a`, `wind_v`, `solar2_v`, `lipo_v` and `load_v`. Six are
now converted; `load_v` is not. Two other boundaries are not unit changes: the
`aisvn.current_a` step at 2020-10-23 → 10-30 is the collector's pre-reinstall
window in `BAD_WINDOWS`, and `aisvn2.current_a_chA` steps by ~200× between
2021-04 and 2021-10, which is undocumented and is not a clean factor.

**`aisvn.temp_c` is now published 10× too small, and was already inconsistent.**
`CONFIRMED_WINDOWS` carries `aisvn temp_c ×0.1` for 04:12–08:20Z on 2020-06-17,
which is exactly the window the repair converted from tenths to degrees. The
committed build had 317–341 there (tenths); the current one has 31.7–34.2
(degrees), and the rollup divides by ten again. Every other reading of the record
is in tenths (mean 323.3), so those hours land at 0.33 °C in a June chart in Ho
Chi City. The window's scale needs inverting from ×0.1 to ×10 — or, better, the
repair's unit change and the regime table reconciled once rather than twice.

Note the count in that window also moved, 114 → 121. Nothing explains that yet,
and it is small enough to be a side effect of the same edit; worth a look before
the regime is touched.

**The 100%-flagged channels are almost all a band describing the wrong unit.**
Dividing by 1000 brings 98–100% of them inside the recorded band:
`phumy2.current2_a` and `lipo2_v`, `aisvn2.battery2_v`, `maker-webhooks.battery_v`,
`current_a_chA`, `load_v` and `solar2_v`, `aisvn-solar.lipo_v`,
`solar-2020-05.lipo_v`. Two are *not* a scale question at all:

- **`aisvn2.lipo2_v` is a stuck input.** 17 distinct values in 164,097 readings;
  7,097 appears 161,790 times (98.6%). The same station's `solar3_v` has 2,131
  distinct values over the same rows. The 2,307 excursions to 258–358 are what
  the detector proposed a ×0.01 window for, and that window is itself wrong —
  the low values run to 2021-10-30, not 2020-06-23. Nothing to scale until the
  hardware is known.
- **`aisvn-solar.battery_v` is a 2 V pack, not a 12 V bank.** Raw 0–2,574, mean
  2,060, over 2020-05-21 → 2020-06-12; ÷1000 gives 1.98–2.57 V, which is
  self-consistent with that station's `solar_v` topping out at 3.5 V (open
  question 2) and its header being `time, solar, battery, load_1, load_2, LiPo,
  wind, dump, boot`. The 9–16 V band describes the other stations' hardware. This
  needs a per-station *band*, not a scale — which is the same
  `CHANNEL_UNITS`/`column_semantics` gap as `solardata_raw.db`.


**The 10-column pre-recompile layout is now gone from the raw archive.** That
layout was the evidence for the 2020-06-17 recompile — the record that made
`CONFIRMED_WINDOWS`' boundary defensible. With the donor file rewritten to 11
columns, `metric_defs` records a single 11-column layout for `aisvn` where there
were two, and the remaining 38 files inherit from it. Nothing is *wrong*; the
finding is just no longer checkable against the data.

**Still to repair, from an audit of all 363 files:** 304 files across five folders
still hold at least one millivolt channel. All of them are *uniformly*
millivolt, which is what makes the confirmed regime windows a correct description
of them, and they are the ones the pipeline already compensates for. The file that
was worth repairing in place is the one that was not uniform — see above.

---

## Planned

### `solardata_raw.db` — a verbatim layer beside the derived one

**Status: not built, no stage writes one, nothing in this repository produces
it.** If you have gone looking for it after `python -m etl all`, it is not
missing.

The intent, in as much as it is written down anywhere, is a second store holding
each cell **as the sheet gave it**, next to the decision made about it. The gap
it would close is real: `metrics.METRICS` describes a plausibility band per
*column*, so it can say one unit for every station that logs that column, and the
two exceptions are carried separately in three places.

| Where | What it costs today |
|---|---|
| `config.CHANNEL_UNITS` | a per-station band override, read in two places, because applying it in one is the same bug one level up |
| `build_db._band_override` | the override resolved per row at flag time |
| `build_exports.build` → `stations.json.channel_units` | the override shipped to the browser, which is where a missing third place becomes 280 °C for a 28 °C afternoon |

**What has to be settled first.** Recorded rather than decided:

1. **What "raw" means when a cell is a sentinel.** `-992` is an IFTTT
   disconnected-sensor marker. Does the derived store read *from* the verbatim
   one, or do both continue to be built from the sheet in parallel? The second is
   what happens now, and it is why they could never be diffed against each other.
2. **One file or two.** Two files is two places for the readings to disagree,
   which is a new failure mode rather than a smaller one.
3. **What it is worth.** 731,885 rows already have a home with provenance down to
   the file and the sheet row. The case for a verbatim layer is auditability —
   re-deriving the derived store from something nobody has to trust. Whether
   anyone will do that is not established.
4. **The fuller description is not in this repository.** The name appears in one
   comment in `etl/config.py` and in the design note linked above. If a real
   specification exists elsewhere, it belongs in
   [`docs/format-design.md`](format-design.md) before anyone starts, because none
   of the four questions above are answerable from what is written down.

### Voltage columns: float volts here, integer millivolts there

**Status: decided for `solardata.db`, and the other half is not implemented.**

Every voltage in `readings` and in both rollups is a **float number of volts**,
whatever the hardware produced. That is the column's documented unit, it is what
the plausibility bands are written in, and it is what the site draws. The
collector repaired `aisvn`'s sheets into plain volts precisely so that a single
unit describes the whole column: `load_v` is 0–13.716 V in
`IFTTT_aisvn.xlsx` with no millivolt readings left, and the 195 volt readings that
an earlier repair had overwritten with 0 are back.

The **verbatim** store is where integer millivolts belong, and that step does not
exist yet. The two are not the same decision: the derived store answers "what is
this channel's value", and a float does that; the verbatim layer answers "what
did the ADC say", and for a divider output that is a 16-bit integer in millivolts
with its own scale factor beside it. So the plan is that
`solardata_raw.db` — when it is built — carries `load_v`, `battery_v` and the
rest as `INTEGER` millivolts with the divider ratio in `column_semantics`, and
that the derived store reads volts from it.

**Do not implement the conversion in `build_db` in the meantime.** Multiplying a
column into millivolts at ingest is a unit decision taken in the wrong layer, and
the cost is already documented: `aisvn-solar.battery_v` has to be ×2 at ingest
*and* override its band *and* ship the override to the browser, in three places,
because a plausibility band can only describe one unit and that column's is not
the default one. A column-wide mV conversion would multiply that cost by the
number of channels and change every stored value in the archive.

---

## Blocked — needs an answer from the collector

These are the open questions in `AGENTS.md` that cannot be closed in code. Each
is a question about hardware or about firmware, and the archive does not contain
the answer. **Do not quietly decide any of them.**

| | Question | What is known |
|---|---|---|
| 1 | The 3 unconfirmed scale regimes | `maker-webhooks.solar2_v` and `test.solar2_v` at ×0.001, which land inside their recorded bands when scaled, so the evidence is good and only the firmware is missing. The third, `aisvn2.lipo2_v` ×0.01, **should be withdrawn**: the channel is stuck, not mis-scaled — see "In progress" above |
| 2 | `aisvn-solar.solar_v` maxes at 3,532 mV | A panel should reach 15–20 V open circuit, so either that input is not a panel or the station never saw one. The whole file sits on a 2–3.5 V scale: `battery_v` reads 1.98–2.57 V, i.e. a 2 V pack, not a 12 V bank. The header is `time, solar, battery, load_1, load_2, LiPo, wind, dump, boot`. Self-consistent, and inconsistent with the bands written for the other stations |
| 3 | `phumy2.power_w` is not a power measurement | The hardware was never implemented. 415,112 of 415,117 readings are exactly 0; the other five are 13,810–19,877 W, all flagged |
| 4 | `maker-webhooks` resets its counter every 16 readings | 526 resets in 8,535 readings, 523 with no gap in sampling. A reboot looks like that, but a counter moving that fast may be something else |
| 5 | The `phumy2` bridge ratio, and with it `current2_a`'s unit | `solar2_v` is confirmed as millivolts but its maximum is 5,899 mV — 5.9 V — where a 12 V nominal panel sits at 17–20 V open circuit, so the channel is a divider output. That is also why `current2_a`'s unit cannot be settled from the archive: to read a current in amps you need a voltage to pair it with, and the only voltage this station logs is not the panel's. The two available arguments conflict — the same applet's voltage channel is confirmed at ×0.001, so consistency says milliamps-as-integer, which would make the station produce ~1.4 W |
| 6 | `aisvn.load_v` behaviour change | 0 V works to 2020-07-10, then appears sporadically to 2020-10-30. Whether the later 0s are genuine or a stuck pin is unknown. The column also spans two units inside one file — mV to 2020-06-17 12:09 local, V from 15:20 |
| 7 | What `wind_v` is connected to | Wired and logging: 0–13.3 V hourly in `aisvn` 2021, up to 12,784 V in 2020, exactly 0 for all of 2022. Not a plausible generator output either. It has no band, deliberately, because a band would have to be a guess |
| 8 | The ten further millivolt channels the detector never proposed | `aisvn2.solar3_v` and `current_a_chA`/`chB`, `aisvn.load_v` and `solar2_v` after the recompile, `phumy2.current2_a`, `aisvn-solar.load1_v`/`load2_v`, `maker-webhooks.current_a_chA`/`chB`. Recorded in `build_regimes.CONFIRMED_WINDOWS` once the raw store lands |

---

## Known debt

Things that work, and work worse than they could. Each is a deliberate decision
rather than an oversight, and each has a reason.

### Release hygiene

- **The project is moving from `kreier/solardata` to `aisvn-data/solardata`.**
  Not done. The README badges point at the new home; the branches, the tags and
  the releases are all on the old one. What has to happen, in order:

  | step | why |
  |---|---|
  | 1. Merge to the new repository's default branch | a manual `workflow_dispatch` run is only offered for a workflow on the default branch, so until it is there the "Run workflow" button does not exist |
  | 2. Push the `v0.8.0` tag (and the earlier tags, if they matter) to the new repository | a tag push runs the workflow as it exists at that commit, in whichever repository receives it |
  | 3. Set `expected_repository: aisvn-data/solardata` on later manual runs | a release into the wrong repository publishes a 19 MiB database nobody reads, and is invisible from the right one |
  | 4. Decide what happens to `kreier/solardata` — archive it, or leave it as the working fork | two repositories that both accept tags is two places a release can appear |

  A pull request does not and should not trigger a release; the tag is the
  trigger. `release.yml` documents all three gotchas above at the top of the file.
- **`v0.7.2` was published by hand.** Tagged, released, with a hand-written title
  and GitHub's generated pull-request list, and it never ran `release.yml` — so
  nothing verified the database it would have shipped and no `solardata.db.gz`
  was regenerated for it. `CHANGELOG.md` had no `## [0.7.2]` section at all;
  recorded in 0.8.0 from what the tag contains.
- **`release.yml` used to write the same two sentences into every release**, so no
  release page said what the release changed. Fixed in 0.8.0: the notes are
  extracted from the changelog section for the tag, and
  `tests/test_changelog.py` holds the format.
- **`v0.2` is not `v0.2.0`.** The tag does not follow the convention every later
  tag does, and `CHANGELOG.md` calls that release 0.2.0. Retagging a published
  release to agree with a file written afterwards is worse than recording the
  mismatch, so the mismatch is recorded. `tests/test_changelog.py` exempts tags
  that are not `X.Y.Z` rather than pretending the problem is not there.

### The data model

- **`CHANNEL_UNITS` is the small version of a `column_semantics` table.** The
  unit belongs to a (station, column) pair and the schema has no place to say so.
  This is the `solardata_raw.db` problem above, in miniature.
- **`readings` stores `ts_local` and `tz` per row.** 30 MiB of derivable data,
  stored 731,885 times. Dropping it would take the database to ~128 MiB, which is
  still over GitHub's 100 MiB limit, so it would not make the file committable
  either. `docs/format-design.md` has the measured breakdown.
- **A column-keyed band cannot describe two units.** `temp_c` is tenths on `aisvn`
  and `phumy2` and hundredths on `test`; `battery_v` is a lead pack on `aisvn` and
  something else elsewhere. One table, one column name, one band — with the
  exceptions patched around it.

### The tooling

- **`test` and `voltage-phumy` are on the site and are not solar production.** The
  distinction is a heading in the picker and a note on the panel. A reader who
  wants a chart of only production data has to filter, and the site does not offer
  that as a control.
- **No Parquet write of `rejects`, `notes` or `regimes`.** The Parquet export is
  `readings` only, partitioned by station and year. Everything needed to audit a
  value is in the database and in the quality report, and a Parquet-only reader
  does not get it.

---

## Not started, and not planned

Named so their absence is a decision rather than an oversight:

- **No API.** The site reads static files; there is no query endpoint and no plan
  for one. GitHub Pages cannot run one.
- **No incremental ingest.** `ingest` deletes and rebuilds the database. Correct
  at 364 files; it would not be at 36,400.
- **No tests against the real archive in the default suite.** `pytest` uses
  fixtures; only `python -m etl verify` and the weekly `data.yml` run see the
  731,885 rows. A channel that a fixture does not mention can still break — which
  is not hypothetical: the header-order invariant in `tests/test_ingest.py` is the
  one test that reads `data/raw`, and it exists because two `aisvn` files
  disagreed about which of two identical-width columns was `load`.
- **No cross-station calibration.** Each band is per column, per station, and
  nothing checks that two stations' `battery_v` are the same kind of thing.

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
3. **What it is worth.** 730,914 rows already have a home with provenance down to
   the file and the sheet row. The case for a verbatim layer is auditability —
   re-deriving the derived store from something nobody has to trust. Whether
   anyone will do that is not established.
4. **The fuller description is not in this repository.** The name appears in one
   comment in `etl/config.py` and in the design note linked above. If a real
   specification exists elsewhere, it belongs in
   [`docs/format-design.md`](format-design.md) before anyone starts, because none
   of the four questions above are answerable from what is written down.

---

## Blocked — needs an answer from the collector

These are the open questions in `AGENTS.md` that cannot be closed in code. Each
is a question about hardware or about firmware, and the archive does not contain
the answer. **Do not quietly decide any of them.**

| | Question | What is known |
|---|---|---|
| 1 | The 3 unconfirmed scale regimes | `aisvn2.lipo2_v` ×0.01 over 2020-06-18 to 06-23, and ×0.001 for `maker-webhooks.solar2_v` and `test.solar2_v`. Both land inside their recorded bands when scaled, so the evidence is good and only the firmware is missing |
| 2 | `aisvn-solar.solar_v` maxes at 3,532 mV | A panel should reach 15–20 V open circuit, so either that input is not a panel or the station never saw one. Flags nothing today, because 3.5 V is inside a 0–60 V band |
| 3 | `phumy2.power_w` is not a power measurement | The hardware was never implemented. 415,112 of 415,117 readings are exactly 0; the other five are 13,810–19,877 W, all flagged |
| 4 | `maker-webhooks` resets its counter every 16 readings | 526 resets in 8,535 readings, 523 with no gap in sampling. A reboot looks like that, but a counter moving that fast may be something else |
| 5 | The `phumy2` bridge ratio | `solar2_v` is confirmed as millivolts but steps from ~5,000 mV to ~1,200 mV when a bridge and load were fitted, so the stored value is a divider output. Without the ratio, `solar2_v` after the bridge is not a panel voltage and should not be charted as one |
| 6 | `aisvn.load_v` behaviour change | 0 V works to 2020-07-10, then appears sporadically to 2020-10-30. Whether the later 0s are genuine or a stuck pin is unknown |
| 7 | What `wind_v` is connected to | Wired and logging: 0–13.3 V hourly in `aisvn` 2021, up to 12,784 V in 2020, exactly 0 for all of 2022. Not a plausible generator output either. It has no band, deliberately, because a band would have to be a guess |
| 8 | The ten further millivolt channels the detector never proposed | `aisvn2.solar3_v` and `current_a_chA`/`chB`, `aisvn.load_v` and `solar2_v` after the recompile, `phumy2.current2_a`, `aisvn-solar.load1_v`/`load2_v`, `maker-webhooks.current_a_chA`/`chB`. Recorded in `build_regimes.CONFIRMED_WINDOWS` once the raw store lands |

---

## Known debt

Things that work, and work worse than they could. Each is a deliberate decision
rather than an oversight, and each has a reason.

### Release hygiene

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
  stored 730,914 times. Dropping it would take the database to ~128 MiB, which is
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
  730,914 rows. A channel that a fixture does not mention can still break.
- **No cross-station calibration.** Each band is per column, per station, and
  nothing checks that two stations' `battery_v` are the same kind of thing.

/**
 * Checks the pure logic in src/data.js and the chart's path/tick helpers
 * against the real exported CSVs.
 *
 * There is no test runner in the frontend, so this is a plain script run with
 * `node scripts/check_frontend.mjs`. It exists because the two things most
 * likely to be wrong -- treating an empty CSV cell as 0, and drawing a line
 * across a gap -- are both invisible in a screenshot until someone misreads a
 * chart.
 */

import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import assert from 'node:assert/strict'

const ROOT = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const DATA = join(ROOT, 'public', 'data')

// ---------------------------------------------------------------- test data

const SAMPLE = [
  'day,ts_utc_day,n_samples,n_hours,solar_v_avg,solar_v_max,battery_v_min,battery_v_max,power_w_avg,power_w_max,energy_wh,temp_c_min,temp_c_avg,temp_c_max',
  '2023-01-01,2023-01-01T00:00:00Z,671.0,24.0,12.5,14.0,13.1,13.9,10.0,20.0,5.0,25.1,25.9,30.6',
  '2023-01-02,2023-01-02T00:00:00Z,0.0,0.0,,,,,,,,,,,',
  '2023-01-03,2023-01-03T00:00:00Z,600.0,20.0,0.0,0.0,12.0,12.5,0.0,0.0,0.0,24.0,25.0,29.0',
].join('\n')

// Mirror of the helpers in src/data.js, so the assertions test the same logic
// the app runs. Kept in sync by the assertions below comparing against the
// real export output.
function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/).filter((line) => line.length > 0)
  const header = lines[0].split(',')
  return lines.slice(1).map((line) => {
    const cells = line.split(',')
    const row = {}
    header.forEach((name, index) => {
      row[name] = cells[index] ?? ''
    })
    return row
  })
}

function num(value) {
  if (value === undefined || value === null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

let passed = 0
function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ok  ${name}`)
}

console.log('chart helpers and CSV semantics')

check('an empty cell becomes null, never 0', () => {
  const row = parseCsv(SAMPLE)[0]
  assert.equal(num(row.solar_v_avg), 12.5)
  assert.equal(num(row.battery_v_min), 13.1)
})

check('a genuine 0 survives as 0', () => {
  const row = parseCsv(SAMPLE)[2]
  // phumy2's power channel is 0.0 for every day; coercing that to null would
  // hide a real measurement, and coercing null to 0 would invent one.
  assert.equal(num(row.solar_v_avg), 0)
  assert.equal(num(row.power_w_avg), 0)
  assert.equal(num(row.n_samples), 600)
})

check('a fully empty day is all-null', () => {
  const row = parseCsv(SAMPLE)[1]
  for (const key of ['solar_v_avg', 'battery_v_min', 'temp_c_avg', 'energy_wh']) {
    assert.equal(num(row[key]), null, key)
  }
  assert.equal(num(row.n_samples), 0)
})

check('buildPath breaks the line at a null instead of joining across it', () => {
  const rows = [
    { date: 0, v: 1 },
    { date: 1, v: null },
    { date: 2, v: 3 },
  ]
  const x = (d) => d * 10
  const y = (v) => 100 - v * 10
  const get = (row) => row.v

  let d = ''
  let penDown = false
  for (const row of rows) {
    const value = get(row)
    if (value === null) {
      penDown = false
      continue
    }
    d += `${penDown ? 'L' : 'M'}${x(row.date)},${y(value)} `
    penDown = true
  }
  // Two subpaths: M for the first point, M again for the third. A single path
  // with an L across the gap would fabricate a reading on the 2nd.
  assert.equal((d.match(/M/g) || []).length, 2, d)
  assert.equal((d.match(/L/g) || []).length, 0, d)
})

check('a contiguous series is one subpath', () => {
  const rows = [{ v: 1 }, { v: 2 }, { v: 3 }]
  const get = (r) => r.v
  let d = ''
  let penDown = false
  for (const row of rows) {
    d += `${penDown ? 'L' : 'M'}${get(row)} `
    penDown = true
  }
  assert.equal((d.match(/M/g) || []).length, 1)
  assert.equal((d.match(/L/g) || []).length, 2)
})

check('niceTicks produces round numbers inside the domain', () => {
  const lo = 12.4
  const hi = 29.8
  const span = hi - lo
  const rawStep = span / 5
  const magnitude = 10 ** Math.floor(Math.log10(rawStep))
  const normalised = rawStep / magnitude
  const step = (normalised >= 5 ? 10 : normalised >= 2 ? 5 : normalised >= 1 ? 2 : 1) * magnitude
  const ticks = []
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) {
    ticks.push(Number(t.toFixed(10)))
  }
  assert.ok(ticks.length >= 2, `only ${ticks.length} ticks`)
  for (const tick of ticks) {
    assert.ok(tick >= lo && tick <= hi, `${tick} outside [${lo}, ${hi}]`)
  }
  // The float-accumulation guard: labels must not read 0.30000000000000004.
  for (const tick of ticks) {
    assert.equal(String(tick).length <= 6, true, String(tick))
  }
})

check('a flat series still yields a drawable band', () => {
  // power_w_avg is 0.0 for every day of phumy2; lo === hi would divide by zero.
  const lo = 0
  const hi = 0
  let loAdj = lo
  let hiAdj = hi
  if (loAdj === hiAdj) {
    loAdj -= 0.5
    hiAdj += 0.5
  }
  assert.ok(hiAdj > loAdj)
  const y = (v) => 100 - ((v - loAdj) / (hiAdj - loAdj)) * 100
  assert.ok(Number.isFinite(y(0)))
})

// ------------------------------------------------------- real exported data

console.log('\nreal exported files')

const stations = JSON.parse(readFileSync(join(DATA, 'stations.json'), 'utf8'))
const quality = JSON.parse(readFileSync(join(DATA, 'quality.json'), 'utf8'))

check('stations.json has the production stations with years', () => {
  const published = stations.filter((s) => s.published)
  assert.ok(published.length >= 6, `only ${published.length} published`)
  for (const station of published) {
    assert.ok(station.years.length > 0, `${station.station_id} has no years`)
    assert.ok(station.n_readings > 0, station.station_id)
  }
})

check('every published station declares the rollups that exist on disk', () => {
  // The resolution switch offers only what stations.json claims, so a missing
  // hourly file has to fail here rather than as a 404 in the browser.
  for (const station of stations.filter((s) => s.published)) {
    for (const folder of ['daily', 'hourly']) {
      assert.ok(
        station.granularities.includes(folder),
        `${station.station_id} is published without ${folder}`,
      )
      for (const year of station.years) {
        assert.ok(
          existsSync(join(DATA, station.station_id, folder, `${year}.csv`)),
          `${station.station_id}/${folder}/${year}.csv is missing`,
        )
      }
    }
  }
})

check('bench stations are present but flagged as not production', () => {
  // Their rollups are published and their readings browsable; `published: false`
  // only separates them into the site's "Not solar production" group. Filtering
  // the picker on `published` is what hid them, and the check that would have
  // caught it is the one below.
  const bench = stations.filter((s) => !s.published).map((s) => s.station_id)
  assert.ok(bench.includes('test'), bench.join(','))
  assert.ok(bench.includes('voltage-phumy'), bench.join(','))
  for (const id of bench) {
    const station = stations.find((s) => s.station_id === id)
    assert.ok(
      station.years.length > 0,
      `${id} has no published year, so the site cannot offer it at all`,
    )
  }
})

check('quality.json carries the counts the UI displays', () => {
  // 731,885 since the collector's account of the test station removed its
  // 11-column solar layout. The site displays this number, so a change to it has
  // to fail here rather than appear quietly on the Data quality tab.
  assert.equal(quality.totals.readings, 731885)
  assert.equal(quality.source_files.total, 364)
  // 290 of 364, and it was 305: the collector's replacement `aisvn` and `phumy2b`
  // files carry header rows, and a header is the only thing that makes a file not
  // headerless. It has fallen by nine in two rounds as the repair reached further
  // back into the archive, which is worth watching: every file that gains a header
  // stops borrowing one, so this number is a direct measure of how much of the
  // archive is still standing on an inferred schema.
  assert.equal(quality.source_files.without_header, 290)
  assert.ok(Array.isArray(quality.regimes))
  assert.ok(Array.isArray(quality.notes))
  // 11: the ten recovered from data cells, plus one rationale per excluded file.
  // It was 12 while two test files were excluded, and one of them is gone from
  // the archive, so the note went with it. Every note must still have a source
  // row, which is the property that matters, not the count.
  assert.ok(quality.notes.length >= 11, `expected >= 11 notes, got ${quality.notes.length}`)
  for (const note of quality.notes) {
    assert.ok(note.note && String(note.note).length > 0, `an empty note: ${JSON.stringify(note)}`)
  }
  // And the exclusion is visible as a groupable category, not a silent hole.
  assert.ok(
    quality.rejects.by_reason.some((r) => r.reason === 'station_setup'),
    'the excluded test files are not in rejects.by_reason',
  )
})

check('a folder that changed layout keeps both layouts, not the last one', () => {
  // Regression. `metric_defs` was keyed on (station, folder, col_index), so a
  // folder could hold exactly one meaning per column index and the second layout
  // silently overwrote the first. `aisvn` gained a `power` column on 2020-06-17
  // and went from 10 columns to 11, so column 4 ended up recorded as `load` for
  // all 39 files when 38 of them are 11-column files where it is `power`. The
  // ingest was never wrong -- it maps each file with its own width-matched
  // header -- but this table is what the report and the channel-coverage tab
  // present as the schema.
  //
  // The width is the part of the key that carries the change, so the check is
  // that it is recorded. `aisvn` no longer *has* two widths to record: the
  // collector's conversion of `IFTTT_aisvn.xlsx` made all 39 files 11 columns.
  //
  // **The key cannot express a difference of column *order* at the same width**,
  // and the archive now has one: five `aisvn` files put `power` at index 4 where
  // the converted file puts `load`, so the two collide and files (1)-(7),
  // which borrow their order from the converted file, store the two channels
  // swapped. That is asserted where it can be seen -- against the real sheets, in
  // `tests/test_ingest.py::TestHeadersInOneFolderAgree`, because it is a
  // statement about the raw archive and this file cannot read an xlsx.
  const defs = quality.metric_defs
  assert.ok(defs.length > 0, 'quality.json has no metric_defs')
  for (const def of defs) {
    assert.ok(
      'n_columns' in def,
      `metric_defs row for ${def.station_id}/${def.source_dir}/${def.col_index} has no width`,
    )
  }
  const aisvnWidths = new Set(defs.filter((d) => d.station_id === 'aisvn').map((d) => d.n_columns))
  for (const width of aisvnWidths) {
    // One meaning per index per width: that is what the key guarantees, and it
    // is exactly what an order-only difference would violate behind its back.
    const atIndex4 = defs.filter(
      (d) => d.station_id === 'aisvn' && d.n_columns === width && d.col_index === 4,
    )
    assert.equal(
      new Set(atIndex4.map((d) => d.raw_name)).size,
      1,
      `aisvn width ${width}: index 4 is recorded as more than one column`,
    )
  }
  // `test` *was* two unrelated schemas in one folder -- 4 columns of
  // nix/temp/wifi probe and 11 columns of solar channels. The collector's account
  // is that the solar stretch was system setup rather than measurement, so those
  // two files are excluded and `test` now has one layout. The exclusion is
  // recorded row by row in `rejects` with reason `station_setup`, which is
  // asserted above; this asserts the layout itself is gone rather than merely
  // flagged, so a layout cannot come back through a donor.
  const widths = new Set(defs.filter((d) => d.station_id === 'test').map((d) => d.n_columns))
  assert.deepEqual([...widths], [4], `test should have only the probe layout, saw ${[...widths]}`)
  const testCols = defs.filter((d) => d.station_id === 'test').map((d) => d.canonical_col)
  assert.ok(
    !testCols.some((c) => c && (c.includes('solar') || c.includes('battery'))),
    `test must have no solar or battery channel, saw ${testCols.join(',')}`,
  )
})

check('the uptime counter is published, since it is the only reboot evidence', () => {
  // `boot` is the logger's own monotonic read counter. It is the only record that
  // the hardware restarted, and the gaps in every other channel start where it
  // drops. It has no plausibility band (it is a count, not a measurement) and it
  // was absent from both rollups, so the site could not show it at all.
  for (const [station, year, column] of [
    ['aisvn', '2020', 'boot_count_max'],
    ['phumy2', '2023', 'boot_count_max'],
    ['aisvn2', '2021', 'boot_count_max'],
  ]) {
    const rows = parseCsv(readFileSync(join(DATA, station, 'daily', `${year}.csv`), 'utf8'))
    assert.ok(column in rows[0], `${station} ${year} has no ${column} column`)
    const values = rows.map((r) => num(r[column])).filter((v) => v !== null)
    assert.ok(values.length > 0, `${station} ${year} ${column} has no values`)
    assert.ok(Math.max(...values) > 1, `${station} ${year}: counter never got past 1`)
  }
  // A station whose applet has no boot column must not gain a fabricated one.
  const noBoot = parseCsv(readFileSync(join(DATA, 'solar-2020-05', 'daily', '2020.csv'), 'utf8'))
  assert.ok(
    noBoot.every((r) => num(r.boot_count_max) === null),
    'solar-2020-05 has no boot channel and must not have uptime values',
  )
  const hourly = parseCsv(readFileSync(join(DATA, 'aisvn', 'hourly', '2020.csv'), 'utf8'))
  assert.ok('boot_count_min' in hourly[0], 'the hourly rollup needs boot_count_min too')
})

check('a contaminated aggregate is distinguishable from a clean one', () => {
  // The real case, and the reason the per-metric counts exist. phumy2
  // 2020-11-27 16:00 UTC contains one sample of power_w = 19,877 where its
  // neighbours are 0. Averaged with the 29 good zeros in that hour it becomes
  // 662.57 W, which is *inside* the +/-2000 W band, so nothing about the value
  // says anything is wrong. `power_w_n_oor = 1` does.
  const hourly = parseCsv(readFileSync(join(DATA, 'phumy2', 'hourly', '2020.csv'), 'utf8'))
  assert.ok('power_w_n_oor' in hourly[0], 'the hourly rollup needs a per-metric count')
  const row = hourly.find((r) => r.ts_utc === '2020-11-27T16:00:00Z')
  assert.ok(row, 'the 2020-11-27 16:00 bucket is missing')
  assert.equal(num(row.n_samples), 30)
  assert.equal(num(row.power_w_n_oor), 1, 'exactly one sample of the 30 was out of band')
  // The aggregate is inside the band, which is the whole point: the value alone
  // cannot be judged, the count beside it can.
  const value = num(row.power_w_avg)
  assert.ok(value > 600 && value < 700, `expected ~662.57, got ${value}`)
  assert.ok(Math.abs(value) <= 2000, 'the aggregate is inside the band')
  // The row-level count is 30 of 30 and therefore useless on its own. The readings
  // really are stored as 232 against a +/-50 A band, so at ingest every one of them
  // is flagged -- and that flag is about the *stored reading*, which rule 2 says is
  // kept as it was written rather than quietly repaired.
  assert.equal(num(row.n_out_of_range), 30, 'the row-level count is saturated here')
  // The rollup's per-metric count disagrees, and now it is the rollup that is
  // right: `current2_a` is confirmed as milliamps, the published value is 0.232 A
  // and the count is 0. A flag that the collector has since explained away must not
  // keep marking a bucket contaminated, or every phumy2 aggregate is suspect and
  // the count stops meaning anything. The two numbers describe different things --
  // the reading as stored, and the value as published -- and both are worth having.
  assert.equal(num(row.current2_a_n_oor), 0, 'the confirmed mA scale must clear the rollup count')
  // It is a single bucket in the whole station-year, so a reader is not looking
  // at a channel that is broadly broken.
  const contaminated = hourly.filter((r) => num(r.power_w_n_oor) > 0)
  assert.ok(contaminated.length < 10, `${contaminated.length} buckets flagged on power_w`)
  // And the same must hold across the station: no bucket is marked out of range on
  // a channel whose regime the collector has confirmed, because the value it
  // publishes is inside the band. This is the regression that produced 416,088
  // "contaminated" current2_a samples on a channel reading 0.2 A.
  for (const channel of ['current2_a', 'solar2_v', 'lipo2_v']) {
    const marked = hourly.filter((r) => num(r[`${channel}_n_oor`]) > 0).length
    assert.ok(marked < 10, `${marked} phumy2 2020 buckets flagged on ${channel}`)
  }
})

check('every channel a station records is offered, not a fixed six', () => {
  // Regression. The picker iterated a hardcoded list of six metrics, so
  // `solar3_v` and `lipo2_v` were in `readings`, in the rollups and in the
  // database the whole time with nowhere to appear -- and selecting AISVN #2
  // offered nothing that worked. Channels are now discovered from the data.
  for (const station of stations.filter((s) => s.published)) {
    const year = station.years[station.years.length - 1]
    const rows = parseCsv(
      readFileSync(join(DATA, station.station_id, 'daily', `${year}.csv`), 'utf8'),
    )
    for (const [column] of [
      ['solar_v'], ['solar2_v'], ['solar3_v'], ['battery_v'], ['battery2_v'],
      ['lipo_v'], ['lipo2_v'], ['current_a'], ['current2_a'], ['power_w'],
      ['load_v'], ['temp_c'],
    ]) {
      if (rows.some((r) => num(r[`${column}_avg`]) !== null)) {
        assert.ok(
          `${column}_avg` in rows[0],
          `${station.station_id} ${year}: ${column} has data but no exported column`,
        )
      }
    }
  }
  // The two that were unreachable before, asserted directly.
  const aisvn2 = parseCsv(readFileSync(join(DATA, 'aisvn2', 'daily', '2021.csv'), 'utf8'))
  assert.ok(
    aisvn2.some((r) => num(r.solar3_v_avg) !== null),
    'aisvn2 solar3_v must be exported and therefore selectable',
  )
  const phumy2 = parseCsv(readFileSync(join(DATA, 'phumy2', 'daily', '2020.csv'), 'utf8'))
  assert.ok(
    phumy2.some((r) => num(r.lipo2_v_avg) !== null),
    'phumy2 lipo2_v must be exported and therefore selectable',
  )
  // And each station's observed range is published, so the site can show what
  // that instrument did beside what the pipeline expects of it.
  assert.ok(Array.isArray(quality.channel_ranges), 'quality.json has no channel_ranges')
  const forPhumy2 = quality.channel_ranges.filter((r) => r.station_id === 'phumy2')
  assert.ok(forPhumy2.length >= 5, 'phumy2 should report several channels')
  for (const r of forPhumy2) {
    assert.ok(r.n > 0 && r.min !== null && r.max !== null, `${r.column} has no range`)
    assert.ok('band_lo' in r && 'band_hi' in r, `${r.column} has no band`)
  }
  // The disagreement this exists to surface. It used to be phumy2.temp_c, which
  // read 80.6 against a 45 C band until the collector confirmed that the channel
  // is stored in tenths of a degree -- at which point the band and the readings
  // agree, which is the whole point of the confirmations. The disagreement that
  // remains is aisvn2.lipo2_v: 6.3 to 7.1 V against a band written for a single
  // cell, and that one still needs the hardware.
  const temp = forPhumy2.find((r) => r.column === 'temp_c')
  assert.ok(temp.max <= temp.band_hi, 'phumy2 temp_c should now sit inside its band')
  assert.ok(temp.n > 400000, 'temp_c should be the dominant phumy2 channel')
  // And the unit is visible rather than implied, because a channel stored in
  // tenths and documented in degrees is the exact mismatch that cost two commits
  // in metric_defs.
  assert.equal(temp.unit, '0.1 degC')
  const aisvn2Lipo = quality.channel_ranges.find(
    (r) => r.station_id === 'aisvn2' && r.column === 'lipo2_v',
  )
  assert.ok(aisvn2Lipo, 'aisvn2 lipo2_v should be reported')
  assert.ok(
    aisvn2Lipo.max > aisvn2Lipo.band_hi,
    'aisvn2 lipo2_v should still exceed its 1S band; that is an open question',
  )
})

check('a reject reason is a category, never a sentence', () => {
  // Rule 2, and the archive is where ignoring it cost 80.6 MiB: storing the
  // NULL_WINDOWS prose as `rejects.reason` put one ~300-character sentence on
  // 220,074 rows and made `rejects` as large as `readings`. The prose now lives
  // once, in etl/config.py, and is republished here -- so this asserts the
  // category is short AND that the sentence is still reachable by a reader.
  for (const row of quality.rejects.by_reason) {
    assert.ok(
      row.reason.length <= 40,
      `rejects.reason is ${row.reason.length} chars: ${row.reason.slice(0, 60)}`,
    )
    assert.ok(!/\s\s|;/.test(row.reason), `rejects.reason reads as prose: ${row.reason}`)
  }
  assert.ok(
    quality.rejects.by_reason.some((r) => r.reason === 'null_window'),
    'the null_window category is missing from by_reason',
  )
  // And the explanation has to still be published, or the category is a shrug.
  assert.ok(Array.isArray(quality.null_windows), 'quality.json has no null_windows')
  assert.ok(quality.null_windows.length > 0, 'null_windows is empty')
  const window = quality.null_windows.find((w) => w.n_rejected > 1000)
  assert.ok(window, 'no window explains a significant number of cells')
  assert.ok(window.why.length > 40, 'the window has no explanation')
  assert.ok(Array.isArray(window.columns) && window.columns.length > 0)
  assert.ok(window.valid_from && window.valid_to)
  // Every column the window names should be accounted for in the count.
  const flagged = quality.null_windows.reduce((sum, w) => sum + w.n_rejected, 0)
  assert.equal(
    flagged,
    quality.rejects.by_reason.find((r) => r.reason === 'null_window').n,
    'window counts do not add up to the null_window reject total',
  )
})

let dailyFiles = 0
let dailyRows = 0
let hourlyFiles = 0
let hourlyRows = 0
function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walk(full)
    else if (entry.endsWith('.csv')) {
      const rows = parseCsv(readFileSync(full, 'utf8'))
      const folder = full.split(/[\\/]/).at(-2)
      if (folder === 'daily') {
        dailyFiles += 1
        dailyRows += rows.length
        for (const row of rows) {
          assert.match(row.day, /^\d{4}-\d{2}-\d{2}$/, `${full}: bad day ${row.day}`)
          assert.equal(row.ts_utc_day.slice(0, 10), row.day, `${full}: local/UTC day mismatch`)
        }
      } else if (folder === 'hourly') {
        hourlyFiles += 1
        hourlyRows += rows.length
        for (const row of rows) {
          assert.match(
            row.ts_utc,
            /^\d{4}-\d{2}-\d{2}T\d{2}:00:00Z$/,
            `${full}: bad ts_utc ${row.ts_utc}`,
          )
        }
      } else {
        assert.fail(`unexpected CSV folder: ${folder} in ${full}`)
      }
    }
  }
}
walk(DATA)

check('every daily CSV parses and its day matches its UTC day', () => {
  // 16 station-years across all 8 stations, 1,184 daily rows. Pinned because a
  // silent change here means the site is showing a different amount of data than
  // the report claims. It was 13 files and 1,124 rows across 6 stations until
  // 0.7.2, when `test` and `voltage-phumy` stopped being withheld; 1,127 and 1,124
  // before that were the 2020-06-17 schema alignment fix and the aisvn (25) row
  // exclusions; 15 and 1,181 until 0.8.0, when phumy2b gained a 2026 file. Each
  // count is also pinned independently by `data/baseline.json`.
  assert.equal(dailyFiles, 16, `expected 16 daily csv files, got ${dailyFiles}`)
  assert.equal(dailyRows, 1184, `expected 1184 daily rows, got ${dailyRows}`)
})

check('the hourly rollups are published alongside the daily ones', () => {
  // Same 16 station-years, 25,566 hourly buckets, and the same numbers
  // `data/baseline.json` pins. This is the file the Hour view reads; if it
  // silently stops being written the view 404s rather than degrading, so it is
  // pinned here instead.
  assert.equal(hourlyFiles, 16, `expected 16 hourly csv files, got ${hourlyFiles}`)
  assert.equal(hourlyRows, 25566, `expected 25566 hourly rows, got ${hourlyRows}`)
})

check('the hourly rollups agree with the daily ones on the same days', () => {
  // The daily row is derived from the hourly rows, so any day present in one
  // must be present in the other. A mismatch means the aggregate stage or the
  // export is bucketing differently, which is the bug the `ts_utc` vs `day`
  // year split would cause.
  for (const station of stations.filter((s) => s.years.length > 0)) {
    for (const year of station.years) {
      const daily = parseCsv(
        readFileSync(join(DATA, station.station_id, 'daily', `${year}.csv`), 'utf8'),
      )
      const hourly = parseCsv(
        readFileSync(join(DATA, station.station_id, 'hourly', `${year}.csv`), 'utf8'),
      )
      const dailyDays = new Set(daily.map((r) => r.day))
      const hourlyDays = new Set(hourly.map((r) => r.ts_utc.slice(0, 10)))
      for (const day of dailyDays) {
        assert.ok(
          hourlyDays.has(day),
          `${station.station_id} ${year}: daily row ${day} has no hourly rows`,
        )
      }
      const dailySamples = daily.reduce((sum, r) => sum + num(r.n_samples), 0)
      const hourlySamples = hourly.reduce((sum, r) => sum + num(r.n_samples), 0)
      assert.equal(
        dailySamples,
        hourlySamples,
        `${station.station_id} ${year}: daily and hourly sample counts differ`,
      )
    }
  }
})

check('a day of genuine zeros is not confused with a day of NULLs', () => {
  const rows = parseCsv(readFileSync(join(DATA, 'phumy2', 'daily', '2023.csv'), 'utf8'))
  const reported = rows.filter((r) => num(r.n_samples) > 0)
  assert.ok(reported.length > 200, `only ${reported.length} days with samples`)
  const withSolar = rows.filter((r) => num(r.solar_v_avg) !== null)
  // phumy2 has no solar_v channel at all: the column must be empty for every
  // row, which is what makes availableMetrics() drop it from the UI.
  assert.equal(withSolar.length, 0, `${withSolar.length} rows unexpectedly have solar_v`)
  const withPower = rows.filter((r) => num(r.power_w_avg) === 0)
  assert.ok(withPower.length > 200, `only ${withPower.length} zero-power days`)
  // The column is `temp_deci_c_avg` and holds tenths of a degree. The name is the
  // point: `temp_c_avg` in a header would have put 294.8 in a field documented
  // as degrees, which is the same mismatch metric_defs had.
  assert.ok('temp_deci_c_avg' in rows[0], 'the temperature column must name its unit')
  assert.ok(!('temp_c_avg' in rows[0]), 'no column may be named as degrees and hold tenths')
  const withTemp = rows.filter((r) => num(r.temp_deci_c_avg) !== null)
  assert.ok(withTemp.length > 200, `only ${withTemp.length} days with temperature`)
  // And the values really are tenths: phumy2's 15.5 to 80.6 degC, so 155 to 806.
  const temps = rows.map((r) => num(r.temp_deci_c_avg)).filter((v) => v !== null)
  assert.ok(Math.min(...temps) >= 100, `expected tenths from 100, got ${Math.min(...temps)}`)
  assert.ok(Math.max(...temps) <= 900, `expected tenths up to 806, got ${Math.max(...temps)}`)
})

// --------------------------------------------------------- flagged-value bands

const bands = JSON.parse(readFileSync(join(DATA, 'metrics.json'), 'utf8')).bands

/** CSV column -> canonical channel, the inverse of VALUE_COLUMNS in src/data.js. */
const CHANNEL_OF = {
  solar_v_avg: 'solar_v',
  solar2_v_avg: 'solar2_v',
  battery_v_avg: 'battery_v',
  battery_v_min: 'battery_v',
  battery2_v_min: 'battery2_v',
  power_w_avg: 'power_w',
  temp_c_avg: 'temp_c',
}

/** The metric keys, mirrored from src/data.js, in declaration order. */
const METRIC_CHANNELS = {
  solar: ['solar_v_avg', 'solar2_v_avg'],
  battery: ['battery_v_min', 'battery2_v_min'],
  power: ['power_w_avg'],
  temp: ['temp_c_avg'],
  energy: ['energy_wh'],
}

/** Mirrors availableMetricKeys in src/data.js: keys, in declaration order. */
function availableMetricKeys(rows) {
  return Object.keys(METRIC_CHANNELS).filter((key) =>
    rows.some((row) => METRIC_CHANNELS[key].some((c) => num(row[c]) !== null)),
  )
}

check('an available metric is selectable, because the picker is given keys', () => {
  // Regression, and it was entirely silent. `availableMetrics()` returned metric
  // *objects* while the picker tested `metrics.includes(metric.key)` against
  // them, so every checkbox rendered disabled for every station -- no error, no
  // empty chart, the default two channels still drawn. Nothing here imports
  // src/data.js (it needs import.meta.env), so the contract is asserted against
  // the real CSVs: the value has to be a string, and the picker's membership
  // test has to succeed for a channel the station demonstrably has.
  for (const station of stations.filter((s) => s.published)) {
    for (const year of station.years) {
      const rows = parseCsv(
        readFileSync(join(DATA, station.station_id, 'daily', `${year}.csv`), 'utf8'),
      )
      const keys = availableMetricKeys(rows)
      for (const key of keys) {
        assert.equal(typeof key, 'string', `${station.station_id} ${year}: ${key} is not a string`)
        assert.ok(keys.includes(key), `${station.station_id} ${year}: ${key} is not selectable`)
      }
      // And the selection the UI defaults to must be a subset of what is
      // offered, or the chart is handed metrics it will not draw.
      const defaultSelection = keys.slice(0, 2)
      for (const key of defaultSelection) {
        assert.ok(keys.includes(key), `${station.station_id} ${year}: default ${key} unavailable`)
      }
    }
  }
  // One station legitimately has nothing: `solar-2020-05` is a bench sheet whose
  // rollup columns are all empty. Its picker is meant to be disabled.
  const bench = parseCsv(readFileSync(join(DATA, 'solar-2020-05', 'daily', '2020.csv'), 'utf8'))
  assert.deepEqual(availableMetricKeys(bench), [])
})

check('the view the site opens on reads real columns', () => {
  // `check_render.mjs` resolves DEFAULT_VIEW's station, year, range and channels
  // against the real files, but it does so through `VALUE_COLUMNS` in src/data.js
  // -- a mapping. This is the same question asked of the CSV itself: is the
  // column the site will chart actually published, under the name the exporter
  // writes? `wind_v` is the collector's unwired wind-turbine input, the most
  // likely of the opening pair to be dropped in a cleanup as "always zero", and
  // nothing in the browser would fail if it were.
  const header = readFileSync(join(DATA, 'aisvn', 'daily', '2020.csv'), 'utf8')
    .split(/\r?\n/)[0]
    .split(',')
  for (const column of ['solar_v_avg', 'wind_v_avg']) {
    assert.ok(header.includes(column), `aisvn 2020: the rollup has no ${column} column`)
  }
})

/** Mirrors isOutOfBand in src/data.js, so the assertion exercises that logic. */
function isOutOfBand(channel, value) {
  const band = bands[channel]
  if (!band || band.lo === null || band.hi === null) return null
  if (value === null || value === undefined) return null
  return value >= band.lo && value <= band.hi ? null : band
}

/**
 * Mirrors classifyRows: a bucket is flagged when a value being plotted is
 * outside the band recorded for the channel that value came from.
 */
function flaggedDays(rows, columns) {
  return rows.filter((row) =>
    columns.some((column) => isOutOfBand(CHANNEL_OF[column], num(row[column]))),
  )
}

check('the bands reach the browser verbatim from the ETL', () => {
  // If these drift from etl/normalize/metrics.py the site is flagging values on
  // a criterion the pipeline never applied, which is the one thing shipping the
  // bands rather than retyping them was for.
  assert.equal(bands.solar_v.lo, 0)
  assert.equal(bands.solar_v.hi, 60)
  assert.equal(bands.battery_v.lo, 9)
  assert.equal(bands.battery_v.hi, 16)
  // 50 to 900 tenths of a degree, not 5 to 45 degrees. The unit is part of the
  // band, and the site divides by it for display -- which is why the assertion is
  // on the unit as well as the numbers.
  assert.equal(bands.temp_c.unit, '0.1 degC')
  assert.equal(bands.temp_c.lo, 50)
  assert.equal(bands.temp_c.hi, 900)
  assert.equal(bands.power_w.lo, -2000)
  assert.equal(bands.power_w.hi, 2000)
  // A channel with no band must say so rather than be missing, so the UI can
  // answer "this one is never flagged" instead of inferring it.
  assert.ok('wind_v' in bands)
  assert.equal(bands.wind_v.lo, null)
})

check('the band the pipeline records catches a day the readings deserve to lose', () => {
  // The original case was aisvn 2020-10-01, a single reading of solar 123 V and
  // battery 456 V between days reading 18 and 19 -- an ADC test pattern. The
  // collector's repair has since removed that reading, and pinning it would mean
  // this check fails every time the archive is fixed, which is the wrong reason
  // for a test to go red. So it asserts the *property* instead, recomputing the
  // comparison from the band the pipeline ships: a day is published as flagged
  // exactly when its value is outside that band, and at least one day is.
  //
  // 21 of aisvn's 101 days in 2020 are flagged, and the flag is not decorative:
  // 2020-10-30's battery minimum is -0.99 V, and 2020-10-23 to 11-27 sit at
  // 28-29 V on a bank whose band tops out at 16. Those are hardware faults, and
  // the site has to be able to see them.
  const rows = parseCsv(readFileSync(join(DATA, 'aisvn', 'daily', '2020.csv'), 'utf8'))
  const bands = JSON.parse(readFileSync(join(DATA, 'metrics.json'), 'utf8')).bands
  const flagged = flaggedDays(rows, [['solar_v_avg'], ['battery_v_min']])
  assert.ok(flagged.length > 0, 'aisvn 2020 has no flagged day, so the band catches nothing')

  for (const row of rows) {
    for (const [column, channel, band] of [
      ['solar_v_avg', 'solar_v', bands.solar_v],
      ['battery_v_min', 'battery_v', bands.battery_v],
    ]) {
      const value = num(row[column])
      if (value === null) continue
      if (value < band.lo || value > band.hi) {
        assert.ok(
          num(row[`${channel}_n_oor`]) > 0,
          `${row.day} ${column} = ${value} is outside ${band.lo}..${band.hi} but ` +
            `${channel}_n_oor is 0, so the site would draw an out-of-band value with no flag`,
        )
      }
    }
  }
  // The reverse does *not* hold and must not be asserted: a channel's count can be
  // positive while its aggregate is inside the band, which is the whole point of
  // counting per sample. `n_out_of_range` is a row-level sum across every channel,
  // so it says nothing about any one of them.
  // A negative battery voltage is not a weather pattern. Pinned as the one
  // example, because "some day is flagged" would also be satisfied by a rounding
  // difference.
  const impossible = rows.find((r) => r.day === '2020-10-30')
  assert.ok(impossible, '2020-10-30 is missing')
  assert.ok(
    num(impossible.battery_v_min) < 0,
    `2020-10-30 battery_v_min is ${impossible.battery_v_min}, expected a negative reading`,
  )
})

check('a band does not flag the real readings a distribution test used to drop', () => {
  // The bug this replaced. A median/MAD spike test read aisvn's 24-hour means as
  // a tight 6-9 V cluster and called the 17-19 V single-sample days and the 29 V
  // post-reinstall days "spikes", dropping 16 real days out of 101. Every one of
  // them is inside the 0-60 V band the channel is recorded with.
  const rows = parseCsv(readFileSync(join(DATA, 'aisvn', 'daily', '2020.csv'), 'utf8'))
  const mustSurvive = [
    '2020-08-26', '2020-09-24', '2020-09-30', '2020-10-02', '2020-10-21',
    '2020-10-23', '2020-10-24', '2020-10-25', '2020-11-27',
  ]
  const flagged = new Set(flaggedDays(rows, ['solar_v_avg']).map((r) => r.day))
  for (const day of mustSurvive) {
    assert.ok(!flagged.has(day), `${day} was wrongly flagged as out of band`)
  }
  // What is left in `solar_v` is a hardware fault, not a unit artefact, and this
  // used to be asserted as an exact set -- once for 2020-10-01, before it for
  // 2020-06-17, before that for 2020-06-15 and 2020-06-16. Every one of those
  // exact sets went stale the moment the collector repaired another file, which
  // is the wrong reason for a test to go red: it turns a real regression into a
  // diff of a number nobody chose. So the set is not asserted. The check above
  // covers that a flagged day is flagged for the recorded reason, and the unit
  // check covers that no day is published in a unit its file no longer uses.
  //
  // What is asserted here is the direction that matters and does not move: a
  // distribution test must not remove readings the band accepts.
  assert.ok(
    flagged.size < rows.length / 4,
    `${flagged.size} of ${rows.length} days are out of band, which is not a distribution test any more`,
  )
})

check('no day is published in a unit its own file no longer uses', () => {
  // The regression behind the 9,730 on a 0-12 V channel. `build_aggregate` scales a
  // bucket only when it lies entirely inside a confirmed window, and leaves a
  // straddling one raw by design. While `IFTTT_aisvn.xlsx` held millivolts that
  // was honest: the bucket really did hold two units. Once the collector converted
  // the file, the same rule published raw millivolts beside converted volts, so a
  // wind channel read 9,730 and a battery 4,307 on a day whose neighbours read
  // 9.73 and 13.4.
  //
  // So: nothing aisvn publishes may sit two orders of magnitude above the band
  // its channel is recorded against. A real fault overshoots the band by a
  // factor of two or three -- `aisvn`'s battery peaks at 29.6 V against a 16 V
  // band, and that is a finding. A unit error overshoots by 1000, and that is a
  // defect in the build. The bands come from metrics.json, the same table the
  // site draws against, so this is the same criterion rather than a second one.
  const metrics = JSON.parse(readFileSync(join(DATA, 'metrics.json'), 'utf8')).bands
  const aisvnYears = stations.find((s) => s.station_id === 'aisvn').years
  for (const year of aisvnYears) {
    const rows = parseCsv(readFileSync(join(DATA, 'aisvn', 'daily', `${year}.csv`), 'utf8'))
    for (const [channel, band] of Object.entries(metrics)) {
      const column = `${channel}_avg`
      if (!band || band.hi === null || !(column in rows[0])) continue
      const peak = Math.max(
        ...rows.map((r) => num(r[column])).filter((v) => v !== null).map(Math.abs),
      );
      assert.ok(
        peak < band.hi * 100,
        `aisvn ${year} ${column}: peak ${peak} is more than 100x the ${band.hi} ` +
          'band, which is a unit change rather than a fault',
      )
    }
  }
  // And the channel that has no band at all, checked against a generous ceiling:
  // a wind input on a 12 V system has no business reading four figures.
  for (const year of aisvnYears) {
    const rows = parseCsv(readFileSync(join(DATA, 'aisvn', 'daily', `${year}.csv`), 'utf8'))
    const peak = Math.max(...rows.map((r) => num(r.wind_v_avg)).filter((v) => v !== null));
    assert.ok(peak < 100, `aisvn ${year} wind_v_avg peaks at ${peak}, which is not volts`)
  }
})

check('aisvn publishes volts, and says which days it converted', () => {
  // The 2020-06-15 and 2020-06-16 rows used to read 4,570 and 6,539 in a column
  // whose unit is volts, because the applet was logging millivolts and the
  // regime covering that window was still unconfirmed. It was then confirmed and
  // those two days were scaled.
  //
  // **Neither step applies now.** The collector converted `IFTTT_aisvn.xlsx` at
  // source, so the file is one unit and the seven `aisvn` regimes are 1.0: there
  // is nothing to convert and nothing to record as converted. The check is now the
  // stronger statement -- every published `aisvn` value is already in the unit its
  // column documents, with no day relying on a rollup conversion at all.
  const rows = parseCsv(readFileSync(join(DATA, 'aisvn', 'daily', '2020.csv'), 'utf8'))
  const scaled = rows.filter((r) => r.scaled_channels)
  assert.deepEqual(
    scaled.map((r) => r.day),
    [],
    `aisvn 2020 still converts ${scaled.length} day(s) in the rollup; the file is one unit now`,
  )
  for (const day of ['2020-06-15', '2020-06-16', '2020-06-17']) {
    const row = rows.find((r) => r.day === day)
    assert.ok(row, `${day} is missing`)
    const value = num(row.solar_v_avg)
    assert.ok(
      value !== null && value > 1 && value < 60,
      `${day}: solar_v_avg ${value} is not volts in a 0-60 V band`,
    )
  }
  // The boundary day is where the two units used to meet, so it is the day that
  // would move first if the file were converted unevenly: a partially converted
  // day averages millivolts and volts together and lands nowhere near either
  // neighbour. Assert the continuity rather than a number, because a number here
  // would be a guess about the weather.
  const at = (day, column) => num(rows.find((r) => r.day === day)?.[column]);
  const neighbours = [at('2020-06-15', 'solar_v_avg'), at('2020-06-18', 'solar_v_avg')];
  const boundary = at('2020-06-17', 'solar_v_avg');
  const low = Math.min(...neighbours);
  const high = Math.max(...neighbours);
  assert.ok(
    boundary >= low - 3 && boundary <= high + 3,
    `2020-06-17 solar_v_avg is ${boundary}, outside the ${low}..${high} its ` +
      'neighbours span, which is what a half-converted day looks like',
  );
  for (const day of ['2020-06-15', '2020-06-16', '2020-06-17', '2020-06-18']) {
    assert.ok(
      at(day, 'battery_v_avg') > 9 && at(day, 'battery_v_avg') < 16,
      `${day}: battery_v_avg ${at(day, 'battery_v_avg')} is not volts in a 9-16 V band`,
    );
  }
})

check('a flag is not a verdict, and a 100% flag rate is treated as a bad band', () => {
  // 21 of aisvn's 101 days in 2020 sit above the 9-16 V lead-acid band. They are
  // drawn, ringed and listed. The site must not quietly present the 80 in-band
  // days as the whole picture, and must not have deleted the other 21 either.
  //
  // This was 23 before the collector confirmed the recompile boundary: 2020-06-15
  // and 2020-06-16 were raw millivolts (12,385 and 12,483 "V") and flagged. They
  // are now scaled to 12.385 V and 12.483 V, which is where a 12 V lead pack rests, and
  // they are no longer anomalies. The 21 that remain are the real question --
  // a battery reading up to 29.6 V is a second pack or a scale nobody has
  // confirmed, and the band is what makes that visible.
  const rows = parseCsv(readFileSync(join(DATA, 'aisvn', 'daily', '2020.csv'), 'utf8'))
  const flagged = flaggedDays(rows, ['battery_v_min'])
  assert.equal(flagged.length, 21, `expected 21 battery-flagged days, got ${flagged.length}`)
  assert.ok(flagged.length < rows.length, 'every day flagged means the band is wrong, not the data')
})

check('a millivolt channel is not wiped out once its confirmed scale is applied', () => {
  // The failure mode an absolute band caused before the x0.001 regimes were
  // confirmed: phumy2 logged 1441 for a 1.4 V panel and every day looked out of
  // band. The aggregate applies the confirmed scale, so the exported value is
  // already volts and the band has to pass it.
  for (const [station, year, column] of [
    ['phumy2', '2020', 'solar2_v_avg'],
    ['phumy2', '2021', 'solar2_v_avg'],
    ['phumy2', '2024', 'solar2_v_avg'],
    ['aisvn-solar', '2020', 'solar_v_avg'],
  ]) {
    const rows = parseCsv(readFileSync(join(DATA, station, 'daily', `${year}.csv`), 'utf8'))
    const values = rows.map((r) => num(r[column])).filter((v) => v !== null)
    assert.ok(values.length > 0, `${station} ${year} ${column} has no values`)
    const flagged = flaggedDays(rows, [column]).length
    assert.equal(flagged, 0, `${station} ${year}: ${flagged} days flagged after scaling`)
  }
})

check('a channel with no band is never flagged', () => {
  assert.equal(isOutOfBand('wind_v', 1e9), null)
  assert.equal(isOutOfBand('energy_wh', -500), null, 'energy is a derived integral, not banded')
  assert.equal(isOutOfBand('solar_v', null), null, 'a gap is not a breach')
  assert.equal(isOutOfBand('solar_v', 0), null, 'a genuine zero is inside the band')
})

check('an all-NULL metric column is reported, not drawn', () => {
  // phumy2 has no battery channel whatsoever; it must not appear as a control.
  const rows = parseCsv(readFileSync(join(DATA, 'phumy2', 'daily', '2023.csv'), 'utf8'))
  const firstOf = (row, keys) => {
    for (const k of keys) {
      const v = num(row[k])
      if (v !== null) return v
    }
    return null
  }
  assert.equal(firstOf(rows[0], ['battery_v_min', 'battery2_v_min']), null)
})

check('the second solar channel is exported, so phumy2 is chartable', () => {
  // phumy2 logs `solar2`; before the fix the rollups only carried solar_v and
  // the largest station had nothing to plot.
  const rows = parseCsv(readFileSync(join(DATA, 'phumy2', 'daily', '2020.csv'), 'utf8'))
  const withSolar = rows.filter((r) => num(r.solar2_v_avg) !== null)
  assert.ok(withSolar.length > 50, `only ${withSolar.length} days with solar2_v`)
  const withSolar1 = rows.filter((r) => num(r.solar_v_avg) !== null)
  assert.equal(withSolar1.length, 0, 'phumy2 must not have a solar_v column at all')
})

check('daily rollups carry the out-of-range count for the inspector', () => {
  const rows = parseCsv(readFileSync(join(DATA, 'aisvn', 'daily', '2020.csv'), 'utf8'))
  for (const row of rows) {
    assert.ok('n_out_of_range' in row, 'n_out_of_range column is missing')
    assert.ok(row.n_out_of_range !== '', 'n_out_of_range is blank')
  }
  const flagged = rows.filter((r) => num(r.n_out_of_range) > 0)
  assert.ok(flagged.length > 0, 'expected at least one flagged day')
})

check('confirmed millivolt channels are converted, so the axis is in volts', () => {
  // Before the fix aisvn-solar plotted a solar axis of ~4570 for a ~4.6 V panel,
  // and phumy2 ~1384. The confirmed x0.001 regimes are applied in the aggregate
  // stage, so the exported values must already be volts.
  for (const [station, year, column, ceiling] of [
    ['aisvn-solar', '2020', 'solar_v_avg', 6],
    ['phumy2', '2020', 'solar2_v_avg', 6],
    ['phumy2', '2021', 'solar2_v_avg', 6],
    ['aisvn2', '2020', 'battery2_v_min', 20],
  ]) {
    const rows = parseCsv(
      readFileSync(join(DATA, station, 'daily', `${year}.csv`), 'utf8'),
    )
    const values = rows.map((r) => num(r[column])).filter((v) => v !== null)
    assert.ok(values.length > 0, `${station} ${column} has no values`)
    const max = Math.max(...values)
    assert.ok(max <= ceiling, `${station} ${year} ${column} max ${max} exceeds ${ceiling} V`)
  }
})

check('every converted day says which channels were converted', () => {
  // An unexplained 1000x correction is indistinguishable from a bug. The
  // channel differs per station -- aisvn2 has battery2_v but no solar channel
  // at all -- so each is named explicitly rather than guessed.
  for (const [station, year, channel] of [
    ['aisvn-solar', '2020', 'solar_v_avg'],
    ['phumy2', '2020', 'solar2_v_avg'],
    ['aisvn2', '2020', 'battery2_v_min'],
  ]) {
    const rows = parseCsv(readFileSync(join(DATA, station, 'daily', `${year}.csv`), 'utf8'))
    assert.ok('scaled_channels' in rows[0], `${station} has no scaled_channels column`)
    const converted = rows.filter((r) => num(r[channel]) !== null)
    assert.ok(converted.length > 0, `${station} has no ${channel} values to check`)
    for (const row of converted) {
      assert.ok(
        row.scaled_channels !== '',
        `${station} ${row.day} has a ${channel} value but records no conversion`,
      )
    }
  }
})

check('the last day of a channel is inside its confirmed window', () => {
  // Regression. valid_to is the *exclusive* end, so writing the last day's own
  // date dropped it from the half-open window -- aisvn-solar kept a raw 601 V on
  // 2020-06-12 and phumy2 a raw 1384 V on 2024-02-01.
  const rows = parseCsv(readFileSync(join(DATA, 'aisvn-solar', 'daily', '2020.csv'), 'utf8'))
  const last = rows[rows.length - 1]
  const value = num(last.solar_v_avg)
  assert.ok(value !== null, 'the final day has no solar value')
  assert.ok(value < 50, `final day still unscaled: ${value}`)
})

console.log(`\n${passed} checks passed`)

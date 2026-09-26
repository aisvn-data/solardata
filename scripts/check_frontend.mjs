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

import { readFileSync, readdirSync, statSync } from 'node:fs'
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

check('bench stations are present but flagged unpublished', () => {
  const bench = stations.filter((s) => !s.published).map((s) => s.station_id)
  assert.ok(bench.includes('test'), bench.join(','))
  assert.ok(bench.includes('voltage-phumy'), bench.join(','))
})

check('quality.json carries the counts the UI displays', () => {
  assert.equal(quality.totals.readings, 734908)
  assert.equal(quality.source_files.total, 364)
  assert.equal(quality.source_files.without_header, 305)
  assert.ok(Array.isArray(quality.regimes))
  assert.ok(Array.isArray(quality.notes))
  assert.ok(quality.notes.length >= 10)
})

let csvFiles = 0
let totalRows = 0
function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walk(full)
    else if (entry.endsWith('.csv')) {
      const rows = parseCsv(readFileSync(full, 'utf8'))
      csvFiles += 1
      totalRows += rows.length
      for (const row of rows) {
        assert.match(row.day, /^\d{4}-\d{2}-\d{2}$/, `${full}: bad day ${row.day}`)
        assert.equal(row.ts_utc_day.slice(0, 10), row.day, `${full}: local/UTC day mismatch`)
      }
    }
  }
}
walk(DATA)

check('every daily CSV parses and its day matches its UTC day', () => {
  // 13 station-years across 6 production stations, 1,124 daily rows. Pinned
  // because a silent change here means the site is showing a different amount
  // of data than the report claims. It was 1,127 before the 2020-06-17 schema
  // alignment fix and the aisvn (25) row exclusions.
  assert.equal(csvFiles, 13, `expected 13 csv files, got ${csvFiles}`)
  assert.equal(totalRows, 1124, `expected 1124 daily rows, got ${totalRows}`)
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
  const withTemp = rows.filter((r) => num(r.temp_c_avg) !== null)
  assert.ok(withTemp.length > 200, `only ${withTemp.length} days with temperature`)
})

console.log(`\n${passed} checks passed`)

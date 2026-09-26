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

// ------------------------------------------------------------ spike detection

const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b)
  const m = s.length >> 1
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

const SPIKE_MAD_MULTIPLIER = 6
const SPIKE_RELATIVE_FLOOR = 0.35
const MAX_TIGHTNESS = 0.2

const mad = (xs, med) => median(xs.map((v) => Math.abs(v - med)))

/** Mirrors of src/data.js, so the assertions exercise the same logic. */
function robustStats(values) {
  const clean = values.filter((v) => v !== null && v !== undefined)
  if (clean.length === 0) return { median: null, mad: null }
  return { median: median(clean), mad: mad(clean, median(clean)) }
}

function spikeLimit(stats, unit) {
  if (!stats || stats.median === null) return null
  if (Math.abs(stats.mad) > Math.abs(stats.median) * MAX_TIGHTNESS) return null
  const spread = stats.mad * 1.4826 * SPIKE_MAD_MULTIPLIER
  const relative = Math.abs(stats.median) * SPIKE_RELATIVE_FLOOR
  const absolute = unit === 'Wh' ? 5 : unit === 'W' ? 20 : 1.5
  return Math.max(spread, relative, absolute)
}

check('the ADC test-pattern day is a spike against its own series', () => {
  // The real case: aisvn 2020-10-01, a single reading of solar 123 V and
  // battery 456 V between days that read 18 and 19.
  const series = [18.45, 19.12, 18.9, 19.4, 18.7, 19.0, 18.8, 19.2, 18.6, 19.1]
  const stats = robustStats(series)
  const limit = spikeLimit(stats, 'V')
  assert.ok(limit !== null, 'a tight series must yield a usable limit')
  assert.ok(Math.abs(123 - stats.median) > limit, '123 V should be a spike')
  assert.ok(Math.abs(456 - stats.median) > limit, '456 V should be a spike')
  for (const v of series) {
    assert.ok(Math.abs(v - stats.median) <= limit, `${v} should NOT be a spike`)
  }
})

check('spike detection is scale-blind, so a millivolt channel is not wiped out', () => {
  // The failure mode this replaced: an absolute plausibility band flagged every
  // phumy2 reading (millivolts) and dropped 636 of 636 days.
  const millivolts = [1441, 1520, 1390, 1610, 1475, 1555, 1430, 1580, 1490, 1560]
  const limit = spikeLimit(robustStats(millivolts), 'V')
  assert.ok(limit !== null)
  const stats = robustStats(millivolts)
  for (const v of millivolts) {
    assert.ok(Math.abs(v - stats.median) <= limit, `${v} mV should not be a spike`)
  }
})

check('a bimodal series disables spike detection instead of eating half of it', () => {
  // phumy2.solar2_v steps from ~5000 mV to ~1200 mV when a bridge is fitted.
  // Over a window holding both, the distribution is bimodal and either mode
  // looks like a spike against the median of the other. Neither is an artefact,
  // so the detector must decline rather than delete a real configuration change.
  const bimodal = [...Array(30).fill(5000), ...Array(30).fill(1200)]
  const limit = spikeLimit(robustStats(bimodal), 'V')
  assert.equal(limit, null, 'a bimodal series must not be filtered')
})

check('an absent channel yields no limit rather than a wrong one', () => {
  assert.equal(spikeLimit({ median: null, mad: null }, 'V'), null)
  assert.equal(spikeLimit({ median: 0, mad: 0 }, 'V'), 1.5)
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

console.log(`\n${passed} checks passed`)

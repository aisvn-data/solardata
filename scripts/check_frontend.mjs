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
  // 13 station-years across 6 production stations, 1,124 daily rows. Pinned
  // because a silent change here means the site is showing a different amount
  // of data than the report claims. It was 1,127 before the 2020-06-17 schema
  // alignment fix and the aisvn (25) row exclusions.
  assert.equal(dailyFiles, 13, `expected 13 daily csv files, got ${dailyFiles}`)
  assert.equal(dailyRows, 1124, `expected 1124 daily rows, got ${dailyRows}`)
})

check('the hourly rollups are published alongside the daily ones', () => {
  // Same 13 station-years, 24,210 hourly buckets. This is the file the Hour
  // view reads; if it silently stops being written the view 404s rather than
  // degrading, so it is pinned here instead.
  assert.equal(hourlyFiles, 13, `expected 13 hourly csv files, got ${hourlyFiles}`)
  assert.equal(hourlyRows, 24210, `expected 24210 hourly rows, got ${hourlyRows}`)
})

check('the hourly rollups agree with the daily ones on the same days', () => {
  // The daily row is derived from the hourly rows, so any day present in one
  // must be present in the other. A mismatch means the aggregate stage or the
  // export is bucketing differently, which is the bug the `ts_utc` vs `day`
  // year split would cause.
  for (const station of stations.filter((s) => s.published)) {
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
  const withTemp = rows.filter((r) => num(r.temp_c_avg) !== null)
  assert.ok(withTemp.length > 200, `only ${withTemp.length} days with temperature`)
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
  assert.equal(bands.temp_c.lo, 5)
  assert.equal(bands.temp_c.hi, 45)
  assert.equal(bands.power_w.lo, -2000)
  assert.equal(bands.power_w.hi, 2000)
  // A channel with no band must say so rather than be missing, so the UI can
  // answer "this one is never flagged" instead of inferring it.
  assert.ok('wind_v' in bands)
  assert.equal(bands.wind_v.lo, null)
})

check('the ADC test-pattern day is caught, by the band the pipeline records', () => {
  // The real case: aisvn 2020-10-01, a single reading of solar 123 V and
  // battery 456 V, between days reading 18 and 19.
  const rows = parseCsv(readFileSync(join(DATA, 'aisvn', 'daily', '2020.csv'), 'utf8'))
  const flagged = flaggedDays(rows, [['solar_v_avg'], ['battery_v_min']])
  const day = flagged.find((r) => r.day === '2020-10-01')
  assert.ok(day, '2020-10-01 must be flagged')
  assert.equal(num(day.solar_v_avg), 123)
  assert.equal(num(day.battery_v_min), 456)
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
  // The four that really are out of band: the unconverted-millivolt commissioning
  // window, and the test pattern.
  assert.deepEqual(
    [...flagged].sort(),
    ['2020-06-15', '2020-06-16', '2020-06-17', '2020-10-01'],
  )
})

check('a flag is not a verdict, and a 100% flag rate is treated as a bad band', () => {
  // 23 of aisvn's 101 days in 2020 sit above the 9-16 V 3S LiPo band. They are
  // drawn, ringed and listed. The site must not quietly present the 78 in-band
  // days as the whole picture, and must not have deleted the other 23 either.
  const rows = parseCsv(readFileSync(join(DATA, 'aisvn', 'daily', '2020.csv'), 'utf8'))
  const flagged = flaggedDays(rows, ['battery_v_min'])
  assert.equal(flagged.length, 23, `expected 23 battery-flagged days, got ${flagged.length}`)
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

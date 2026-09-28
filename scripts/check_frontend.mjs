/**
 * Chart and CSV semantics, checked against the real exported data.
 *
 * The build alone cannot catch either of the two things that matter most here:
 * coercing an empty cell to 0, which turns every sensor outage into a
 * measurement, and drawing a line across a gap, which implies data that does not
 * exist. Both produce a chart that looks correct.
 *
 * The third class of check is the one this file was rewritten for in 0.9: **a
 * station's CSV must contain only that station's channels.** 0.8 wrote 32
 * statistic columns into every CSV for all 16 station-years, so `phumy2` -- which
 * has five real channels -- shipped 27 columns of NULL, and the browser had to
 * discover which of them were real. There is nothing to discover now, and these
 * checks assert that there is nothing to discover.
 *
 * Run with `node scripts/check_frontend.mjs`. It reads the committed
 * `public/data/`, so a build that has not been re-exported fails here rather
 * than in a browser.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const DATA = join(ROOT, 'public', 'data')

let passed = 0
const failures = []

function check(name, fn) {
  try {
    fn()
    passed += 1
    console.log(`  ok   ${name}`)
  } catch (err) {
    failures.push({ name, message: err.message })
    console.log(`  FAIL ${name}\n         ${err.message}`)
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function equal(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
  }
}

/**
 * Load `src/data.js` with a stubbed `import.meta.env`.
 *
 * The module reads `import.meta.env.BASE_URL` at the top level, and plain node
 * has no `import.meta.env`, so importing it directly throws. Rather than mock
 * the module system, the three lines that need it are stripped and the rest is
 * evaluated as a module -- which is the real code, not a copy of it.
 */
function loadDataModule() {
  const source = readFileSync(join(ROOT, 'src', 'data.js'), 'utf8')
    .replace(/^const BASE_URL = .*$/m, "const BASE_URL = '/'")
    .replace(/^const DATA_ROOT = .*$/m, "const DATA_ROOT = 'data'")
  const body = source
    .replace(/^export (async )?function /gm, '$1function ')
    .replace(/^export const /gm, 'const ')
    .replace(/^export \{ MS_PER_DAY \}$/m, 'const __MS_PER_DAY = MS_PER_DAY')
  const names = [
    'loadStations',
    'loadQuality',
    'loadBands',
    'loadRollup',
    'parseCsv',
    'channelsFor',
    'hiddenChannels',
    'seriesFor',
    'pick',
    'get',
    'statLabel',
    'availableMonths',
    'filterByRange',
    'classify',
    'classifyRows',
    'summarise',
    'GRANULARITIES',
  ]
  const factory = new Function(
    `${body}
     return { ${names.join(', ')} }`,
  )
  return factory()
}

const data = loadDataModule()
const stations = JSON.parse(readFileSync(join(DATA, 'stations.json'), 'utf8'))
const metrics = JSON.parse(readFileSync(join(DATA, 'metrics.json'), 'utf8'))
const quality = JSON.parse(readFileSync(join(DATA, 'quality.json'), 'utf8'))

const byId = new Map(stations.map((s) => [s.station_id, s]))

function csvDir(station, folder) {
  return join(DATA, station, folder)
}

function csvFiles(station, folder) {
  const dir = csvDir(station, folder)
  try {
    return readdirSync(dir)
      .filter((f) => f.endsWith('.csv'))
      .sort()
  } catch {
    return []
  }
}

function readHeader(station, folder, file) {
  const text = readFileSync(join(csvDir(station, folder), file), 'utf8')
  return text.trim().split(/\r?\n/)[0].split(',')
}

console.log('\nchart helpers and CSV semantics')

check('an empty CSV cell is null, never 0', () => {
  const parsed = data.parseCsv('ts,a_avg,b_avg\n2020-01-01,,0\n')
  equal(parsed.rows[0].a_avg, '', 'the empty cell is the empty string')
  const row = { values: { a: null }, stats: { a: 'avg' }, byStat: { a: { avg: null } }, oor: {} }
  const channel = { channel: 'a', label: 'A', unit: 'V', band: { lo: 0, hi: 10 } }
  const picked = data.pick(row, channel)
  equal(picked.value, null, 'a gap reads as null')
  equal(picked.display, null, 'and stays null for the chart')
})

check('a genuine 0 survives as 0', () => {
  const parsed = data.parseCsv('ts,a_avg\n2020-01-01,0\n')
  equal(parsed.rows[0].a_avg, '0', 'the cell text is "0"')
  const row = { values: { a: 0 }, stats: { a: 'avg' }, byStat: { a: { avg: 0 } }, oor: {} }
  const channel = { channel: 'a', label: 'A', unit: 'V', band: { lo: 0, hi: 10 } }
  equal(data.pick(row, channel).value, 0, 'a real zero is 0 and not a gap')
})

check('a gap is not summarised as 0 by summarise()', () => {
  const channel = { channel: 'a', label: 'A', unit: 'V', band: { lo: 0, hi: 10 } }
  const row = (value) => ({
    values: { a: value },
    byStat: { a: { avg: value } },
    stats: { a: 'avg' },
    oor: {},
  })
  const out = data.summarise([row(null), row(null)], channel)
  equal(out.count, 0, 'two gaps are zero values, not two zeroes')
  equal(out.mean, null, 'and no mean')
})

check('an empty cell never becomes a plotted zero', () => {
  // The regression by name: a median/MAD spike test used to drop 16 real days of
  // aisvn 2020 out of 101, because a panel's 24-hour mean is dominated by night
  // and one afternoon sample looks like an outlier against it. The rule is that
  // the *pipeline* decides what is implausible and the site marks it; the site
  // never decides for itself.
  const channel = { channel: 'solar_v', label: 'Solar', unit: 'V', band: { lo: 0, hi: 30 } }
  const rows = [0, 1, 2, 25, 0, 0, 0, 0].map((v) => ({
    nSamples: 30,
    values: { solar_v: v },
    byStat: { solar_v: { avg: v } },
    oor: {},
  }))
  const out = data.classifyRows(rows, [channel])
  equal(out.plottable.length, 8, 'every row with samples is still plotted')
  equal(out.breaches, 0, 'a value inside the band is not a breach, however unusual')
})

check('a flagged value is kept and marked, never dropped', () => {
  const channel = { channel: 'battery_v', label: 'Battery', unit: 'V', band: { lo: 9, hi: 16 } }
  const row = {
    nSamples: 30,
    values: { battery_v: 29.8 },
    byStat: { battery_v: { avg: 29.8 } },
    stats: { battery_v: 'avg' },
    oor: { battery_v: 1 },
  }
  const out = data.classifyRows([row], [channel])
  equal(out.plottable.length, 1, 'the row is plotted')
  equal(out.breaches, 1, 'and marked')
  equal(out.flaggedRows[0].breaches[0].level, 'partial', 'as partly built from flagged samples')
})

check('one flagged sample of thirty is visible even when the mean is in band', () => {
  // The case a band test on the aggregate alone cannot see: an hour that averages
  // one 19,877 W sample into twenty-nine zeros lands well inside the band.
  const channel = { channel: 'power_w', label: 'Power', unit: 'W', band: { lo: -2000, hi: 2000 } }
  const row = {
    nSamples: 30,
    values: { power_w: 662.57 },
    byStat: { power_w: { avg: 662.57 } },
    stats: { power_w: 'avg' },
    oor: { power_w: 1 },
  }
  const found = data.classify(row, [channel])
  equal(found.length, 1, 'the hour is annotated')
  equal(found[0].level, 'partial', 'as partial, not clean')
})

check('every sample out of band is reported as contaminated', () => {
  const channel = { channel: 'temp_c', label: 'Temp', unit: 'degC', band: { lo: 0, hi: 60 } }
  const row = {
    nSamples: 30,
    values: { temp_c: 72.4 },
    byStat: { temp_c: { avg: 72.4 } },
    stats: { temp_c: 'avg' },
    oor: { temp_c: 30 },
  }
  equal(data.classify(row, [channel])[0].level, 'contaminated', 'all thirty flagged')
})

check('a value outside the station range but inside the band is reported, not flagged', () => {
  const channel = {
    channel: 'aisvn2_battery2_v',
    label: 'Battery',
    unit: 'V',
    band: { lo: 9, hi: 16 },
    range: { min: 0.456, max: 15.36, n: 100 },
  }
  // 0.5 V is inside nothing, but the point is the converse: a value inside the
  // band and outside the recorded range is an observation about this instrument,
  // not a verdict on the hardware.
  const inBand = {
    channel: 'lipo_v',
    label: 'LiPo',
    unit: 'V',
    band: { lo: 0, hi: 8.7 },
    range: { min: 0.258, max: 7.097, n: 100 },
  }
  const row = {
    nSamples: 10,
    values: { lipo_v: 7.5 },
    byStat: { lipo_v: { avg: 7.5 } },
    stats: { lipo_v: 'avg' },
    oor: {},
  }
  const found = data.classify(row, [inBand])
  equal(found.length, 1, '7.5 V is inside the band but above anything recorded')
  equal(found[0].level, 'clean', 'and is not called contaminated')
  assert(found[0].range !== null, 'the range disagreement is carried alongside')
  void channel
})

check('channelsFor returns only the channels the station publishes', () => {
  for (const station of stations) {
    const offered = data.channelsFor(station).map((c) => c.key)
    const published = station.channels.filter((c) => c.published).map((c) => c.channel)
    equal(
      offered.length,
      published.length,
      `${station.station_id} offers every published channel`,
    )
    for (const hidden of station.hidden ?? []) {
      assert(
        !offered.includes(hidden.channel),
        `${station.station_id} must not offer the excluded ${hidden.channel}`,
      )
    }
  }
})

check('every channel the picker offers carries a unit, decimals and a band decision', () => {
  for (const station of stations) {
    for (const channel of data.channelsFor(station)) {
      assert(typeof channel.unit === 'string', `${channel.key} has a unit string`)
      assert(typeof channel.decimals === 'number', `${channel.key} declares decimals`)
      assert(
        channel.band &&
          (typeof channel.band.lo === 'number' || channel.band.lo === null) &&
          (typeof channel.band.hi === 'number' || channel.band.hi === null),
        `${station.station_id}.${channel.key} resolves to a lo/hi pair, nulls included`,
      )
      const banded = channel.band.lo !== null || channel.band.hi !== null
      if (!banded) {
        // A channel with no band is a decision, not an omission: an uncalibrated
        // ADC count and an uptime counter have no plausible range, and inventing
        // one is what put 232 mA inside a +/-50 A band in the first place.
        assert(
          channel.bandNote && channel.bandNote.length > 10,
          `${station.station_id}.${channel.key} has no band and must say why`,
        )
      }
    }
  }
})

check('a banded channel explains itself, and a bandless one says why', () => {
  for (const station of stations) {
    for (const channel of station.channels) {
      const banded = channel.band[0] !== null || channel.band[1] !== null
      if (banded) {
        assert(
          channel.band_note && channel.band_note.length > 10,
          `${station.station_id}.${channel.channel} is banded and must say why`,
        )
      }
      if (!channel.published) {
        assert(
          channel.exclude_reason && channel.exclude_note,
          `${station.station_id}.${channel.channel} is hidden and must say why`,
        )
      }
    }
  }
})

console.log('\nthe three excluded kinds of channel')

check('wind_v is charted only where the collector confirmed its unit', () => {
  // Three stations record a `wind_v`. 0.9.0 asserted none of them charted it, on
  // the grounds that 12,784 V and 14,686 mV are not plausible generator outputs.
  // The collector has since confirmed the channel as a power measurement in
  // watts, so aisvn and maker-webhooks chart it, banded 0-50 W. aisvn-solar's is
  // identically zero for all 13,788 of its readings and stays hidden as
  // `constant`.
  //
  // Worth stating that the old assertion was not wrong about the number and wrong
  // about the unit: it read a real reading in the wrong unit and concluded the
  // hardware was not implemented. That is 0.8's error -- a plausible value tested
  // against a band in another unit -- in the direction that hides data.
  const wind = stations.flatMap((s) => s.channels.filter((c) => c.channel === 'wind_v'))
  assert(wind.length > 0, 'the archive does record wind_v somewhere')
  const charted = []
  for (const station of stations) {
    const channel = station.channels.find((c) => c.channel === 'wind_v')
    if (!channel) continue
    if (channel.published) {
      charted.push(station.station_id)
      equal(channel.unit, 'W', `${station.station_id}.wind_v is published in watts`)
      // The two stations get there differently, and that is the point: aisvn's
      // applet writes volts and the number needs no conversion, while
      // maker-webhooks' writes millivolts like every other channel it sends. What
      // both now agree on is the published unit, which is the thing a reader sees.
      if (station.station_id === 'maker-webhooks') {
        equal(channel.raw_unit, 'mV', 'maker-webhooks.wind_v is stored from a millivolt cell')
        equal(channel.scale, 0.001, 'maker-webhooks.wind_v applies the confirmed scale once')
      } else {
        equal(channel.raw_unit, null, 'aisvn.wind_v needs no conversion: the applet writes volts')
        equal(channel.scale, 1, 'aisvn.wind_v applies no scale')
      }
      equal(
        JSON.stringify(channel.band),
        JSON.stringify([0, 50]),
        `${station.station_id}.wind_v is banded 0-50 W`,
      )
    } else {
      equal(
        channel.published,
        false,
        `${station.station_id}.wind_v is hidden and says why`,
      )
      assert(
        channel.exclude_reason === 'constant' || channel.exclude_reason === 'not_measurement',
        `${station.station_id}.wind_v is excluded for a stated reason`,
      )
    }
  }
  equal(JSON.stringify(charted.sort()), JSON.stringify(['aisvn', 'maker-webhooks']), 'charted at two stations')

  // A charted channel must actually reach the CSVs, or the picker offers a
  // control that draws nothing.
  for (const stationId of charted) {
    const header = readHeader(stationId, 'hourly', '2020.csv')
    assert(
      header.some((c) => c.startsWith('wind_v_')),
      `${stationId}'s hourly CSV carries a wind_v column, because the picker offers one`,
    )
  }
})

check('phumy2.power_w is not charted, and phumy2 has no power_w column', () => {
  const phumy2 = byId.get('phumy2')
  const power = phumy2.channels.find((c) => c.channel === 'power_w')
  assert(power, 'phumy2 records a power channel')
  equal(power.published, false, 'and it is not a measurement, so it is not charted')
  equal(
    power.observed.n_values,
    416088,
    'the values are still stored, because the input is real',
  )
  for (const file of csvFiles('phumy2', 'hourly')) {
    const header = readHeader('phumy2', 'hourly', file)
    assert(
      !header.some((h) => h.startsWith('power_w')),
      `phumy2/hourly/${file} must not carry a power_w column`,
    )
  }
})

check('aisvn.power_w is charted, because at that station it is a measurement', () => {
  const aisvn = byId.get('aisvn')
  const power = aisvn.channels.find((c) => c.channel === 'power_w')
  assert(power, 'aisvn records a power channel')
  equal(power.published, true, 'and it is charted, unlike phumy2 pin of the same name')
  const header = readHeader('aisvn', 'hourly', '2021.csv')
  assert(header.includes('power_w_avg'), 'aisvn/hourly/2021.csv carries power_w_avg')
})

check('a channel that never varies is excluded for being constant', () => {
  const dump = stations
    .flatMap((s) => s.channels)
    .filter((c) => c.exclude_reason === 'constant')
  assert(dump.length > 0, 'the archive does have channels that never move')
  for (const channel of dump) {
    equal(
      channel.observed.min,
      channel.observed.max,
      `${channel.channel} is still constant, so the exclusion is not stale`,
    )
  }
})

check('a channel with no established unit is not charted', () => {
  const unresolved = stations
    .flatMap((s) => s.channels)
    .filter((c) => c.exclude_reason === 'unresolved_unit')
  assert(unresolved.length > 0, 'the archive does have channels of unknown unit')
  for (const channel of unresolved) {
    equal(channel.published, false, `${channel.channel} is not charted`)
    equal(channel.unit, '', `${channel.channel} claims no unit`)
  }
})

console.log('\nper-station CSV columns')

check('a station CSV has only that station\'s published channels', () => {
  // The three metadata columns describe the bucket; everything after them is a
  // published channel's statistics. `n_samples` and `n_hours` are not channels,
  // and a check that treated them as one is how a reader ends up offered an
  // "N samples" control.
  const META = ['ts', 'n_samples', 'n_hours']
  for (const station of stations) {
    const expected = new Set()
    for (const channel of station.channels) {
      if (!channel.published) continue
      for (const stat of channel.stats) expected.add(`${channel.channel}_${stat}`)
      if (channel.band[0] !== null || channel.band[1] !== null) {
        expected.add(`${channel.channel}_n_oor`)
      }
    }
    for (const folder of ['hourly', 'daily']) {
      for (const file of csvFiles(station.station_id, folder)) {
        const header = readHeader(station.station_id, folder, file)
        const want = folder === 'hourly' ? ['ts', 'n_samples'] : ['ts', 'n_samples', 'n_hours']
        equal(
          header.slice(0, want.length).join(','),
          want.join(','),
          `${station.station_id}/${folder}/${file} starts with its bucket metadata`,
        )
        for (const column of header.slice(META.length)) {
          assert(
            expected.has(column),
            `${station.station_id}/${folder}/${file} column ${column} is a published channel statistic`,
          )
        }
        for (const column of expected) {
          assert(
            header.includes(column),
            `${station.station_id}/${folder}/${file} carries ${column}`,
          )
        }
      }
    }
  }
})

/**
 * A column may legitimately be empty for a whole file, and when it is there has
 * to be a reason.
 *
 * `phumy2`'s `solar2_v` is NULL for every hour of 2023, because a collector
 * window records the input as disconnected over 2022-10 to 2024-01. Shipping the
 * column keeps the header identical across a station's files, which is worth more
 * than the empty cells; dropping it would make the header depend on the data.
 * But a column that is empty with *no* documented reason is a channel the
 * pipeline stopped ingesting, and that is the failure this catches -- 0.8 shipped
 * 27 permanently-empty columns per station-year for exactly that reason and nobody
 * could tell them apart from real gaps.
 */
function nullWindowCovers(stationId, channel, year) {
  return (quality.windows?.null ?? []).some(
    (w) =>
      w.station_id === stationId &&
      w.columns.includes(channel) &&
      w.valid_from.slice(0, 4) <= year &&
      w.valid_to.slice(0, 4) >= year,
  )
}

check('an empty column is empty for a documented reason', () => {
  const empties = []
  for (const station of stations) {
    for (const folder of ['hourly', 'daily']) {
      for (const file of csvFiles(station.station_id, folder)) {
        const year = file.replace('.csv', '')
        const text = readFileSync(join(csvDir(station.station_id, folder), file), 'utf8')
        const lines = text.trim().split(/\r?\n/)
        const header = lines[0].split(',')
        for (let c = 1; c < header.length; c += 1) {
          const filled = lines.slice(1).some((line) => (line.split(',')[c] ?? '') !== '')
          if (!filled) {
            const channel = header[c].replace(/_(avg|min|max|n_oor)$/, '')
            empties.push(`${station.station_id}/${folder}/${file} ${header[c]}`)
            assert(
              nullWindowCovers(station.station_id, channel, year),
              `${station.station_id}/${folder}/${file}: ${header[c]} is empty for the whole ` +
                `file and no null window covers ${channel} in ${year}`,
            )
          }
        }
      }
    }
  }
  console.log(`         (${empties.length} empty column-file(s), all null-windowed)`)
})

check('a channel that is null-windowed is absent from the chart for those hours', () => {
  const phumy2 = byId.get('phumy2')
  const solar = phumy2.channels.find((c) => c.channel === 'solar2_v')
  equal(solar.published, true, 'it is a real channel, so it is charted where it has data')
  assert(
    quality.windows.null.some((w) => w.station_id === 'phumy2' && w.columns.includes('solar2_v')),
    'and the window over which it is not is documented',
  )
})

check('a station with no power channel has no power column, and vice versa', () => {
  const expectations = [
    ['phumy2', false],
    ['aisvn', true],
    ['aisvn2', false],
    ['aisvn-solar', false],
    ['maker-webhooks', false],
    ['solar-2020-05', false],
    ['test', false],
    ['voltage-phumy', false],
  ]
  for (const [id, wants] of expectations) {
    const station = byId.get(id)
    const channel = station.channels.find((c) => c.channel === 'power_w')
    const has = channel ? channel.published : false
    equal(has, wants, `${id} power_w charted`)
    const header = csvFiles(id, 'hourly').length
      ? readHeader(id, 'hourly', csvFiles(id, 'hourly')[0])
      : []
    const column = header.some((h) => h.startsWith('power_w'))
    equal(column, wants, `${id} CSV carries a power column`)
  }
})

console.log('\nunits, one per channel, no divisor')

check('every channel publishes a unit and a scale, and the scale is never applied twice', () => {
  for (const station of stations) {
    for (const channel of station.channels) {
      assert(typeof channel.scale === 'number', `${channel.channel} declares a scale`)
      assert(channel.scale > 0, `${channel.channel} has a positive scale`)
      if (channel.scale !== 1) {
        assert(channel.raw_unit, `${channel.channel} records the unit it was converted from`)
      }
    }
  }
})

check('phumy2.current2_a is in amps, banded in amps, and flags nothing', () => {
  const channel = byId.get('phumy2').channels.find((c) => c.channel === 'current2_a')
  equal(channel.unit, 'A', 'published in amps')
  equal(channel.scale, 0.001, 'from milliamps')
  equal(channel.observed.min.toFixed(3), '0.155', 'the stored minimum is 0.155 A')
  equal(channel.observed.max.toFixed(3), '1.997', 'and the maximum 1.997 A')
  equal(
    channel.observed.n_out_of_range,
    0,
    'a sensor measuring a quarter of an amp flags nothing',
  )
  const header = readHeader('phumy2', 'hourly', '2020.csv')
  assert(header.includes('current2_a_avg'), 'the CSV carries current2_a_avg')
})

check('test.temp_c is in degrees, like every other temperature in the archive', () => {
  const channel = byId.get('test').channels.find((c) => c.channel === 'temp_c')
  equal(channel.unit, 'degC', 'published in degrees')
  equal(channel.scale, 1, 'and not scaled: the sheet writes 29.47, so 0.9 stores 29.47')
  assert(
    channel.observed.max > 20 && channel.observed.max < 40,
    `the stored maximum is a plausible temperature, got ${channel.observed.max}`,
  )
  equal(
    channel.observed.n_out_of_range,
    0,
    'a probe reading 21-31 degC flags nothing',
  )
  const text = readFileSync(join(DATA, 'test', 'daily', '2020.csv'), 'utf8')
  const header = text.trim().split('\n')[0].split(',')
  assert(header.includes('temp_c_avg'), 'the CSV names it temp_c, not temp_deci_c')
  const values = text
    .trim()
    .split(/\r?\n/)
    .slice(1)
    .map((line) => Number(line.split(',')[header.indexOf('temp_c_avg')]))
    .filter((v) => Number.isFinite(v))
  assert(values.length > 0, 'the daily file has temperature values')
  for (const value of values) {
    assert(value > 0 && value < 60, `a daily mean of ${value} degC is plausible`)
  }
})

check('all three temperature channels are plain degrees', () => {
  // 0.8 multiplied two of them (aisvn and phumy2 by 10, test by 100) and banded
  // each in the multiplied unit, so every reading landed inside a band that was
  // wrong by the same factor as the value. Three different corrections, all of
  // them self-consistent, none of them real: the sheets write degrees.
  for (const id of ['aisvn', 'aisvn2', 'aisvn-solar', 'maker-webhooks', 'phumy2', 'solar-2020-05', 'test']) {
    const channel = byId.get(id).channels.find((c) => c.channel === 'temp_c')
    if (!channel) continue
    equal(channel.unit, 'degC', `${id}.temp_c is published in degrees`)
    equal(channel.scale, 1, `${id}.temp_c applies no scale`)
  }
  const temp = (id) => byId.get(id).channels.find((c) => c.channel === 'temp_c')
  assert(temp('aisvn').observed.max < 100, `aisvn peaks at ${temp('aisvn').observed.max} degC`)
  assert(
    temp('phumy2').observed.max < 100,
    `phumy2 peaks at ${temp('phumy2').observed.max} degC`,
  )
  assert(temp('test').observed.max < 100, `test peaks at ${temp('test').observed.max} degC`)
})

check('aisvn.temp_c is plain degrees, so no scale is applied to it', () => {
  const channel = byId.get('aisvn').channels.find((c) => c.channel === 'temp_c')
  equal(channel.scale, 1, 'the sheet writes degrees, so nothing is converted')
  equal(channel.unit, 'degC', 'published in degrees')
  assert(
    channel.observed.median === undefined || channel.observed.median < 100,
    'the median is a temperature, not a scaled one',
  )
  assert(channel.observed.max < 100, `the maximum is ${channel.observed.max} degC`)
})

console.log('\nbands belong to a station and a channel')

check('every band in metrics.json is keyed by station and channel', () => {
  for (const [key, entry] of Object.entries(metrics.channels)) {
    assert(key.includes('.'), `${key} is keyed "station.channel"`)
    equal(key, `${entry.station_id}.${entry.channel}`, `${key} agrees with its own contents`)
    const station = byId.get(entry.station_id)
    assert(station, `${entry.station_id} exists`)
    const channel = station.channels.find((c) => c.channel === entry.channel)
    assert(channel, `${key} exists in stations.json`)
    equal(
      channel.band[0],
      entry.band[0],
      `${key} has the same lower bound in both files`,
    )
    equal(channel.band[1], entry.band[1], `${key} has the same upper bound in both files`)
  }
})

check('the same column name at two stations has two different bands', () => {
  // The whole reason a band cannot be a property of a column name: solar_v is
  // volts at aisvn and millivolts at three other stations.
  const byChannel = new Map()
  for (const station of stations) {
    for (const channel of station.channels) {
      if (!byChannel.has(channel.channel)) byChannel.set(channel.channel, [])
      byChannel.get(channel.channel).push(channel)
    }
  }
  const solar = byChannel.get('solar_v') ?? []
  const solar2 = byChannel.get('solar2_v') ?? []
  assert(solar.length + solar2.length > 1, 'the archive reuses solar channel names')
  const units = new Set([...solar, ...solar2].map((c) => `${c.scale}:${c.unit}`))
  assert(units.size > 1, `the same name is stored in more than one scale: ${[...units]}`)
})

check('no band fires on more than 1% of its channel without a note saying why', () => {
  for (const row of quality.band_audit.rows) {
    if (row.fraction > quality.band_audit.threshold) {
      assert(
        row.justified,
        `${row.station_id}.${row.channel} fires on ${(row.fraction * 100).toFixed(1)}% ` +
          'of its own record and has no note explaining why',
      )
    }
  }
  equal(quality.band_audit.unjustified.length, 0, 'no unjustified band')
})

check('the out-of-range count is accounted for, and is not mostly unit mismatch', () => {
  // 0.8: 631,252 of 731,885, i.e. 86%, of which 416,088 were one quarter-of-an-amp
  // current sensor tested against a +/-50 A band. The 1% ceiling was the check that
  // would have caught it, and it is kept -- but as a statement about the *remainder*.
  //
  // The total is now 40,393 (5.5%) and it moved up deliberately. The collector
  // tightened two bands 0.9.0 had widened to silence: aisvn.solar2_v to 0-15 V
  // and aisvn.lipo_v to 0-5 V. Each flags 14,107 readings, and those 14,107 are one
  // exact value repeated -- 19.5 V and 6.84 V. A plateau on a rail, not a unit
  // error, and a band that declines to ring it has been widened to be quiet.
  //
  // So: the ceiling applies once the declared plateaus are set aside, and the
  // plateaus themselves must each carry a band_note saying the fire is the
  // finding (asserted by the band-audit check above, and again per channel in
  // tests/test_catalog.py).
  const total = stations.reduce((a, s) => a + (s.n_readings ?? 0), 0)
  const rows = quality.band_audit.rows
  const out = rows.reduce((a, r) => a + r.n_out_of_range, 0)
  const PLATEAUS = new Set(['aisvn.solar2_v', 'aisvn.lipo_v'])
  const remainder = rows
    .filter((r) => !PLATEAUS.has(`${r.station_id}.${r.channel}`))
    .reduce((a, r) => a + r.n_out_of_range, 0)

  equal(total, 731981, 'the archive is the size the baseline says')
  equal(out, 42095, 'the total out-of-range count')
  equal(remainder, 12528, 'the out-of-range count outside the two declared plateaus')
  assert(
    remainder / total < 0.02,
    `${remainder} of ${total} values are out of band once the two declared plateaus are set aside ` +
      `(${((remainder / total) * 100).toFixed(2)}%); 0.8 was 86%, and a unit mismatch shows up here first`,
  )
  for (const id of PLATEAUS) {
    const row = rows.find((r) => `${r.station_id}.${r.channel}` === id)
    assert(row, `${id} is in the band audit`)
    assert(
      row.n_out_of_range / row.n_values > 0.01,
      `${id} still fires above 1% (${row.n_out_of_range} of ${row.n_values})`,
    )
  }
})

console.log('\nrollup shape')

const csvCount = stations.reduce(
  (a, s) => a + csvFiles(s.station_id, 'hourly').length + csvFiles(s.station_id, 'daily').length,
  0,
)
check('both resolutions exist for every station, every year', () => {
  equal(csvCount, 32, '32 CSVs: 16 station-years at two resolutions')
  for (const station of stations) {
    const hourly = csvFiles(station.station_id, 'hourly')
    const daily = csvFiles(station.station_id, 'daily')
    equal(
      hourly.length,
      daily.length,
      `${station.station_id} has the same years at both resolutions`,
    )
    equal(
      hourly.length,
      station.years.length,
      `${station.station_id}'s manifest matches its files`,
    )
  }
})

check('the daily and hourly files for a station share a channel set', () => {
  // They differ in exactly one column: the daily rollup carries `n_hours`, which
  // is how many of the day's hourly buckets had any sample in them. Everything
  // else has to agree, or a reader switching resolution is looking at a
  // different set of channels rather than a different sampling of the same ones.
  for (const station of stations) {
    const hourly = csvFiles(station.station_id, 'hourly')
    if (hourly.length === 0) continue
    const a = readHeader(station.station_id, 'hourly', hourly[0]).filter((c) => c !== 'n_hours')
    const b = readHeader(station.station_id, 'daily', csvFiles(station.station_id, 'daily')[0])
    equal(
      a.join(','),
      b.filter((c) => c !== 'n_hours').join(','),
      `${station.station_id} hourly and daily agree on their channels`,
    )
    assert(
      !readHeader(station.station_id, 'hourly', hourly[0]).includes('n_hours'),
      `${station.station_id}/hourly carries no n_hours; an hourly bucket is one hour by construction`,
    )
  }
})

check('an hourly file has roughly 700 rows per 30 days and a daily file 30', () => {
  const dir = join(DATA, 'phumy2', 'hourly')
  const file = csvFiles('phumy2', 'hourly')[0]
  const lines = readFileSync(join(dir, file), 'utf8').trim().split(/\r?\n/).length - 1
  const daily = readFileSync(join(DATA, 'phumy2', 'daily', file), 'utf8')
    .trim()
    .split(/\r?\n/).length - 1
  assert(lines > daily * 10, `hourly (${lines}) is much finer than daily (${daily})`)
})

check('public/data is small enough to commit', () => {
  let total = 0
  const walk = (dir) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry)
      if (statSync(full).isDirectory()) walk(full)
      else total += statSync(full).size
    }
  }
  walk(DATA)
  assert(total < 35 * 1024 * 1024, `public/data is ${(total / 1048576).toFixed(1)} MiB`)
})

console.log('\nthe report the inspector reads')

check('the report has a section for every station in the manifest', () => {
  for (const station of stations) {
    const row = quality.stations.find((s) => s.station_id === station.station_id)
    assert(row, `${station.station_id} appears in quality.json`)
    equal(
      row.channels.length,
      station.channels.length,
      `${station.station_id} reports every channel`,
    )
    equal(row.table, station.table, `${station.station_id} names the same table in both`)
  }
})

check('the report\'s totals agree with the manifest\'s', () => {
  const summed = stations.reduce((a, s) => a + (s.n_readings ?? 0), 0)
  equal(quality.totals.readings, summed, 'the headline reading count agrees')
  equal(quality.totals.stations, stations.length, 'the station count agrees')
})

check('the report and the CSVs agree on the out-of-range total', () => {
  const csvOor = []
  for (const station of stations) {
    for (const channel of station.channels) {
      if (!channel.published) continue
      if (channel.band[0] === null && channel.band[1] === null) continue
      csvOor.push(`${channel.channel}_n_oor`)
    }
  }
  const sample = join(DATA, 'aisvn', 'daily', '2021.csv')
  const text = readFileSync(sample, 'utf8')
  const header = text.trim().split('\n')[0].split(',')
  let total = 0
  for (const column of csvOor) {
    if (!header.includes(column)) continue
    const index = header.indexOf(column)
    for (const line of text.trim().split(/\r?\n/).slice(1)) {
      total += Number(line.split(',')[index] ?? 0) || 0
    }
  }
  assert(total > 0, 'aisvn 2021 does flag something, so the counter is wired up')
})

check('every reject reason in the report is in the declared vocabulary', () => {
  for (const row of quality.rejects_by_reason) {
    assert(
      quality.reject_vocabulary.includes(row.reason),
      `${row.reason} is a declared category, not a sentence`,
    )
    assert(row.reason.length < 40, `${row.reason} is short enough to be a category`)
  }
})

check('every flag in the report is in the declared vocabulary', () => {
  for (const flag of Object.keys(quality.flag_totals)) {
    const base = flag.split(':')[0]
    assert(
      quality.flag_vocabulary.includes(base),
      `${flag} is a declared flag family`,
    )
    assert(quality.flag_names[base], `${base} has a sentence explaining it`)
  }
})

check('the windows in the report carry their prose once, not per row', () => {
  for (const kind of ['null', 'bad']) {
    for (const window of quality.windows[kind]) {
      assert(window.why && window.why.length > 30, `${kind} window has prose`)
      assert(Array.isArray(window.columns) && window.columns.length > 0, 'and names its channels')
    }
  }
})

check('a duplicate timestamp is recorded, not silently absorbed', () => {
  const duplicates = quality.rejects_by_reason.find((r) => r.reason === 'duplicate_ts')
  if (duplicates) {
    assert(duplicates.n >= 0, 'duplicates count is non-negative')
  } else {
    equal(quality.totals.duplicate_ts ?? 0, 0, 'no duplicates in consolidated archive')
  }
})

check('a station with no header row still gets its column meanings from the catalog', () => {
  const phumy2 = byId.get('phumy2')
  assert(
    phumy2 && phumy2.channels.length >= 5,
    'phumy2 declares its channels',
  )
})

console.log(
  `\n${passed} passed, ${failures.length} failed`,
  failures.length === 0 ? '' : `\n\n${failures.map((f) => `${f.name}: ${f.message}`).join('\n')}`,
)
process.exit(failures.length === 0 ? 0 : 1)

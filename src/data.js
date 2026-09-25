/**
 * Data access for the site.
 *
 * Everything the browser needs is a small static file under `public/data`,
 * written by `python -m etl export`:
 *
 *   stations.json               station metadata, coverage, available years
 *   {station}/daily/{year}.csv  daily rollups, ~4 rows per month
 *   quality.json                the data-quality report, for the inspector
 *
 * Two properties of the data drive the design here, and both come from
 * `AGENTS.md`:
 *
 * 1. **NULL is not 0.** A missing channel and a genuine zero reading are
 *    different facts, so a CSV empty cell stays `null` all the way to the chart
 *    and breaks the line. It must never be coerced to 0, or every gap becomes a
 *    cliff to the floor.
 * 2. **phumy2 has no `solar_v` or `battery_v` at all.** Its channels are named
 *    `solar2`/`lipo2`, so those columns are empty for all 415k rows. Which
 *    metrics exist is therefore a property of the data, not a fixed list, and
 *    the UI has to discover it rather than assume.
 */

const DATA_ROOT = `${import.meta.env.BASE_URL}data`

/** Cache so switching back to a previously viewed year is instant. */
const cache = new Map()

async function fetchJson(path) {
  const response = await fetch(`${DATA_ROOT}/${path}`)
  if (!response.ok) {
    throw new Error(`${path}: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

async function fetchText(path) {
  const response = await fetch(`${DATA_ROOT}/${path}`)
  if (!response.ok) {
    throw new Error(`${path}: ${response.status} ${response.statusText}`)
  }
  return response.text()
}

export function loadStations() {
  if (!cache.has('stations')) {
    cache.set('stations', fetchJson('stations.json'))
  }
  return cache.get('stations')
}

export function loadQuality() {
  if (!cache.has('quality')) {
    cache.set('quality', fetchJson('quality.json'))
  }
  return cache.get('quality')
}

export function loadDaily(stationId, year) {
  const key = `daily:${stationId}:${year}`
  if (!cache.has(key)) {
    cache.set(
      key,
      fetchText(`${stationId}/daily/${year}.csv`).then(parseCsv).then((rows) =>
        rows.map(decorateRow),
      ),
    )
  }
  return cache.get(key)
}

/**
 * Minimal CSV parser.
 *
 * The exporter writes plain RFC 4180 with no quoting (no value in this dataset
 * contains a comma or a quote), so a split on the delimiter is sufficient and
 * avoids pulling in a parser for 15 small files.
 */
function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/).filter((line) => line.length > 0)
  if (lines.length === 0) return []
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

const MS_PER_DAY = 86400000

function decorateRow(row) {
  const day = row.day
  return {
    day,
    // Parse as UTC midnight. `new Date('2023-01-01')` is already UTC, but
    // being explicit avoids the local-timezone trap where a date-only string
    // shifts by a day west of Greenwich.
    date: Date.parse(`${day}T00:00:00Z`),
    // The local calendar day is what the CSV is keyed on; the UTC instant it
    // starts at is kept so the two are never confused (they differ by 7 hours
    // in Asia/Ho_Chi_Minh, so a local day is not a UTC day).
    tsUtcDay: row.ts_utc_day,
    nSamples: num(row.n_samples),
    nHours: num(row.n_hours),
    energyWh: num(row.energy_wh),
    solarAvg: num(row.solar_v_avg),
    solarMax: num(row.solar_v_max),
    batteryMin: num(row.battery_v_min),
    batteryMax: num(row.battery_v_max),
    powerAvg: num(row.power_w_avg),
    powerMax: num(row.power_w_max),
    tempMin: num(row.temp_c_min),
    tempAvg: num(row.temp_c_avg),
    tempMax: num(row.temp_c_max),
  }
}

/** Empty CSV cell -> null. Never 0: see the note at the top of this file. */
function num(value) {
  if (value === undefined || value === null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * The metrics the chart can draw, and how to get them out of a row.
 *
 * `avg`/`min`/`max` are separate series rather than a band because a daily
 * min/max envelope is genuinely useful here: the difference between
 * `temp_c_min` and `temp_c_max` is the diurnal swing, and for solar voltage the
 * daily max is the clearest single number in the dataset.
 */
export const METRICS = [
  {
    key: 'solar',
    label: 'Solar voltage',
    unit: 'V',
    colour: '#d97706',
    get: (row) => row.solarAvg,
    peak: (row) => row.solarMax,
    decimals: 2,
  },
  {
    key: 'battery',
    label: 'Battery',
    unit: 'V',
    colour: '#2f855a',
    get: (row) => row.batteryMin,
    peak: (row) => row.batteryMax,
    decimals: 2,
  },
  {
    key: 'power',
    label: 'Power',
    unit: 'W',
    colour: '#805ad5',
    get: (row) => row.powerAvg,
    peak: (row) => row.powerMax,
    decimals: 1,
  },
  {
    key: 'temp',
    label: 'Temperature',
    unit: '°C',
    colour: '#2b6cb0',
    get: (row) => row.tempAvg,
    peak: (row) => row.tempMax,
    decimals: 1,
  },
  {
    key: 'energy',
    label: 'Energy',
    unit: 'Wh',
    colour: '#b7791f',
    get: (row) => row.energyWh,
    peak: (row) => row.energyWh,
    decimals: 1,
  },
]

export const METRIC_BY_KEY = Object.fromEntries(METRICS.map((m) => [m.key, m]))

/**
 * Which metrics actually have data for this station.
 *
 * Derived from the rows rather than hardcoded, because `phumy2` has no
 * `solar_v` or `battery_v` at all and `solar-2020-05` has no temperature. A
 * fixed list would offer controls that draw a flat empty axis.
 */
export function availableMetrics(rows) {
  if (!rows || rows.length === 0) return []
  return METRICS.filter((metric) => rows.some((row) => metric.get(row) !== null))
}

export function filterByRange(rows, fromDay, toDay) {
  if (!fromDay && !toDay) return rows
  return rows.filter((row) => {
    if (fromDay && row.day < fromDay) return false
    if (toDay && row.day > toDay) return false
    return true
  })
}

/** Summary numbers for the current selection. */
export function summarise(rows, metric) {
  const values = []
  for (const row of rows) {
    const value = metric.get(row)
    if (value !== null) values.push(value)
  }
  if (values.length === 0) {
    return { count: 0, min: null, max: null, mean: null, total: null }
  }
  const sum = values.reduce((a, b) => a + b, 0)
  return {
    count: values.length,
    min: Math.min(...values),
    max: Math.max(...values),
    mean: sum / values.length,
    // Energy is the one metric that is meaningful summed over the range; for
    // the others a "total" would be a meaningless unit soup.
    total: metric.key === 'energy' ? sum : null,
  }
}

export { MS_PER_DAY }

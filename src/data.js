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
    // Share of the day's samples flagged out-of-range. A day whose only reading
    // is an ADC test pattern (solar 123 V, battery 456 V) still produces a row
    // here, so without this the chart cannot tell it from a real day.
    nOutOfRange: num(row.n_out_of_range) ?? 0,
    energyWh: num(row.energy_wh),
    solarAvg: num(row.solar_v_avg),
    solarMax: num(row.solar_v_max),
    solar2Avg: num(row.solar2_v_avg),
    solar2Max: num(row.solar2_v_max),
    batteryMin: num(row.battery_v_min),
    batteryMax: num(row.battery_v_max),
    battery2Min: num(row.battery2_v_min),
    battery2Max: num(row.battery2_v_max),
    powerAvg: num(row.power_w_avg),
    powerMax: num(row.power_w_max),
    tempMin: num(row.temp_c_min),
    tempAvg: num(row.temp_c_avg),
    tempMax: num(row.temp_c_max),
    // Comma-separated channels that had a collector-confirmed scale applied to
    // this day's aggregate, e.g. 'solar2_v,lipo2_v'. Empty means the value is
    // exactly what the sensor reported, which for a confirmed millivolt channel
    // would mean the chart is about to show 1000x too much.
    scaledChannels: row.scaled_channels || '',
  }
}

/** True when any row in this set had a confirmed unit correction applied. */
export function anyScaled(rows) {
  return rows.some((row) => row.scaledChannels)
}

/** Empty CSV cell -> null. Never 0: see the note at the top of this file. */
function num(value) {
  if (value === undefined || value === null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * Robust per-metric statistics, used to recognise spikes.
 *
 * A spike test has to be relative to the series, not absolute, because the
 * stations do not agree on units: `phumy2.solar2_v` is logged in millivolts and
 * `aisvn` in volts, so a fixed plausibility band flags *every* reading of one
 * and none of the other. The 2020-10-01 problem -- solar 123 V, battery 456 V
 * among neighbouring days of 18 and 19 -- is visible only against the series'
 * own distribution.
 *
 * Median and MAD rather than mean and standard deviation, because a single
 * 456 V reading is exactly what drags a mean away from the value you want to
 * compare against.
 */
export function robustStats(values) {
  const sorted = values.filter((v) => v !== null && v !== undefined).sort((a, b) => a - b)
  if (sorted.length === 0) return { median: null, mad: null, n: 0 }
  const mid = sorted.length >> 1
  const median =
    sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
  const deviations = sorted.map((v) => Math.abs(v - median)).sort((a, b) => a - b)
  const mad =
    deviations.length % 2 ? deviations[mid] : (deviations[mid - 1] + deviations[mid]) / 2
  return { median, mad, n: sorted.length }
}

/**
 * How far a value may sit from the median before it counts as a spike.
 *
 * The floor matters: for a channel that is genuinely steady -- and a daily mean
 * of panel voltage is, to within a few percent -- the MAD is near zero, and a
 * pure MAD threshold would flag normal variation.
 *
 * Returns `null` when the series is too irregular for the question to be
 * meaningful. `phumy2.solar2_v` steps from ~5000 mV to ~1200 mV when a bridge is
 * fitted, and over a window containing both halves the distribution is
 * bimodal: half the days look like spikes against the median of the other mode.
 * Calling either mode an artefact would erase a real configuration change, so
 * the detector declines instead of guessing.
 */
const SPIKE_MAD_MULTIPLIER = 6
const SPIKE_RELATIVE_FLOOR = 0.35
const MAX_TIGHTNESS = 0.2

export function spikeLimit(stats, unit) {
  if (!stats || stats.median === null) return null
  const mad = stats.mad === null ? 0 : stats.mad
  const median = stats.median
  // A channel whose scatter is a large fraction of its own level is
  // multi-modal or genuinely variable, not steady-with-spikes.
  if (Math.abs(mad) > Math.abs(median) * MAX_TIGHTNESS) return null
  const spread = mad * 1.4826 * SPIKE_MAD_MULTIPLIER
  const relative = Math.abs(median) * SPIKE_RELATIVE_FLOOR
  // A small absolute floor in the unit's own terms, so a channel sitting near
  // zero does not get a zero-width tolerance.
  const absolute = unit === 'Wh' ? 5 : unit === 'W' ? 20 : 1.5
  return Math.max(spread, relative, absolute)
}

export function isSpike(value, stats, unit) {
  if (value === null || value === undefined) return false
  const limit = spikeLimit(stats, unit)
  if (limit === null) return false
  return Math.abs(value - stats.median) > limit
}

/**
 * Drop the days that are artefacts rather than measurements, and say how many.
 *
 * Two conditions, both scale-independent:
 *   - the day has no samples at all, so there is nothing to draw
 *   - every value it carries is a spike against that channel's own distribution
 *
 * The dropped days are not deleted. `n_out_of_range` is carried through to the
 * readout so the chart can state what it left out and why, rather than quietly
 * drawing a nicer-looking graph.
 */
export function trustworthyRows(rows, series) {
  const stats = {}
  for (const metric of series) {
    stats[metric.key] = robustStats(rows.map((row) => metric.get(row)))
  }
  const kept = []
  const dropped = []
  for (const row of rows) {
    const noData = (row.nSamples ?? 0) === 0
    const allSpike =
      !noData &&
      series.length > 0 &&
      series.every((metric) => {
        const value = metric.get(row)
        if (value === null) return false
        return isSpike(value, stats[metric.key], metric.unit)
      })
    if (noData || allSpike) {
      dropped.push(row)
      continue
    }
    kept.push(row)
  }
  kept.droppedDays = dropped.length
  kept.dropped = dropped
  return kept
}

/**
 * First non-null of the given fields.
 *
 * The stations number their second panel input `solar2` and `aisvn2` uses
 * `solar3` + `battery2`, so "the solar voltage of this station" is whichever
 * member of the family it actually logs. Aggregating only `solar_v` left the two
 * largest stations with nothing to chart.
 */
function firstOf(row, keys) {
  for (const key of keys) {
    const value = row[key]
    if (value !== null && value !== undefined) return value
  }
  return null
}

export const METRICS = [
  {
    key: 'solar',
    label: 'Solar voltage',
    unit: 'V',
    colour: '#d97706',
    get: (row) => firstOf(row, ['solarAvg', 'solar2Avg']),
    peak: (row) => firstOf(row, ['solarMax', 'solar2Max']),
    decimals: 2,
  },
  {
    key: 'battery',
    label: 'Battery',
    unit: 'V',
    colour: '#2f855a',
    get: (row) => firstOf(row, ['batteryMin', 'battery2Min']),
    peak: (row) => firstOf(row, ['batteryMax', 'battery2Max']),
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
 * Derived from the rows rather than hardcoded: `phumy2` has no `solar_v` or
 * `battery_v` at all (it logs `solar2` and has no battery channel), and
 * `aisvn2` uses `solar3` + `battery2`. A fixed list would offer controls that
 * draw a flat empty axis.
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

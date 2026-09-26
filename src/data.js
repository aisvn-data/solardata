/**
 * Data access for the site.
 *
 * Everything the browser needs is a static file under `public/data`, written by
 * `python -m etl export`:
 *
 *   stations.json               station metadata, coverage, available years
 *   metrics.json                the plausibility bands, verbatim from the ETL
 *   {station}/hourly/{year}.csv  ~30 rows/day, the native rollup
 *   {station}/daily/{year}.csv   ~4 rows/month, a mean over the hourly rows
 *   quality.json                the data-quality report, for the inspector
 *
 * Four properties of the data drive the design here, and all of them come from
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
 * 3. **A flagged value is kept, never dropped.** The pipeline flags an
 *    implausible reading and stores it; the site marks it and says so. The one
 *    thing the UI must not do is decide on its own that a value is not real --
 *    see `classifyRows` for what replaced the heuristic that used to.
 * 4. **The rollups are means, and which mean depends on the granularity.** The
 *    daily battery column is a day's *minimum*; the hourly one is the hour's
 *    *mean*. They are both labelled "Battery" in the UI, so the readout has to
 *    name the statistic or the two views look comparable when they are not.
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

/**
 * The plausibility bands, keyed by canonical channel.
 *
 * Shipped rather than retyped so the browser applies the identical criterion
 * the ingest applied to each raw cell. Correct a band in
 * `etl/normalize/metrics.py` and the site follows on the next export; a second
 * copy of the numbers in JavaScript would drift, and a drifted band is a chart
 * that lies with a straight face.
 */
export function loadBands() {
  if (!cache.has('bands')) {
    cache.set(
      'bands',
      fetchJson('metrics.json').then((payload) => payload.bands ?? {}),
    )
  }
  return cache.get('bands')
}

/** The two resolutions the exporter publishes, in the order the UI offers them. */
export const GRANULARITIES = [
  { folder: 'daily', label: 'Day', noun: 'day' },
  { folder: 'hourly', label: 'Hour', noun: 'hour' },
]

export function loadRollup(stationId, folder, year) {
  const key = `rollup:${stationId}:${folder}:${year}`
  if (!cache.has(key)) {
    cache.set(
      key,
      fetchText(`${stationId}/${folder}/${year}.csv`)
        .then(parseCsv)
        .then((rows) => rows.map((row) => decorateRow(row, folder))),
    )
  }
  return cache.get(key)
}

/**
 * Minimal CSV parser.
 *
 * The exporter writes plain RFC 4180 with no quoting (no value in this dataset
 * contains a comma or a quote), so a split on the delimiter is sufficient and
 * avoids pulling in a parser for the handful of small files involved.
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

/**
 * Which CSV column carries each canonical channel, and which statistic it is.
 *
 * The statistic is not decoration. `readings_daily` has no `battery_v_avg`
 * because a mean of minima is not a useful number, so the daily column is
 * `battery_v_min` -- the *lowest* battery voltage of the day -- while the hourly
 * column is the hour's mean. Both appear under one "Battery" label, so a reader
 * has to be told which one they are looking at or the daily dip looks like a
 * different battery.
 */
const VALUE_COLUMNS = {
  daily: {
    solar_v: ['solar_v_avg', 'mean'],
    solar2_v: ['solar2_v_avg', 'mean'],
    battery_v: ['battery_v_min', 'min'],
    battery2_v: ['battery2_v_min', 'min'],
    power_w: ['power_w_avg', 'mean'],
    temp_c: ['temp_c_avg', 'mean'],
    energy_wh: ['energy_wh', 'total'],
    boot_count_max: ['boot_count_max', 'max'],
  },
  hourly: {
    solar_v: ['solar_v_avg', 'mean'],
    solar2_v: ['solar2_v_avg', 'mean'],
    battery_v: ['battery_v_avg', 'mean'],
    battery2_v: ['battery2_v_min', 'min'],
    power_w: ['power_w_avg', 'mean'],
    temp_c: ['temp_c_avg', 'mean'],
    energy_wh: ['energy_wh', 'total'],
    boot_count_max: ['boot_count_max', 'max'],
  },
}

function decorateRow(raw, folder) {
  const hourly = folder === 'hourly'
  // An hourly row is keyed by the UTC instant of the hour; a daily row by the
  // local calendar day, whose UTC instant is midnight *of that day label* and
  // is therefore not the start of the local day. The two differ by 7 hours in
  // Asia/Ho_Chi_Minh, which is why the label and the instant are kept apart.
  const instant = hourly ? raw.ts_utc : `${raw.day}T00:00:00Z`
  const columns = VALUE_COLUMNS[folder]
  const values = {}
  const stats = {}
  for (const [channel, [column, stat]] of Object.entries(columns)) {
    values[channel] = num(raw[column])
    stats[channel] = stat
  }
  return {
    // The row's own identifier, kept verbatim so a value on screen can be found
    // in the CSV and in the database without a conversion in the reader's head.
    key: hourly ? raw.ts_utc : raw.day,
    // What the axis and the readout print.
    day: hourly ? raw.ts_utc.slice(0, 16).replace('T', ' ') : raw.day,
    // What the From/To date inputs compare against, so a range boundary lands
    // on the day a reader typed rather than on the first hour of it.
    dateDay: (hourly ? raw.ts_utc : raw.day).slice(0, 10),
    date: Date.parse(instant),
    tsUtcDay: raw.ts_utc_day,
    nSamples: num(raw.n_samples),
    // An hourly bucket is one hour wide by construction; the daily rollup
    // carries how many of the day's hours had any sample at all.
    nHours: hourly ? 1 : num(raw.n_hours),
    // How many of the day's samples the pipeline flagged out_of_range. A day
    // whose only reading is an ADC test pattern (solar 123 V, battery 456 V)
    // still produces a row here, so without this the chart cannot tell it from
    // a real day.
    nOutOfRange: num(raw.n_out_of_range) ?? 0,
    values,
    stats,
    // Comma-separated channels that had a collector-confirmed scale applied to
    // this bucket's aggregate, e.g. 'solar2_v,lipo2_v'. Empty means the value is
    // exactly what the sensor reported, which for a confirmed millivolt channel
    // would mean the chart is about to show 1000x too much.
    scaledChannels: raw.scaled_channels || '',
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

export const METRICS = [
  {
    key: 'solar',
    label: 'Solar voltage',
    unit: 'V',
    colour: '#d97706',
    // The stations number their second panel input `solar2`, so "the solar
    // voltage of this station" is whichever member of the family it logs.
    channels: ['solar_v', 'solar2_v'],
    decimals: 2,
  },
  {
    key: 'battery',
    label: 'Battery',
    unit: 'V',
    colour: '#2f855a',
    channels: ['battery_v', 'battery2_v'],
    decimals: 2,
  },
  {
    key: 'power',
    label: 'Power',
    unit: 'W',
    colour: '#805ad5',
    channels: ['power_w'],
    decimals: 1,
  },
  {
    key: 'temp',
    label: 'Temperature',
    unit: '°C',
    colour: '#2b6cb0',
    channels: ['temp_c'],
    decimals: 1,
  },
  {
    key: 'energy',
    label: 'Energy',
    unit: 'Wh',
    colour: '#b7791f',
    channels: ['energy_wh'],
    decimals: 1,
  },
  {
    key: 'boot',
    // The logger's own monotonic counter, which resets when it reboots. Shown
    // because it is the only channel that records the hardware's view of its own
    // uptime: a line that climbs and drops to 1 is the station restarting, which
    // is also where the gaps in the other channels come from. It is a count, not
    // a measurement, so it has no plausibility band and is never flagged.
    label: 'Uptime counter',
    unit: 'reads',
    colour: '#4c51bf',
    channels: ['boot_count_max'],
    decimals: 0,
  },
]

export const METRIC_BY_KEY = Object.fromEntries(METRICS.map((m) => [m.key, m]))

/**
 * Which channel a row actually has, and what it says.
 *
 * Returns the first channel in the family with a value, so a station that logs
 * `solar2_v` is charted on the "Solar voltage" control without the UI needing
 * to know which numbered variant it is.
 */
export function pick(row, metric) {
  for (const channel of metric.channels) {
    const value = row.values[channel]
    if (value !== null && value !== undefined) {
      return { channel, value, stat: row.stats[channel] }
    }
  }
  return { channel: null, value: null, stat: null }
}

/** The plotted value for a metric, or null. A null is a gap, not a zero. */
export function get(row, metric) {
  return pick(row, metric).value
}

/** How a statistic should be named in the readout. */
const STAT_LABELS = {
  mean: 'mean',
  min: 'minimum',
  max: 'peak',
  total: 'total',
}

export function statLabel(stat) {
  return STAT_LABELS[stat] ?? stat ?? ''
}

/**
 * Which metrics actually have data for this station, as keys.
 *
 * Derived from the rows rather than hardcoded: `phumy2` has no `solar_v` or
 * `battery_v` at all (it logs `solar2` and has no battery channel), and
 * `aisvn2` uses `battery2` with no solar channel whatsoever. A fixed list would
 * offer controls that draw a flat empty axis.
 *
 * **Keys, not metric objects, and there is deliberately only one form.** The
 * selection state and the picker both hold keys, and the two shapes are
 * interchangeable at a glance: returning objects from here while the picker
 * tested `metrics.includes(metric.key)` made every checkbox render `disabled`
 * for every station, with no error anywhere and the chart still drawing the
 * default two channels. `check_frontend.mjs` has a regression check by name.
 */
export function availableMetricKeys(rows) {
  if (!rows || rows.length === 0) return []
  return METRICS.filter((metric) => rows.some((row) => get(row, metric) !== null)).map(
    (metric) => metric.key,
  )
}

/**
 * The months that have data in the loaded rollup, as `YYYY-MM`.
 *
 * Computed from the rows rather than from the calendar, so a station that only
 * reported in June and July is offered two months instead of twelve, and picking
 * one cannot select a range with nothing in it.
 */
export function availableMonths(rows) {
  const seen = new Set()
  for (const row of rows ?? []) {
    if (row.nSamples > 0) seen.add(row.dateDay.slice(0, 7))
  }
  return [...seen].sort()
}

export function filterByRange(rows, fromDay, toDay) {
  if (!fromDay && !toDay) return rows
  return rows.filter((row) => {
    if (fromDay && row.dateDay < fromDay) return false
    if (toDay && row.dateDay > toDay) return false
    return true
  })
}

/** The band for a channel, or null if it has none or was never flagged. */
function bandFor(bands, channel) {
  if (!bands || !channel) return null
  const band = bands[channel]
  if (!band || band.lo === null || band.hi === null) return null
  return band
}

export function isOutOfBand(bands, channel, value) {
  const band = bandFor(bands, channel)
  if (!band || value === null || value === undefined) return null
  return value >= band.lo && value <= band.hi ? null : band
}

/**
 * Decide what the chart draws, and say what it is uneasy about.
 *
 * A row is *unplottable* only when it holds no samples at all: there is nothing
 * to draw, and a straight line across the hole would invent one.
 *
 * A row is *flagged* when a value being plotted falls outside the band the
 * pipeline records for that channel -- the same band that raised `out_of_range`
 * on the underlying cell. Flagged rows are **kept and drawn**, with a marker and
 * a stated reason. Nothing is removed, because a value being implausible is not
 * the same as a value being wrong, and only a human can adjudicate that here:
 * the `aisvn` battery band of 9-16 V is a 3S LiPo, and 23 of its 101 days in
 * 2020 sit above it, which may be a second battery pack or a scale nobody has
 * confirmed. Deleting those days would have hidden the question.
 *
 * What this replaced
 * ------------------
 * A median/MAD "spike" test that compared each value against its own series'
 * spread. It was removed because it was measuring sampling coverage rather than
 * plausibility. A panel's 24-hour mean is dominated by night, so a fully covered
 * day averages 6-9 V while a single afternoon sample averages 17-19 V; the test
 * read that as 8 real days being "spikes" and dropped them, along with the 4
 * post-reinstall days at 29 V. It also made the answer depend on which metrics
 * happened to be selected, so the 123 V ADC test pattern was filtered under one
 * selection and drawn under another. A band is scale-explicit, selection-local
 * and the same one the data pipeline uses.
 */
export function classifyRows(rows, series, bands) {
  const plottable = []
  const flaggedRows = []
  const unplottable = []
  let breaches = 0
  for (const row of rows) {
    const found = []
    for (const metric of series) {
      const { channel, value } = pick(row, metric)
      const band = isOutOfBand(bands, channel, value)
      if (band) {
        found.push({ metric: metric.key, channel, value, band })
        breaches += 1
      }
    }
    const annotated = { ...row, breaches: found }
    if ((row.nSamples ?? 0) === 0) {
      unplottable.push(annotated)
      continue
    }
    if (found.length > 0) flaggedRows.push(annotated)
    plottable.push(annotated)
  }
  return { plottable, flaggedRows, unplottable, breaches }
}

/** Summary numbers for the current selection. */
export function summarise(rows, metric) {
  const values = []
  for (const row of rows) {
    const value = get(row, metric)
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

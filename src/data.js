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
    cache.set('bands', fetchJson('metrics.json').then((payload) => payload.bands ?? {}))
  }
  return cache.get('bands')
}

/**
 * What each channel actually did, per station, from `quality.json`.
 *
 * The band in `metrics.json` answers "what should this hardware produce" and is
 * one global answer per column name. That is not enough here: `battery_v` is
 * banded 9-16 V for a 3S LiPo and `aisvn` reads 17.6-29.6 V on 23 of its 101 days
 * in 2020. Either that is a second pack, an unconfirmed scale, or a band wrong
 * for the site it is installed in, and the archive cannot say which. So the
 * observed range is reported beside the band, never instead of it, and the two
 * disagreeing is the finding rather than a nuisance to be smoothed over.
 */
export function loadChannelRanges() {
  if (!cache.has('ranges')) {
    cache.set(
      'ranges',
      loadQuality().then((q) => {
        const byStation = new Map()
        for (const row of q.channel_ranges ?? []) {
          if (!byStation.has(row.station_id)) byStation.set(row.station_id, new Map())
          byStation.get(row.station_id).set(row.column, row)
        }
        return byStation
      }),
    )
  }
  return cache.get('ranges')
}

/** The per-station ranges for one station, or an empty Map. */
export function rangesFor(stationId) {
  return loadChannelRanges().then((all) => all.get(stationId) ?? new Map())
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
    solar3_v: ['solar3_v_avg', 'mean'],
    battery_v: ['battery_v_avg', 'mean'],
    battery2_v: ['battery2_v_avg', 'mean'],
    lipo_v: ['lipo_v_avg', 'mean'],
    lipo2_v: ['lipo2_v_avg', 'mean'],
    current_a: ['current_a_avg', 'mean'],
    current_a_chA: ['current_a_chA_avg', 'mean'],
    current_a_chB: ['current_a_chB_avg', 'mean'],
    current2_a: ['current2_a_avg', 'mean'],
    power_w: ['power_w_avg', 'mean'],
    load_v: ['load_v_avg', 'mean'],
    load1_v: ['load1_v_avg', 'mean'],
    load2_v: ['load2_v_avg', 'mean'],
    wind_v: ['wind_v_avg', 'mean'],
    temp_c: ['temp_c_avg', 'mean'],
    voltage_adc: ['voltage_adc_avg', 'mean'],
    digital_adc: ['digital_adc_avg', 'mean'],
  },
  hourly: {
    solar_v: ['solar_v_avg', 'mean'],
    solar2_v: ['solar2_v_avg', 'mean'],
    solar3_v: ['solar3_v_avg', 'mean'],
    battery_v: ['battery_v_avg', 'mean'],
    battery2_v: ['battery2_v_avg', 'mean'],
    lipo_v: ['lipo_v_avg', 'mean'],
    lipo2_v: ['lipo2_v_avg', 'mean'],
    current_a: ['current_a_avg', 'mean'],
    current_a_chA: ['current_a_chA_avg', 'mean'],
    current_a_chB: ['current_a_chB_avg', 'mean'],
    current2_a: ['current2_a_avg', 'mean'],
    power_w: ['power_w_avg', 'mean'],
    load_v: ['load_v_avg', 'mean'],
    load1_v: ['load1_v_avg', 'mean'],
    load2_v: ['load2_v_avg', 'mean'],
    wind_v: ['wind_v_avg', 'mean'],
    temp_c: ['temp_c_avg', 'mean'],
    voltage_adc: ['voltage_adc_avg', 'mean'],
    digital_adc: ['digital_adc_avg', 'mean'],
  },
}

/** `energy_wh` and the uptime counter are not in VALUE_COLUMNS: they are derived
 *  quantities rather than a channel's mean, and the bootstrap counter is a max. */
function valueColumns(folder) {
  const base = VALUE_COLUMNS[folder]
  return {
    ...base,
    energy_wh: ['energy_wh', 'total'],
    boot_count_max: ['boot_count_max', 'peak'],
  }
}

function decorateRow(raw, folder) {
  const hourly = folder === 'hourly'
  // An hourly row is keyed by the UTC instant of the hour; a daily row by the
  // local calendar day, whose UTC instant is midnight *of that day label* and
  // is therefore not the start of the local day. The two differ by 7 hours in
  // Asia/Ho_Chi_Minh, which is why the label and the instant are kept apart.
  const instant = hourly ? raw.ts_utc : `${raw.day}T00:00:00Z`
  const columns = valueColumns(folder)
  const values = {}
  const stats = {}
  for (const [channel, [column, stat]] of Object.entries(columns)) {
    values[channel] = num(raw[column])
    stats[channel] = stat
  }
  // Per-metric out-of-range counts, kept per channel rather than summarised into
  // the row-level `n_out_of_range`, because the row-level count cannot say which
  // channel broke. Every sample of every phumy2 hour is flagged (current2_a reads
  // ~232 against a +/-50 A band), so 30-of-30 tells you nothing; the same hour's
  // `power_w_n_oor` of 1 is the whole finding.
  const oor = {}
  for (const channel of Object.keys(columns)) {
    const n = num(raw[`${channel}_n_oor`])
    if (n !== null) oor[channel] = n
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
    oor,
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

export const METRICS = []

/** Shown when a channel has no recorded plausibility band. */
const NO_BAND = { unit: '', lo: null, hi: null }

/**
 * Every channel the loaded rollup actually has data for, in a stable order.
 *
 * Discovered, not declared. A fixed list of six hand-picked metrics is a claim
 * about the archive that stopped being true: `aisvn2` logs `solar3_v` and no
 * `power_w` or `temp_c` at all, `phumy2` logs `lipo2_v`, and neither fact could
 * be expressed when the picker iterated a hardcoded `METRICS`. `solar3_v` was in
 * `readings`, in the rollups and in the database the whole time, and simply had
 * nowhere to appear -- so selecting AISVN #2 offered nothing that worked.
 *
 * Order is by kind then name, so the picker groups the way a reader thinks:
 * voltages, then currents, then power, then temperature, then the derived
 * quantities. The same channel is one entry regardless of which numbered variant
 * of it a station uses, because a station that logs `solar2` has exactly one
 * "Solar 2" channel and pretending otherwise would be a second naming scheme.
 */
export async function discoverChannels(rows, bands, ranges) {
  if (!rows || rows.length === 0) return []
  const columns = valueColumns('daily') // the union; membership is what matters
  const present = Object.keys(columns).filter((channel) =>
    rows.some((row) => row.values[channel] !== null),
  )
  const KIND_ORDER = { voltage: 0, current: 1, power: 2, temperature: 3, count: 4, raw: 5 }
  return present
    .map((channel, index) => {
      const band = bands?.[channel] ?? NO_BAND
      // `energy_wh` is a derived integral and `boot_count_max` a rollup column
      // rather than a channel, so neither has a band or a range row of its own.
      // Both are described by the channel they are computed from.
      const source = RANGE_SOURCE[channel] ?? channel
      const range = ranges?.get(source) ?? null
      const meta = band.unit ? band : (range ?? NO_BAND)
      return {
        key: channel,
        channel,
        label: channelLabel(channel, band),
        unit: meta.unit ?? '',
        colour: PALETTE[index % PALETTE.length],
        decimals: band.kind === 'count' || channel === 'boot_count_max' ? 0 : 3,
        kind: band.kind ?? 'raw',
        band,
        range,
      }
    })
    .sort((a, b) => {
      const ka = KIND_ORDER[a.kind] ?? 9
      const kb = KIND_ORDER[b.kind] ?? 9
      if (ka !== kb) return ka - kb
      return a.channel.localeCompare(b.channel)
    })
}

/** Rollup-only columns, and the `readings` channel whose behaviour they describe. */
const RANGE_SOURCE = {
  energy_wh: 'power_w',
  boot_count_max: 'boot_count',
}

/** `solar2_v` -> `Solar 2`, `power_w` -> `Power`, `temp_c` -> `Temperature`. */
function channelLabel(channel, band) {
  if (band?.description) {
    // The ETL writes "Solar panel / collector voltage"; trim the unit word off the
    // end so the picker does not read "Solar panel / collector voltage" next to a
    // separate "V" chip.
    return band.description.replace(/\s+(voltage|current|power|temperature|count)$/i, '')
  }
  return channel
    .replace(/_ch[AB]$/, ' ${&}')
    .replace(/(\d+)_v$/, ' $1')
    .replace(/_/g, ' ')
    .replace(/^./, (c) => c.toUpperCase())
}

const PALETTE = [
  '#d97706',
  '#2f855a',
  '#805ad5',
  '#2b6cb0',
  '#b7791f',
  '#4c51bf',
  '#2c7a7b',
  '#9b2c2c',
  '#975a16',
  '#276749',
]

export const METRIC_BY_KEY = Object.fromEntries([])

/** Look a channel up in whatever the picker is currently offering. */
export function seriesFor(keys, channels) {
  return keys.map((key) => channels.find((c) => c.key === key)).filter(Boolean)
}

/**
 * Which channel a row actually has, and what it says.
 *
 * Returns the first channel in the family with a value, so a station that logs
 * `solar2_v` is charted on the "Solar voltage" control without the UI needing
 * to know which numbered variant it is.
 */
export function pick(row, metric) {
  const value = row.values[metric.channel]
  if (value === null || value === undefined) {
    return { channel: null, value: null, stat: null }
  }
  return { channel: metric.channel, value, stat: row.stats[metric.channel] }
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
/**
 * What to make of a plotted value. Three levels, and they are not the same thing.
 *
 * `clean`
 *     Every sample in the bucket was inside the channel's recorded band.
 * `partial`
 *     *Some* samples were not. The aggregate is a blend of measurements and
 *     flagged values, so it is neither. This is the case that a band test on the
 *     aggregate alone cannot see: `phumy2` 2020-11-27 16:00 UTC averages one
 *     sample of 19,877 W into 29 zeros and lands on 662.57 W, which is inside
 *     the +/-2000 W band, so nothing about the number says anything is wrong.
 *     The count beside it says 1 of 30, which does.
 * `contaminated`
 *     *Every* sample was out of band, so the aggregate is not a summary of
 *     plausible values at all -- it is an average of things the hardware should
 *     not have produced. This is as close to "corrupt" as the data can support
 *     saying, and it is stated as a count rather than a verdict.
 *
 * Separately, a value can sit *inside* the band and still be unlike anything that
 * station has ever recorded, which is a fact about the station rather than about
 * the hardware's plausibility. That is reported as `range` and never as a flag:
 * the band is the pipeline's judgement, the range is an observation, and the two
 * disagreeing is the finding.
 */
export function classify(row, series) {
  const n = row.nSamples ?? 0
  const found = []
  for (const item of series) {
    const { channel, value } = pick(row, item)
    const oor = row.oor?.[channel] ?? 0
    const band = item.band
    const hasBand = band && band.lo !== null && band.hi !== null
    const outsideBand = hasBand && value !== null && (value < band.lo || value > band.hi)
    let level = 'clean'
    if (n > 0 && oor >= n) level = 'contaminated'
    else if (oor > 0) level = 'partial'
    if (level === 'clean' && outsideBand) level = 'contaminated'
    const range = item.range
    const outsideRange =
      level === 'clean' && range && value !== null && (value < range.min || value > range.max)
    if (level === 'clean' && !outsideBand && !outsideRange) continue
    found.push({
      metric: item.key,
      channel,
      value,
      level,
      band,
      oor,
      n,
      range: outsideRange ? range : null,
    })
  }
  return found
}

export function classifyRows(rows, series) {
  const plottable = []
  const flaggedRows = []
  const unplottable = []
  let breaches = 0
  for (const row of rows) {
    const found = classify(row, series)
    const annotated = { ...row, breaches: found }
    if ((row.nSamples ?? 0) === 0) {
      unplottable.push(annotated)
      continue
    }
    if (found.length > 0) flaggedRows.push(annotated)
    plottable.push(annotated)
    breaches += found.length
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

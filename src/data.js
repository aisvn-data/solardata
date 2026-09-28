/**
 * Data access for the site.
 *
 * Everything the browser needs is a static file under `public/data`, written by
 * `python -m etl export`:
 *
 *   stations.json               each station, its channels, their units and bands
 *   metrics.json                the same declarations, keyed for lookup
 *   {station}/hourly/{year}.csv  ~30 rows/day, the native rollup
 *   {station}/daily/{year}.csv   ~4 rows/month, a rollup over the hourly rows
 *   quality.json                the per-station data-quality report
 *
 * Three properties of the data drive the design here, and all of them come from
 * the pipeline:
 *
 * 1. **A station's CSV contains only that station's channels.** There is no
 *    global column list, so there is nothing to discover and nothing to get out
 *    of step. `aisvn` has no `power_w` column because the station's pin is not a
 *    measurement; `phumy2` has one because `aisvn` does, but `phumy2` leaves it
 *    empty and the channel is not offered there. A channel cannot be listed,
 *    unreachable, or present as a column of NULLs, because it is only ever a
 *    column where the station has it.
 * 2. **NULL is not 0.** A missing channel and a genuine zero reading are
 *    different facts, so a CSV empty cell stays `null` all the way to the chart
 *    and breaks the line. It must never be coerced to 0, or every gap becomes a
 *    cliff to the floor.
 * 3. **A value is already in the unit it is displayed in.** The pipeline applies
 *    the confirmed scale once, at ingest, so the database, the rollup, this CSV
 *    and the axis label all hold the same number. There is no stored unit, no
 *    display unit, and no divisor to get wrong -- which is what a chart does when
 *    a `test` probe's 27.70 degC arrives as 2,769 and is drawn at 280 degC with
 *    no error anywhere.
 *
 * A fourth thing follows from the first and matters just as much: **the band is
 * per (station, channel), not per column name.** `battery_v` is 9-16 V at `aisvn`
 * and `phumy2` has no `battery_v` at all, so a global band table could not be
 * right about the archive even in principle. The bands arrive on each channel in
 * `stations.json`, which is the same declaration the ingest applied to the raw
 * cell, so the criterion the chart rings a value against is the criterion that
 * flagged it.
 */

const BASE_URL = import.meta.env.BASE_URL
const DATA_ROOT = `${BASE_URL}data`

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

export function loadCuration() {
  if (!cache.has('curation')) {
    cache.set('curation', fetchJson('curation.json'))
  }
  return cache.get('curation')
}

export function loadNormalization() {
  if (!cache.has('normalization')) {
    cache.set('normalization', fetchJson('normalization.json'))
  }
  return cache.get('normalization')
}

/**
 * The plausibility bands, keyed `"<station_id>.<channel>"`.
 *
 * Shipped rather than retyped so the browser applies the identical criterion the
 * ingest applied to each raw cell. Correct a band in `etl/catalog.py` and the
 * site follows on the next export; a second copy of the numbers in JavaScript
 * would drift, and a drifted band is a chart that lies with a straight face.
 *
 * The key is the station *and* the channel because that is the grain the band is
 * declared at. A band keyed by channel name alone cannot be right about an
 * archive where `solar_v` is volts at one station and millivolts at three others.
 */
export function loadBands() {
  if (!cache.has('bands')) {
    cache.set('bands', fetchJson('metrics.json').then((payload) => payload.channels ?? {}))
  }
  return cache.get('bands')
}

/** The resolutions offered in the UI. */
export const GRANULARITIES = [
  { folder: 'daily', label: 'Day', noun: 'day' },
  { folder: 'hourly', label: 'Hour', noun: 'hour' },
  { folder: 'raw', label: 'Raw (1–2 min)', noun: 'sample' },
]

/**
 * The statistics a rollup column can carry, and how the readout names them.
 *
 * Read off the column name rather than from a per-channel table: the exporter
 * writes `<channel>_<stat>` for exactly the statistics that channel declares, so
 * a column named `battery_v_min` says on its own that it is a minimum. The old
 * pipeline shipped a hardcoded `VALUE_COLUMNS` map instead, which is a fourth
 * place to remember that `temp_c` was renamed to `temp_deci_c` in the rollups.
 */
const STAT_SUFFIXES = ['avg', 'min', 'max']
const OOR_SUFFIX = '_n_oor'
const STAT_LABELS = { avg: 'mean', min: 'minimum', max: 'peak', raw: 'sample' }

export function statLabel(stat) {
  return STAT_LABELS[stat] ?? stat ?? ''
}

/** Which statistic a channel's plotted value uses, given the columns available. */
function primaryStat(stats) {
  if (stats.includes('avg')) return 'avg'
  if (stats.includes('raw')) return 'raw'
  return stats[0] ?? null
}

/** `solar_v_avg` -> `['solar_v', 'avg']`. `battery_v_n_oor` -> `['battery_v', null]`. */
function splitColumn(column) {
  if (column.endsWith(OOR_SUFFIX)) return [column.slice(0, -OOR_SUFFIX.length), null]
  for (const stat of STAT_SUFFIXES) {
    if (column.endsWith(`_${stat}`)) return [column.slice(0, -stat.length - 1), stat]
  }
  return [column, null]
}

let rawDbInstance = null
let rawDbPromise = null
const rawDbListeners = new Set()

export function isRawDbReady() {
  return rawDbInstance !== null
}

export function onRawDbReady(cb) {
  if (rawDbInstance) {
    cb(rawDbInstance)
    return () => {}
  }
  rawDbListeners.add(cb)
  return () => rawDbListeners.delete(cb)
}

export async function initRawDatabase() {
  if (rawDbInstance) return rawDbInstance
  if (!rawDbPromise) {
    rawDbPromise = (async () => {
      try {
        const sqlModule = await import('sql.js')
        const initSqlJs = sqlModule.default || sqlModule
        const SQL = await initSqlJs({
          locateFile: (file) => `${BASE_URL || '/'}${file}`,
        })

        let buf
        if (typeof window === 'undefined') {
          try {
            const fs = await import(/* @vite-ignore */ 'node:fs')
            const path = await import(/* @vite-ignore */ 'node:path')
            const dbPath = path.resolve('public', 'data', 'solardata_raw.db')
            buf = fs.readFileSync(dbPath)
          } catch {
            const resp = await fetch(`${DATA_ROOT}/solardata_raw.db`)
            buf = await resp.arrayBuffer()
          }
        } else {
          const resp = await fetch(`${DATA_ROOT}/solardata_raw.db`)
          if (!resp.ok) {
            throw new Error(`Failed to load solardata_raw.db: ${resp.status}`)
          }
          buf = await resp.arrayBuffer()
        }

        rawDbInstance = new SQL.Database(new Uint8Array(buf))
        for (const cb of rawDbListeners) {
          try {
            cb(rawDbInstance)
          } catch (e) {
            console.error(e)
          }
        }
        rawDbListeners.clear()
        return rawDbInstance
      } catch (err) {
        rawDbPromise = null
        throw err
      }
    })()
  }
  return rawDbPromise
}

function applyChannelCorrection(val, tsUtc, channelConfig) {
  if (val === null || val === undefined) return null
  let res = channelConfig.scale && channelConfig.scale !== 1.0 ? val * channelConfig.scale : val
  const corrections = channelConfig.corrections ?? []
  for (const corr of corrections) {
    if (corr.from_ts && tsUtc < corr.from_ts) continue
    if (corr.to_ts && tsUtc >= corr.to_ts) continue
    if (corr.op === 'add') {
      res += corr.value
    } else if (corr.op === 'factor') {
      res *= corr.value
    }
  }
  return typeof res === 'number' ? Math.round(res * 1e9) / 1e9 : res
}

export async function loadRawYear(stationId, year) {
  const [db, stations, normalization, bands] = await Promise.all([
    initRawDatabase(),
    loadStations(),
    loadNormalization(),
    loadBands(),
  ])
  const station = stations.find((s) => s.station_id === stationId)
  if (!station) throw new Error(`Station not found: ${stationId}`)
  const stationNorm = normalization?.stations?.[stationId] ?? {}
  const tableName = `r_${stationId.replace(/-/g, '_')}`

  const minTs = Math.floor(Date.parse(`${year}-01-01T00:00:00Z`) / 1000)
  const maxTs = Math.floor(Date.parse(`${year}-12-31T23:59:59.999Z`) / 1000)

  const stmt = `SELECT * FROM ${tableName} WHERE ts >= ${minTs} AND ts <= ${maxTs} ORDER BY ts ASC`
  const result = db.exec(stmt)
  if (!result || result.length === 0) {
    return {
      header: ['ts'],
      channels: [],
      stats: {},
      allStats: {},
      rows: [],
    }
  }

  const { columns, values } = result[0]
  const publishedChannels = station.channels.filter((c) => c.published).map((c) => c.channel)
  const presentChannels = publishedChannels.filter((c) => columns.includes(c))
  const colIndices = Object.fromEntries(presentChannels.map((c) => [c, columns.indexOf(c)]))
  const tsIndex = columns.indexOf('ts')

  const rows = values.map((rowArr) => {
    const ts = rowArr[tsIndex]
    const instant = new Date(ts * 1000).toISOString()
    const stamp = instant.replace('.000Z', 'Z')
    const dateDay = stamp.slice(0, 10)
    const day = stamp.slice(0, 19).replace('T', ' ')
    const date = ts * 1000

    const rowValues = {}
    const byStat = {}
    const stats = {}
    const breaches = []

    for (const ch of presentChannels) {
      const rawVal = rowArr[colIndices[ch]]
      const chConfig = stationNorm.channels?.[ch] ?? {}
      const val = applyChannelCorrection(rawVal, stamp, chConfig)
      rowValues[ch] = val
      byStat[ch] = { raw: val }
      stats[ch] = 'raw'

      const bandKey = `${stationId}.${ch}`
      const band = bands[bandKey]?.band
      if (val !== null && band) {
        const [lo, hi] = band
        if (lo !== null && val < lo) {
          breaches.push({ channel: ch, value: val, bound: lo, side: 'floor' })
        } else if (hi !== null && val > hi) {
          breaches.push({ channel: ch, value: val, bound: hi, side: 'ceiling' })
        }
      }
    }

    return {
      key: stamp,
      day,
      dateDay,
      date,
      tsUtcDay: stamp,
      nSamples: 1,
      nHours: 1,
      nOutOfRange: breaches.length,
      values: rowValues,
      byStat,
      stats,
      oor: {},
      breaches,
    }
  })

  return {
    header: ['ts', ...presentChannels],
    channels: presentChannels,
    stats: Object.fromEntries(presentChannels.map((c) => [c, 'raw'])),
    allStats: Object.fromEntries(presentChannels.map((c) => [c, ['raw']])),
    rows,
  }
}

export function loadRollup(stationId, folder, year) {
  const key = `rollup:${stationId}:${folder}:${year}`
  if (!cache.has(key)) {
    if (folder === 'raw') {
      cache.set(key, loadRawYear(stationId, year))
    } else {
      cache.set(
        key,
        fetchText(`${stationId}/${folder}/${year}.csv`)
          .then(parseCsv)
          .then((rows) => decorateRows(rows, folder)),
      )
    }
  }
  return cache.get(key)
}

/**
 * Minimal CSV parser.
 *
 * The exporter writes plain RFC 4180 with no quoting (no value in this dataset
 * contains a comma or a quote), so a split on the delimiter is sufficient and
 * avoids pulling in a parser for the handful of small files involved. The header
 * is returned with the rows, because it *is* the station's channel list.
 */
export function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/).filter((line) => line.length > 0)
  if (lines.length === 0) return { header: [], rows: [] }
  const header = lines[0].split(',')
  const rows = lines.slice(1).map((line) => {
    const cells = line.split(',')
    const row = {}
    header.forEach((name, index) => {
      row[name] = cells[index] ?? ''
    })
    return row
  })
  return { header, rows }
}

const MS_PER_DAY = 86400000

/** Empty CSV cell -> null. Never 0: see the note at the top of this file. */
function num(value) {
  if (value === undefined || value === null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * Columns that describe the bucket rather than measure anything.
 *
 * `n_samples` and `n_hours` are how many raw readings the bucket holds. They are
 * not channels, and treating them as channels is how a reader ends up offered a
 * "N samples" control on the picker and a flat line at 700 on the chart. The
 * exporter writes them second and third, after `ts`, and they are the reason a
 * day with one reading is distinguishable from a day with five hundred -- a day
 * with no readings must break the line rather than sit at zero.
 */
const META_COLUMNS = new Set(['n_samples', 'n_hours', 'n_out_of_range'])

/**
 * Turn parsed CSV rows into what the chart wants.
 *
 * The column map comes from the file's own header, so it is exactly the set of
 * channels the station has -- there is no global list to fall out of step with
 * the exporter, and no column whose values are all NULL because the channel does
 * not apply to this station.
 */
function decorateRows(parsed, folder) {
  const hourly = folder === 'hourly'
  const timeColumn = 'ts'

  // Which channels this file carries, and with what statistics, read off the
  // header. `stats` is what the exporter actually wrote, not what the catalog
  // declares, so a channel whose only column is a minimum is plotted as a
  // minimum.
  const columns = new Map()
  for (const column of parsed.header) {
    if (column === 'ts' || META_COLUMNS.has(column)) continue
    const [channel, stat] = splitColumn(column)
    if (!columns.has(channel)) columns.set(channel, { stats: [], oor: false })
    const entry = columns.get(channel)
    if (stat) entry.stats.push(stat)
    else entry.oor = true
  }

  return {
    header: parsed.header,
    channels: [...columns.keys()],
    stats: Object.fromEntries(
      [...columns].map(([channel, entry]) => [channel, primaryStat(entry.stats)]),
    ),
    allStats: Object.fromEntries([...columns].map(([channel, e]) => [channel, e.stats])),
    rows: parsed.rows.map((raw) => decorateRow(raw, folder, columns, timeColumn)),
  }
}

function decorateRow(raw, folder, columns, timeColumn) {
  const hourly = folder === 'hourly'
  // An hourly row is keyed by the UTC instant of the hour; a daily row by the
  // local calendar day, whose UTC instant is midnight *of that day label* and is
  // therefore not the start of the local day. The two differ by 7 hours in
  // Asia/Ho_Chi_Minh, which is why the label and the instant are kept apart.
  const stamp = raw[timeColumn] ?? ''
  const instant = hourly ? stamp : `${stamp}T00:00:00Z`

  // Per-channel out-of-range counts, kept per channel rather than summarised
  // into a row-level count, because the row-level count cannot say which channel
  // broke. A phumy2 hour whose current2_a reads 0.231 A is not contaminated at
  // all, and 30-of-30 used to say it was.
  const oor = {}
  const byStat = {}
  const values = {}
  const stats = {}
  for (const [channel, entry] of columns) {
    if (entry.oor) {
      const n = num(raw[`${channel}${OOR_SUFFIX}`])
      if (n !== null) oor[channel] = n
    }
    const perChannel = {}
    for (const stat of entry.stats) {
      perChannel[stat] = num(raw[`${channel}_${stat}`])
    }
    byStat[channel] = perChannel
    const stat = primaryStat(entry.stats)
    stats[channel] = stat
    values[channel] = stat ? perChannel[stat] : null
  }

  return {
    // The row's own identifier, kept verbatim so a value on screen can be found
    // in the CSV and in the database without a conversion in the reader's head.
    key: stamp,
    // What the axis and the readout print.
    day: hourly ? stamp.slice(0, 16).replace('T', ' ') : stamp,
    // What the From/To date inputs compare against, so a range boundary lands
    // on the day a reader typed rather than on the first hour of it.
    dateDay: stamp.slice(0, 10),
    date: Date.parse(instant),
    tsUtcDay: hourly ? stamp : `${stamp}T00:00:00Z`,
    nSamples: num(raw.n_samples),
    // An hourly bucket is one hour wide by construction; the daily rollup
    // carries how many of the day's hours had any sample at all.
    nHours: hourly ? 1 : num(raw.n_hours),
    nOutOfRange: num(raw.n_out_of_range) ?? 0,
    values,
    byStat,
    stats,
    oor,
  }
}

/**
 * This station's channels, ready for the picker.
 *
 * Discovered, but from a declaration rather than from the data: `stations.json`
 * carries each channel's label, unit, kind, band and the range it actually
 * recorded, and only the channels this station has. 0.8 discovered them by
 * scanning a hardcoded 30-column map for non-NULL values, which meant a station
 * with no `power_w` still had to be special-cased, and a channel whose unit was
 * overridden in a different file than the band it was tested against.
 *
 * Order is by kind then name, so the picker groups the way a reader thinks:
 * voltages, then currents, then power, then temperature, then the raw counts.
 */
const KIND_ORDER = {
  voltage: 0,
  current: 1,
  power: 2,
  temperature: 3,
  duration: 4,
  digital: 5,
  count: 6,
  raw: 7,
}

export function channelsFor(station) {
  if (!station) return []
  return station.channels
    .filter((channel) => channel.published)
    .map((channel, index) => ({
      key: channel.channel,
      channel: channel.channel,
      label: channel.label,
      description: channel.description,
      unit: channel.unit,
      kind: channel.kind,
      band: { lo: channel.band[0], hi: channel.band[1] },
      bandNote: channel.band_note,
      decimals: channel.decimals,
      isCounter: channel.is_counter,
      colour: PALETTE[index % PALETTE.length],
      // What the station actually recorded, for the "unlike anything this
      // station has seen" annotation. Never a substitute for the band.
      range: channel.observed
        ? { min: channel.observed.min, max: channel.observed.max, n: channel.observed.n_values }
        : null,
    }))
    .sort((a, b) => {
      const ka = KIND_ORDER[a.kind] ?? 9
      const kb = KIND_ORDER[b.kind] ?? 9
      if (ka !== kb) return ka - kb
      return a.channel.localeCompare(b.channel)
    })
}

/**
 * The channels this station records but the site does not show, with the reason.
 *
 * Surfaced rather than hidden, because a reader who knows `phumy2` has a power
 * pin will otherwise conclude the site dropped a column. It has not: the pin is
 * not a measurement, and the sentence saying so ships with the station.
 */
export function hiddenChannels(station) {
  return station?.hidden ?? []
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

/** Look a channel up in whatever the picker is currently offering. */
export function seriesFor(keys, channels) {
  return keys.map((key) => channels.find((c) => c.key === key)).filter(Boolean)
}

/**
 * Which value of a row a channel is showing.
 *
 * There is one number per channel and it is already in its display unit, so
 * there is nothing to divide and nothing to convert. `byStat` carries every
 * statistic the rollup has for the channel, for the readout.
 */
export function pick(row, channel) {
  const value = row.values[channel.channel]
  if (value === null || value === undefined) {
    // `display: null` is not redundant with `value: null`. The chart and the
    // tooltip read `display`, and a gap that arrives as `undefined` renders as
    // the string "undefined" on an axis rather than as a break in the line.
    return { channel: null, value: null, display: null, stat: null }
  }
  return {
    channel: channel.channel,
    value,
    display: value,
    // Optional read. A caller that only wants the plotted value should not have
    // to know that the row also carries a statistic map, and a row that lacks
    // one should read "no statistic" rather than throw on the way to a number.
    stat: row.stats?.[channel.channel] ?? null,
  }
}

/** The plotted value for a channel, or null. A null is a gap, not a zero. */
export function get(row, channel) {
  return pick(row, channel).display
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

/**
 * What to make of a plotted value. Three levels, and they are not the same thing.
 *
 * `clean`
 *     Every sample in the bucket was inside the channel's recorded band.
 * `partial`
 *     *Some* samples were not. The aggregate is a blend of measurements and
 *     flagged values, so it is neither. This is the case a band test on the
 *     aggregate alone cannot see: an hour that averages one 19,877 W sample into
 *     twenty-nine zeros lands on 662.57 W, which is inside a +/-2,000 W band, so
 *     nothing about the number says anything is wrong. The count beside it says
 *     1 of 30, which does.
 * `contaminated`
 *     *Every* sample was out of band, so the aggregate is not a summary of
 *     plausible values at all. This is as close to "corrupt" as the data can
 *     support saying, and it is stated as a count rather than a verdict.
 *
 * Separately, a value can sit *inside* the band and still be unlike anything that
 * station has ever recorded, which is a fact about the station rather than about
 * the hardware's plausibility. That is reported as `range` and never as a flag:
 * the band is the pipeline's judgement, the range is an observation, and the two
 * disagreeing is the finding.
 *
 * The band is in the same unit as the value, because the pipeline applied the
 * confirmed scale before it was stored. There is no second unit to convert from,
 * which is what used to make a real reading look out of band.
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
      channel,
      metric: item.key,
      value,
      display: value,
      unit: item.unit,
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

/**
 * Summary numbers for the current selection.
 *
 * Taken from every statistic the rollup carries for the channel, not from the
 * plotted value alone, so the "min" and "max" tiles are the bucket's own minimum
 * and maximum rather than the smallest and largest hourly *mean*. That
 * distinction is the whole reason `readings_daily` carries a min and a max.
 */
export function summarise(rows, channel) {
  const values = []
  let min = null
  let max = null
  for (const row of rows) {
    const perStat = row.byStat?.[channel.channel]
    if (perStat) {
      for (const value of Object.values(perStat)) {
        if (value === null || value === undefined) continue
        values.push(value)
        if (min === null || value < min) min = value
        if (max === null || value > max) max = value
      }
    } else {
      const value = get(row, channel)
      if (value !== null) values.push(value)
    }
  }
  if (min === null && values.length) {
    min = Math.min(...values)
    max = Math.max(...values)
  }
  if (values.length === 0) {
    return { count: 0, min: null, max: null, mean: null, total: null }
  }
  const sum = values.reduce((a, b) => a + b, 0)
  return {
    count: values.length,
    min,
    max,
    mean: sum / values.length,
    total: null,
  }
}

export { MS_PER_DAY }

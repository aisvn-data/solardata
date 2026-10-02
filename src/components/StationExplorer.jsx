import { useEffect, useMemo, useRef, useState } from 'react'
import {
  availableMonths,
  channelsFor,
  classifyRows,
  filterByRange,
  initRawDatabase,
  isRawDbReady,
  loadRollup,
  loadStations,
  onRawDbReady,
  seriesFor,
  statLabel,
  summarise,
} from '../data.js'
import DiurnalChart from './DiurnalChart.jsx'
import StatTiles from './StatTiles.jsx'
import TimeControls from './TimeControls.jsx'
import TimeSeriesChart from './TimeSeriesChart.jsx'
import YearHeatmap from './YearHeatmap.jsx'

/**
 * Station explorer: pick a station and a period, see the values.
 *
 * State is deliberately local and explicit rather than in a store. The only
 * things that need to survive a re-render are the current station, year,
 * resolution, range and channel selection, and they are passed down as props.
 * Adding a state library for that would be more code than the state.
 *
 * Nothing is filtered out of the chart here. `classifyRows` says which values
 * fall outside the band the pipeline records for that station and channel; this
 * component decides whether to *draw* them, and always says how many it left out
 * and why.
 */
function fmt(value, decimals = 2) {
  if (value === null || value === undefined) return '—'
  const abs = Math.abs(value)
  if (abs >= 1000) return value.toFixed(0)
  if (abs >= 100) return value.toFixed(Math.max(0, decimals - 1))
  return value.toFixed(decimals)
}

/** True when the station's own readings fall outside the recorded band. */
function disagrees(range, band) {
  if (!range || !band || (band.lo === null && band.hi === null)) return false
  if (band.lo !== null && range.min < band.lo) return true
  if (band.hi !== null && range.max > band.hi) return true
  return false
}

function bandText(band) {
  if (!band || (band.lo === null && band.hi === null)) return null
  const lo = band.lo === null ? '−∞' : fmt(band.lo)
  const hi = band.hi === null ? '∞' : fmt(band.hi)
  return `${lo} … ${hi}`
}

/**
 * This station's channels, what it recorded, and what the pipeline expects.
 *
 * The two columns are deliberately not collapsed into one "range". The band is
 * the pipeline's judgement about this station's hardware, in the unit the value is
 * stored in; the observed range is what this instrument actually did. They agree
 * on most channels and disagree loudly on the ones where a human has a decision
 * to make — `aisvn`'s battery is banded 9-16 V for a 12 V lead-acid pack and reads
 * up to 29.8 V. Showing only the band hides that; showing only the observed range
 * hides the expectation.
 *
 * The band, the unit and the count of out-of-range readings are the three numbers
 * the pipeline recorded, read from `stations.json`. There is no second copy in
 * this file to fall out of step with the ingest.
 */
function ChannelTable({ channels }) {
  return (
    <details className="channel-table">
      <summary>
        This station&apos;s {channels.length} channel
        {channels.length === 1 ? '' : 's'} — what it recorded, and what the
        pipeline expects
      </summary>
      <table>
        <thead>
          <tr>
            <th>Channel</th>
            <th className="num">Readings</th>
            <th className="num">Observed</th>
            <th className="num">Band</th>
            <th className="num">Out of range</th>
          </tr>
        </thead>
        <tbody>
          {channels.map((channel) => {
            const r = channel.range
            const band = bandText(channel.band)
            return (
              <tr key={channel.key}>
                <td>
                  <code>{channel.channel}</code>
                  <span className="muted small">
                    {' '}
                    {channel.label}
                    {channel.unit ? ` (${channel.unit})` : ''}
                  </span>
                </td>
                <td className="num">{(r?.n ?? 0).toLocaleString()}</td>
                <td className="num">
                  {r ? `${fmt(r.min, channel.decimals)} … ${fmt(r.max, channel.decimals)}` : '—'}
                </td>
                <td className="num">
                  {band ? (
                    <span className={disagrees(r, channel.band) ? 'band-warn' : ''}>{band}</span>
                  ) : (
                    <span className="muted">none</span>
                  )}
                </td>
                <td className="num">
                  {channel.observedOor ? channel.observedOor.toLocaleString() : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="muted small">
        <strong>Observed</strong> is min … max over every raw reading this station
        ever recorded for that channel, in the unit the column is stored in.{' '}
        <strong>Band</strong> is the range the pipeline records this station&apos;s
        hardware as producing, from <code>etl/catalog.py</code>. A channel in
        orange has readings outside its band — that is a question about the
        hardware or about the unit, not a value to discard.
      </p>
    </details>
  )
}

/**
 * The channels this station records that the site is not drawing, and why.
 *
 * Every one of them is in the database, in the Parquet interchange export and in
 * the quality report. They are absent from the chart because the values are not
 * measurements, because they never vary, or because nobody has established what
 * unit they are in — and a reader who knows the station has a wind input or a
 * power pin will otherwise conclude the site dropped a column. Showing the
 * channel, the reason and the range it actually recorded answers that.
 */
const EXCLUDE_REASONS = {
  not_measurement: 'Not a measurement',
  constant: 'Never varies',
  unresolved_unit: 'Unit not established',
  text_label: 'A label, not a number',
}

function HiddenChannels({ station }) {
  const hidden = station.hidden ?? []
  if (hidden.length === 0) return null
  return (
    <details className="channel-table">
      <summary>
        {hidden.length} channel{hidden.length === 1 ? '' : 's'} this station records
        that are not charted
      </summary>
      <table>
        <thead>
          <tr>
            <th>Channel</th>
            <th>Why not</th>
            <th className="num">Recorded</th>
          </tr>
        </thead>
        <tbody>
          {hidden.map((channel) => (
            <tr key={channel.channel}>
              <td>
                <code>{channel.channel}</code>
                <span className="muted small"> {channel.label}</span>
              </td>
              <td>
                <strong>{EXCLUDE_REASONS[channel.reason] ?? channel.reason}</strong>
                <div className="muted small">{channel.note}</div>
              </td>
              <td className="num">
                {channel.observed && channel.observed.n_values
                  ? `${(channel.observed.n_values).toLocaleString()} readings, ${fmt(
                      channel.observed.min,
                    )} … ${fmt(channel.observed.max)}`
                  : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        All of these are in <code>{station.table}</code> and in the quality report.
        Excluding a channel is a decision about what to draw, never about what to
        keep.
      </p>
    </details>
  )
}

/**
 * One station in the picker: its name, where it is, how much it recorded and the
 * span it covers. Four facts, and the reader needs all four before choosing --
 * "Phu My Hung #2" and "Phu My Hung #1" are otherwise indistinguishable.
 */
export function StationButton({ station, active, onPick }) {
  return (
    <button type="button" className={active ? 'active' : ''} onClick={onPick}>
      <strong>{station.display_name}</strong>
      <span className="muted">{station.location}</span>
      <span className="badge">{(station.n_readings ?? 0).toLocaleString()} readings</span>
      <span className="muted small">
        {station.first_ts_utc?.slice(0, 10)} → {station.last_ts_utc?.slice(0, 10)}
      </span>
    </button>
  )
}

/**
 * Collapsible station selector overlay drawer with backdrop.
 * Disappears when a station is selected so the complete width is available.
 */
export function StationSidebar({
  stations,
  stationId,
  isOpen = true,
  onClose,
  onPick,
  asideRef,
}) {
  if (!isOpen) return null

  const production = stations.filter((s) => s.is_production)
  const other = stations.filter((s) => !s.is_production)

  return (
    <div className="station-overlay-wrapper">
      <div
        className="station-overlay-backdrop"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        ref={asideRef}
        className="station-overlay-drawer"
        aria-label="Station selector"
        role="dialog"
        aria-modal="true"
      >
        <div className="station-overlay-header">
          <h3>Select Station</h3>
          {onClose && (
            <button
              type="button"
              className="station-overlay-close-btn"
              onClick={onClose}
              aria-label="Close station selector"
              title="Close station selector"
            >
              ✕
            </button>
          )}
        </div>

        <nav className="station-list" aria-label="Stations">
          {production.map((s) => (
            <StationButton
              key={s.station_id}
              station={s}
              active={s.station_id === stationId}
              onPick={() => onPick(s)}
            />
          ))}
          {other.length > 0 && (
            <>
              <h3 className="station-group">Not solar production</h3>
              {other.map((s) => (
                <StationButton
                  key={s.station_id}
                  station={s}
                  active={s.station_id === stationId}
                  onPick={() => onPick(s)}
                />
              ))}
            </>
          )}
        </nav>
      </aside>
    </div>
  )
}

/**
 * The view the site opens on: AISVN #1, 21.11.2021 to 30.11.2021, at hourly
 * resolution with battery, solar and wind selected.
 */
export const DEFAULT_VIEW = {
  station: 'aisvn',
  year: '2021',
  month: '2021-11',
  from: '2021-11-21',
  to: '2021-11-30',
  resolution: 'hourly',
  channels: ['battery_v', 'solar_v', 'wind_v'],
}

/** The date bounds the site opens on. */
export function openingRange(rows, defaultView = DEFAULT_VIEW) {
  if (defaultView.from && defaultView.to) {
    return { from: defaultView.from, to: defaultView.to }
  }
  return monthBounds(rows, defaultView.month)
}

/** True while the loaded view is the one DEFAULT_VIEW describes. */
function isDefaultView(stationId, year, resolution) {
  return (
    stationId === DEFAULT_VIEW.station &&
    year === DEFAULT_VIEW.year &&
    resolution === DEFAULT_VIEW.resolution
  )
}

/**
 * The station and year the site opens on, or null when there is nothing to open.
 *
 * The requested station, and failing that the one with the most readings — an
 * opening view of nothing is worse than a fallback. Exported because the station
 * name, the year and the month below are claims about the archive, and
 * `check_render.mjs` is the only place that can resolve them against the real
 * `stations.json` without a browser.
 */
export function openingView(stations) {
  const usable = stations.filter((s) => s.years.length > 0)
  if (usable.length === 0) return null
  const preferred = usable.find((s) => s.station_id === DEFAULT_VIEW.station)
  const biggest = usable.reduce((a, b) => ((b.n_readings ?? 0) > (a.n_readings ?? 0) ? b : a))
  const opening = preferred ?? biggest
  return {
    stationId: opening.station_id,
    year: opening.years.includes(DEFAULT_VIEW.year)
      ? DEFAULT_VIEW.year
      : opening.years[opening.years.length - 1],
  }
}

/**
 * The channels to select the first time a view is opened.
 *
 * DEFAULT_VIEW's channels when this is that view and it still has them —
 * intersected with what the station publishes, because a channel the station does
 * not record must never reach the chart. Everything else keeps the old rule: the
 * first two channels, which are the first two voltages and the most readable
 * pair. `scripts/check_render.mjs` asserts that the intersection is not silently
 * empty for the opening view, which is the failure this hides.
 */
export function defaultSelection(available, stationId, year, resolution) {
  if (isDefaultView(stationId, year, resolution)) {
    const preferred = DEFAULT_VIEW.channels.filter((key) => available.includes(key))
    if (preferred.length > 0) return preferred
  }
  return available.slice(0, 2)
}

/**
 * A month's own bounds in a rollup: its first and last day that carry samples.
 *
 * Bounded by the data, not by the calendar, and that is the whole reason this is
 * a function. `aisvn` logged only part of some months, and a From/To padded with
 * days it never reported renders as a chart with gaps in it. The month control
 * and the opening default use this same rule, which is what keeps the control
 * reading "November 2021" instead of "All" beside a range that says November.
 * Returns null for a month the rollup does not have.
 */
export function monthBounds(rows, month) {
  const inMonth = (rows ?? []).filter(
    (row) => row.nSamples > 0 && row.dateDay.slice(0, 7) === month,
  )
  if (inMonth.length === 0) return null
  return { from: inMonth[0].dateDay, to: inMonth[inMonth.length - 1].dateDay }
}

/**
 * The From/To a freshly loaded rollup should have.
 *
 * `resolution` is an argument and is deliberately not part of the decision.
 * That is the whole point of the signature: it makes "a resolution switch keeps
 * the range" a thing a test can ask, by passing the same period twice with two
 * different resolutions and requiring the same answer. The bug this replaces was
 * the period key carrying the resolution, and with the key built by the caller
 * a test could not see it -- the pure function passed every assertion while the
 * component threw the range away.
 *
 * `changed: false` means leave the controls alone. The effect that calls this
 * runs again for the same period when the rollup's channel table arrives, and
 * again on every resolution change; clearing the range on either made the site
 * discard the view it had just opened on.
 *
 * `opening` is the range DEFAULT_VIEW asks for, honoured once. A second visit to
 * the opening view gets the reset, not the default: by then the reader has their
 * own range in those inputs, and a default that keeps reasserting itself is a
 * control that undoes itself.
 */
export function rangeForLoad({ stationId, year, previous, opening, openingApplied }) {
  // Station and year, and nothing else. `resolution` is not passed at all, so it
  // cannot be used here even by accident.
  const period = `${stationId}:${year}`
  if (period === previous) return { changed: false, from: null, to: null, opening: false }
  if (opening && !openingApplied) {
    return { changed: true, from: opening.from, to: opening.to, opening: true }
  }
  return { changed: true, from: '', to: '', opening: false }
}

/**
 * The year a station should open on when it is picked from the list.
 *
 * A function, exported, because the bug it replaces was invisible to every check
 * that existed: the handler read `.year` off an element of `years`, which is an
 * array of plain strings, so the year became `undefined`. The loader's guard then
 * aborted, the previous station's rollup stayed on screen, and — because
 * `channels` is the new station's metadata intersected with the old file's
 * header — the new station's *bands* were tested against the old station's
 * *values*. Every point came back out of band and ringed. `check_render.mjs`
 * could not see it, because it exercised the pure helpers and the event handler
 * was a closure in the component. Same shape as the `rangeForLoad` note above.
 *
 * The newest year the station has, and `undefined` only for a station with no
 * years at all, which `openingView` already filters out.
 */
export function yearForPick(station) {
  const years = station?.years ?? []
  return years.length > 0 ? years[years.length - 1] : undefined
}

export function parseExploreHash(hashString) {
  if (!hashString) return {}
  const raw = hashString.replace(/^#/, '')
  const [route, query] = raw.split('?')
  if (route && route !== 'explore') return {}
  const params = new URLSearchParams(query || '')
  return {
    stationId: params.get('station') || null,
    year: params.get('year') || null,
    resolution: params.get('res') || null,
    fromDay: params.get('from') || null,
    toDay: params.get('to') || null,
  }
}

export function buildExploreHash(stId, yr, res, from, to) {
  if (!stId || !yr) return '#explore'
  const params = new URLSearchParams()
  params.set('station', stId)
  params.set('year', yr)
  if (res && res !== DEFAULT_VIEW.resolution) {
    params.set('res', res)
  }
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  return `#explore?${params.toString()}`
}

export default function StationExplorer() {
  const [stations, setStations] = useState([])
  const [stationId, setStationId] = useState(null)
  const [year, setYear] = useState('')
  const [resolution, setResolution] = useState(DEFAULT_VIEW.resolution)
  const [rollup, setRollup] = useState({ header: [], channels: [], rows: [] })
  const isNavigatingFromHistory = useRef(false)
  // The station/year/resolution `rollup` was actually loaded for.
  //
  // This exists because a rollup that belongs to a different period than the
  // controls are showing is not a cosmetic problem: `channels` is the current
  // station's metadata intersected with the loaded file's header, so a stale file
  // means the current station's *bands* are tested against another station's
  // *values*. That renders a real reading as out of band and rings it, which is
  // the one thing this site must never do. Rather than trust the two to stay in
  // step, the mismatch is made unrepresentable: a rollup whose key is not the
  // wanted one is waited for, never drawn.
  const [rollupFor, setRollupFor] = useState(null)
  const [fromDay, setFromDay] = useState('')
  const [toDay, setToDay] = useState('')
  // Keyed by station. Keying by station *and* resolution made Day and Hour
  // remember independent channel selections, so a reader who picked three
  // channels and pressed Hour got a different set, and neither was wrong on its
  // own terms. A resolution switch is a different sampling of the same days and
  // must not change what is being measured.
  const [selectionByView, setSelectionByView] = useState({})
  const [hoverRow, setHoverRow] = useState(null)
  const [hideFlagged, setHideFlagged] = useState(false)
  const [showStats, setShowStats] = useState(true)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [rawDbReady, setRawDbReady] = useState(isRawDbReady())
  const [rawDbError, setRawDbError] = useState(null)
  const [dailyRows, setDailyRows] = useState([])
  const [hourlyRows, setHourlyRows] = useState([])
  const [isOverlayOpen, setIsOverlayOpen] = useState(false)

  useEffect(() => {
    initRawDatabase()
      .then(() => setRawDbReady(true))
      .catch((e) => {
        console.error('[solardata] Failed to load raw database:', e)
        setRawDbError(e.message)
      })
    const unsubscribe = onRawDbReady(() => setRawDbReady(true))
    return unsubscribe
  }, [])

  useEffect(() => {
    if (!stationId || !year) return
    loadRollup(stationId, 'daily', year)
      .then((data) => setDailyRows(data?.rows ?? []))
      .catch((e) => console.warn('Failed to load daily rollup for heatmap:', e))

    if (resolution !== 'raw') {
      loadRollup(stationId, 'hourly', year)
        .then((data) => setHourlyRows(data?.rows ?? []))
        .catch(() => setHourlyRows([]))
    }
  }, [stationId, year, resolution])
  // The one-shot guard on DEFAULT_VIEW's range. A ref rather than state because
  // it is bookkeeping for an effect, not something anything renders.
  const defaultRangeDone = useRef(false)
  // The station and year fromDay/toDay currently describe, so the loader below
  // resets the range on a change of *period* and not on every run of its effect.
  //
  // Deliberately not keyed on the resolution. Day and Hour are two samplings of
  // the same days -- `dateDay` is derived the same way in both, and a From/To is
  // a statement about which days the reader wants, not how finely to draw them.
  // Clearing it on a resolution switch made the Hour button look broken: you pick
  // November, press Hour to see the dawn, and the chart jumps back to the year.
  const rangeView = useRef(null)

  const viewKey = stationId
  // The station and its channels come from one object now: `stations.json` carries
  // the band, the unit, the decimals and the observed range for every channel
  // this station has. There is no second fetch to keep in step, and nothing to
  // discover from the rows.
  const station = stations.find((s) => s.station_id === stationId) ?? null
  const allChannels = useMemo(() => channelsFor(station), [station])
  const rows = rollup.rows
  // The period the controls currently describe, and whether the loaded rollup is
  // that period's. `null` until the station list has resolved a station and a year.
  const wanted = stationId && year ? `${stationId}/${year}/${resolution}` : null
  const rollupStale = rollupFor !== wanted

  useEffect(() => {
    let cancelled = false
    loadStations()
      .then((list) => {
        if (cancelled) return
        // Every station with a rollup, production or not. Filtering on `published`
        // here is what made the two bench stations disappear from the site.
        setStations(list.filter((s) => s.years.length > 0))
        const initParams = typeof window !== 'undefined' ? parseExploreHash(window.location.hash) : {}
        const initialStation = initParams.stationId && list.find((s) => s.station_id === initParams.stationId)
        if (initialStation) {
          setStationId(initialStation.station_id)
          if (initParams.year && initialStation.years.includes(initParams.year)) {
            setYear(initParams.year)
          } else {
            setYear(yearForPick(initialStation))
          }
        } else {
          const opening = openingView(list)
          if (opening) {
            setStationId(opening.stationId)
            setYear(opening.year)
          }
        }
        if (initParams.resolution && ['daily', 'hourly', 'raw'].includes(initParams.resolution)) {
          setResolution(initParams.resolution)
        }
        if (initParams.fromDay) setFromDay(initParams.fromDay)
        if (initParams.toDay) setToDay(initParams.toDay)
        setLoading(false)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err.message)
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Sync state to browser history when station, year, or resolution changes
  useEffect(() => {
    if (!stationId || !year) return
    if (typeof window === 'undefined') return

    if (isNavigatingFromHistory.current) {
      isNavigatingFromHistory.current = false
      return
    }

    const currentHash = window.location.hash
    const newHash = buildExploreHash(stationId, year, resolution, fromDay, toDay)
    if (currentHash !== newHash) {
      const currentRoute = currentHash.replace(/^#/, '').split('?')[0]
      if (currentRoute === 'explore' || !currentRoute) {
        window.history.pushState(
          { stationId, year, resolution, fromDay, toDay },
          '',
          newHash,
        )
      }
    }
  }, [stationId, year, resolution, fromDay, toDay])

  // Listen to browser popstate (e.g. Back/Forward buttons)
  useEffect(() => {
    if (typeof window === 'undefined') return undefined

    function onPopState() {
      const parsed = parseExploreHash(window.location.hash)
      if (!parsed.stationId) return

      isNavigatingFromHistory.current = true
      if (parsed.stationId) setStationId(parsed.stationId)
      if (parsed.year) setYear(parsed.year)
      if (parsed.resolution) setResolution(parsed.resolution)
      if (parsed.fromDay !== undefined) setFromDay(parsed.fromDay || '')
      if (parsed.toDay !== undefined) setToDay(parsed.toDay || '')
    }

    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  // Close station overlay when Escape is pressed.
  useEffect(() => {
    if (!isOverlayOpen) return undefined
    function onKeyDown(e) {
      if (e.key === 'Escape') setIsOverlayOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isOverlayOpen])

  // Load the CSV whenever station, year or resolution changes. The From/To it
  // opens with is `rangeForLoad`'s decision, which is keyed on the *period* --
  // station and year -- and not on the resolution: switching Day for Hour is a
  // different sampling of the same days, and clearing the range there made the
  // Hour button look broken, because you pick November, press Hour to see the
  // dawn, and the chart jumps back to the whole year.
  useEffect(() => {
    if (!stationId || !year || !resolution) return undefined
    let cancelled = false
    setHoverRow(null)
    setError(null)
    loadRollup(stationId, resolution, year)
      .then((data) => {
        if (cancelled) return
        setRollup(data)
        setRollupFor(`${stationId}/${year}/${resolution}`)
        const opening = isDefaultView(stationId, year, resolution)
          ? openingRange(data.rows)
          : null
        const next = rangeForLoad({
          stationId,
          year,
          previous: rangeView.current,
          opening,
          openingApplied: defaultRangeDone.current,
        })
        if (next.changed) {
          rangeView.current = `${stationId}:${year}`
          if (next.opening) defaultRangeDone.current = true
          setFromDay(next.from)
          setToDay(next.to)
        }
        setSelectionByView((current) => {
          // Keyed on the station, and narrowed to what the station publishes --
          // never to what this one file happens to carry, because narrowing stored
          // state is what made a selection vanish for good in 0.8. A channel a
          // particular year does not carry is dropped at render time instead.
          const offered = allChannels.map((c) => c.key)
          const kept = (current[stationId] ?? []).filter((k) => offered.includes(k))
          return {
            ...current,
            // First visit: the opening view's channels, or the first two this
            // station publishes, which are the voltages it reports and the most
            // readable pair.
            [stationId]:
              kept.length > 0 ? kept : defaultSelection(offered, stationId, year, resolution),
          }
        })
      })
      .catch((err) => {
        if (cancelled) return
        // Drop the key as well as the error, so a failed load reads as "no data"
        // rather than as whatever the previous station left behind.
        setRollupFor(null)
        setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [stationId, year, resolution, allChannels])

  // Only the channels this station publishes *and* the loaded rollup actually
  // carries. The rollup's own header is the authority on the second half: it is
  // the exporter's word about what is in the file, so a channel declared in the
  // catalog but absent from a particular year's CSV cannot reach the chart.
  const channels = useMemo(() => {
    const present = new Set(rollup.channels ?? [])
    return allChannels.filter((channel) => present.has(channel.key))
  }, [allChannels, rollup.channels])

  // The reader's selection, narrowed to what the loaded rollup actually carries.
  //
  // Derived rather than stored, so that a channel a year or a resolution does not
  // carry is simply not drawn and the selection is still there when the reader
  // switches back. Storing the narrowed version instead loses it permanently.
  const selected = useMemo(() => {
    const carried = new Set(channels.map((c) => c.key))
    return (selectionByView[stationId] ?? []).filter((k) => carried.has(k))
  }, [selectionByView, stationId, channels])

  const months = useMemo(() => availableMonths(rows), [rows])
  const inRange = useMemo(() => filterByRange(rows, fromDay, toDay), [rows, fromDay, toDay])
  const series = useMemo(() => seriesFor(selected, channels), [selected, channels])
  const classified = useMemo(() => classifyRows(inRange, series), [inRange, series])
  // The only thing the chart ever drops is a bucket with no samples in it, which
  // has nothing to draw. Flagged buckets are drawn unless the reader asks
  // otherwise, and the count of those is stated either way.
  const plotted = useMemo(
    () =>
      hideFlagged
        ? classified.plottable.filter((row) => row.breaches.length === 0)
        : classified.plottable,
    [classified, hideFlagged],
  )

  // The headline channel is whichever is selected first, and selection order is
  // the reader's, so the tiles follow the reader rather than a fixed ranking.
  const primary = series[0] ?? null
  const summary = primary && plotted.length ? summarise(plotted, primary) : null
  const headlineStat = primary && rows.length ? statLabel(rows[0].stats[primary.channel]) : null

  // Channels whose confirmed scale is not 1: the sheet wrote millivolts, or
  // milliamps, or hundredths, and the value here is the reading rather than the
  // number the collector logged.
  const converted = useMemo(
    () => (station?.channels ?? []).filter((c) => c.published && c.scale !== 1),
    [station],
  )

  function toggleMetric(key) {
    setSelectionByView((current) => {
      const existing = current[viewKey] ?? []
      let next
      if (existing.includes(key)) {
        // Keep at least one channel selected, otherwise the chart has nothing
        // to draw and the user has no way back except re-picking.
        next = existing.length === 1 ? existing : existing.filter((k) => k !== key)
      } else {
        next = [...existing, key]
      }
      return { ...current, [viewKey]: next }
    })
  }

  function applyPreset(days) {
    if (days === null) {
      setFromDay('')
      setToDay('')
      return
    }
    const last = rows[rows.length - 1]
    if (!last) return
    const from = new Date(last.date + days * 86400000).toISOString().slice(0, 10)
    setFromDay(from < rows[0].dateDay ? rows[0].dateDay : from)
    setToDay(last.dateDay)
  }

  /**
   * The month select is a *view* over the range, not a second piece of state.
   *
   * It shows a month only when From and To are exactly that month's bounds, so
   * the two controls cannot disagree about what is on screen — pick a month and
   * the date inputs move with it; edit a date and the month drops back to "All".
   * Holding the month separately was the obvious design and the wrong one: two
   * sources of truth for one range, and a combination the UI could render but
   * not explain.
   */
  const activeMonth = useMemo(() => {
    if (!fromDay || !toDay) return ''
    if (fromDay.slice(0, 7) !== toDay.slice(0, 7)) return ''
    const key = fromDay.slice(0, 7)
    return months.includes(key) ? key : ''
  }, [fromDay, toDay, months])

  const diurnalRows = useMemo(() => {
    if (resolution === 'raw') return plotted
    return filterByRange(resolution === 'hourly' ? rows : hourlyRows, fromDay, toDay)
  }, [resolution, plotted, rows, hourlyRows, fromDay, toDay])

  function selectMonth(key) {
    if (key === '') {
      setFromDay('')
      setToDay('')
      return
    }
    // Bound by the data, not the calendar: a month a station reported only partly
    // is bounded by the days it actually has, so choosing it never produces a
    // range padded with empty days. The same rule the opening default uses.
    const bounds = monthBounds(rows, key)
    if (!bounds) return
    setFromDay(bounds.from)
    setToDay(bounds.to)
  }

  if (loading) return <p className="muted">Loading station list…</p>
  if (error) {
    return (
      <div className="error-box">
        <h3>Could not load the data files</h3>
        <p className="muted">{error}</p>
        <p>
          The site reads static CSV files from <code>public/data/</code>. Generate
          them with <code>python -m etl export</code> and reload.
        </p>
      </div>
    )
  }
  // Nothing below this line may read a rollup that belongs to another station,
  // year or resolution. The controls and the data are a pair, and a mismatched
  // pair is a chart with the wrong station's bands on it, which looks like a
  // finding rather than a bug. Wait for the right file instead of drawing the
  // wrong one.
  if (rollupStale) {
    return (
      <p className="muted">
        Loading {station?.display_name ?? 'data'}
        {year ? `, ${year}` : ''}
        {resolution === 'hourly'
          ? ', hourly'
          : resolution === 'raw'
            ? ', raw samples'
            : ', daily'}…
      </p>
    )
  }

  const flagged = classified.flaggedRows
  const noun = resolution === 'hourly' ? 'hour' : resolution === 'raw' ? 'sample' : 'day'
  const granularityNote =
    resolution === 'hourly'
      ? 'each point is the mean of that hour’s readings'
      : resolution === 'raw'
        ? 'each point is an individual sensor reading at native cadence'
        : 'each point is the mean of that day’s readings'

  // Bench stations are listed, in their own group, and labelled. They were hidden
  // from the site entirely until 0.7.2 because they are not solar production --
  // which was true of what they are and not a reason to withhold 38,930 readings
  // that are in the database, in the Parquet export and in the quality report. A
  // reader who is told a station is a WiFi probe can decide what to do with it; a
  // reader who is shown six of eight stations cannot.
  const production = stations.filter((s) => s.is_production)
  const other = stations.filter((s) => !s.is_production)

  return (
    <div className="explorer">
      {station && (
        <StationSidebar
          stations={stations}
          stationId={stationId}
          isOpen={isOverlayOpen}
          onClose={() => setIsOverlayOpen(false)}
          onPick={(s) => {
            setStationId(s.station_id)
            setYear(yearForPick(s))
            setIsOverlayOpen(false)
          }}
        />
      )}

      <div className="explorer-main">
        {station && (
          <>
            <div className="station-heading">
              <div className="station-title-row">
                <button
                  type="button"
                  className="station-menu-btn"
                  onClick={() => setIsOverlayOpen(true)}
                  aria-label="Select station"
                  aria-expanded={isOverlayOpen}
                  title="Select station"
                >
                  <svg
                    className="burger-icon"
                    viewBox="0 0 24 24"
                    width="18"
                    height="18"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <line x1="3" y1="6" x2="21" y2="6" />
                    <line x1="3" y1="12" x2="21" y2="12" />
                    <line x1="3" y1="18" x2="21" y2="18" />
                  </svg>
                  <span>Stations</span>
                </button>
                <h2>{station.display_name}</h2>
              </div>
              <div className="station-meta-row">
                <p className="muted">
                  {station.location} · {station.tz} · applet <code>{station.applet}</code> ·{' '}
                  <code>{station.table}</code>
                </p>
                <button
                  type="button"
                  className="stats-toggle-btn"
                  onClick={() => setShowStats((prev) => !prev)}
                  title={showStats ? 'Hide summary statistics tiles' : 'Show summary statistics tiles'}
                  aria-expanded={showStats}
                >
                  {showStats ? 'Hide Stats ▲' : 'Show Stats ▼'}
                </button>
              </div>
            </div>

            {!station.is_production && (
              <div className="bench-banner" role="note">
                <strong>Not solar production.</strong> {station.notes}
              </div>
            )}

            {/*
             * Order is reading order, and the reader arrives with three questions
             * in this order: what does this station record, which slice of it am I
             * looking at, and what am I actually looking at. So the tiles lead, the
             * controls narrow, the chart draws, and the caveats that govern how to
             * read that chart sit directly under it — where they are read *after*
             * the line has been interpreted, instead of being skipped above it.
             */}
            {showStats && (
              <StatTiles
                station={station}
                rows={plotted}
                metric={primary}
                stat={headlineStat}
                summary={summary}
                range={fromDay || toDay ? { from: fromDay || 'start', to: toDay || 'end' } : null}
              />
            )}

            <TimeControls
              years={station.years}
              year={year}
              onYearChange={setYear}
              rows={rows}
              fromDay={fromDay}
              toDay={toDay}
              onRangeChange={(from, to) => {
                setFromDay(from)
                setToDay(to)
              }}
              onRangePreset={applyPreset}
              channels={channels}
              selected={selected}
              onMetricToggle={toggleMetric}
              months={months}
              activeMonth={activeMonth}
              onMonthChange={selectMonth}
              granularities={station.granularities ?? ['daily']}
              resolution={resolution}
              onResolutionChange={setResolution}
              hideFlagged={hideFlagged}
              onHideFlaggedChange={setHideFlagged}
              rawDbReady={rawDbReady}
              rawDbError={rawDbError}
            />

            <TimeSeriesChart
              rows={plotted}
              series={series}
              resolution={resolution}
              onHover={setHoverRow}
              hoverRow={hoverRow}
            />

            <p className="chart-note muted">
              {granularityNote}, from the archive&apos;s native cadence
              {resolution === 'hourly'
                ? '; the unaggregated readings are in the Parquet export'
                : ' — switch to Hour for the intraday shape'}
              .
            </p>

            {flagged.length > 0 && (
              <p className="chart-note flagged" role="status">
                {flagged.length} of {classified.plottable.length} {noun}
                {flagged.length === 1 ? '' : 's'} in this range are outside their
                channel&apos;s recorded band or are built partly from samples that are
                {hideFlagged ? ', hidden at your request' : ', ringed on the chart'}.
                The values are kept everywhere &mdash; only the drawing changes.
              </p>
            )}

            {converted.length > 0 && (
              <p className="chart-note scaled" role="status">
                {converted.length} of this station&apos;s channels
                {converted.length === 1 ? ' is' : ' are'} logged by the collector in a
                different unit &mdash; {converted
                  .slice(0, 4)
                  .map((c) => `${c.label} in ${c.raw_unit}`)
                  .join(', ')}
                {converted.length > 4 ? ', and others' : ''} &mdash; and converted once,
                here, against the hardware. The value on the chart is the reading, not
                the number the sheet recorded.
              </p>
            )}

            {classified.unplottable.length > 0 && (
              <p className="chart-note" role="status">
                {classified.unplottable.length} {noun}
                {classified.unplottable.length === 1 ? '' : 's'} in this range recorded no
                readings at all and are not drawn. The rows are in{' '}
                <code>{station.table}</code> and the Parquet export.
              </p>
            )}

            <YearHeatmap
              station={station}
              year={year}
              dailyRows={dailyRows}
              fromDay={fromDay}
              toDay={toDay}
              onSelectDay={(dayStr) => {
                setFromDay(dayStr)
                setToDay(dayStr)
              }}
            />

            <DiurnalChart
              rows={diurnalRows}
              channels={channels}
              station={station}
              resolution={resolution}
            />

            {channels.length > 0 && (
              <ChannelTable
                channels={channels.map((c) => {
                  const declared = station.channels.find((x) => x.channel === c.channel)
                  return { ...c, observedOor: declared?.observed?.n_out_of_range ?? 0 }
                })}
              />
            )}

            <HiddenChannels station={station} />

            {/* The banner above already carries this for a bench station. */}
            {station.is_production && station.notes && (
              <p className="station-note muted">{station.notes}</p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  anyScaled,
  availableMonths,
  classifyRows,
  discoverChannels,
  filterByRange,
  loadBands,
  loadRollup,
  loadStations,
  rangesFor,
  seriesFor,
  statLabel,
  summarise,
} from '../data.js'
import StatTiles from './StatTiles.jsx'
import TimeControls from './TimeControls.jsx'
import TimeSeriesChart from './TimeSeriesChart.jsx'

/**
 * Station explorer: pick a station and a period, see the values.
 *
 * State is deliberately local and explicit rather than in a store. The only
 * things that need to survive a re-render are the current station, year,
 * resolution, range and metric selection, and they are passed down as props.
 * Adding a state library for that would be more code than the state.
 *
 * Nothing is filtered out of the chart here. `classifyRows` says which values
 * fall outside the band the pipeline records for their channel; this component
 * decides whether to *draw* them, and always says how many it left out and why.
 */
/**
 * This station's channels, what it recorded, and what the pipeline expects.
 *
 * The two columns are deliberately not collapsed into one "range". The band is
 * the pipeline's judgement about the hardware and is the same for every station
 * that logs a given column; the observed range is what *this* instrument actually
 * did. They agree on most channels and disagree loudly on the ones where a human
 * has a decision to make -- `aisvn`'s battery is banded 9-16 V for a 12 V lead-acid pack and
 * reads up to 29.6 V, `aisvn2`'s `solar3_v` is banded 0-60 V and reads 23,860
 * because the millivolt scale was never confirmed. Showing only the band hides
 * that; showing only the observed range hides the expectation.
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
          </tr>
        </thead>
        <tbody>
            {channels.map((channel) => {
            const r = channel.range
            const d = channel.divisor ?? 1
            const hasBand = channel.band && channel.band.lo !== null
            return (
              <tr key={channel.key}>
                <td>
                  <code>{channel.channel}</code>
                  <span className="muted small"> {channel.label}</span>
                </td>
                <td className="num">{(r?.n ?? 0).toLocaleString()}</td>
                <td className="num">
                  {r ? `${fmt(r.min / d)} … ${fmt(r.max / d)}` : '—'}
                  {r?.unit ? ` ${r.unit}` : ''}
                </td>
                <td className="num">
                  {hasBand ? (
                    <span className={disagrees(r, channel.band, d) ? 'band-warn' : ''}>
                      {fmt(channel.band.lo / d)} … {fmt(channel.band.hi / d)}
                    </span>
                  ) : (
                    <span className="muted">none</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="muted small">
        <strong>Observed</strong> is min … max over every raw reading this station
        ever recorded for that channel, so a single corrupt sample widens it.{' '}
        <strong>Band</strong> is the range the pipeline records the hardware as
        producing, from <code>etl/normalize/metrics.py</code>. A channel in
        orange has readings outside its band — that is a question about the
        hardware or an unconfirmed unit scale, not a value to discard.
      </p>
    </details>
  )
}

/** True when the station's own readings fall outside the recorded band. */
function disagrees(range, band, divisor = 1) {
  if (!range || !band || band.lo === null) return false
  return range.min < band.lo || range.max > band.hi
}

function fmt(value) {
  if (value === null || value === undefined) return '—'
  const abs = Math.abs(value)
  if (abs >= 1000) return value.toFixed(0)
  if (abs >= 10) return value.toFixed(1)
  return value.toFixed(2)
}

/**
 * One station in the picker: its name, where it is, how much it recorded and the
 * span it covers. Four facts, and the reader needs all four before choosing --
 * "Phu My Hung #2" and "Phu My Hung #1" are otherwise indistinguishable.
 */
function StationButton({ station, active, onPick }) {
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
 * The view the site opens on: AISVN #1, November 2021, at hourly resolution.
 *
 * A default, not a restriction — every control below is still the reader's, and
 * nothing here decides what is interesting. It exists because the alternative
 * was the site opening on whatever the largest station happens to be for the
 * whole year, which asks a first-time reader to find a period worth looking at
 * inside 1,466 daily buckets on their own.
 *
 * The month, not a pair of dates, because the month is what the data has: the
 * range is the month's own first and last day *with samples*, computed by
 * `selectMonth`, so the month control reads "November 2021" on arrival instead
 * of disagreeing with the From and To inputs beside it. A hardcoded pair of dates
 * would go stale the moment a file was re-ingested.
 *
 * Hourly, because this is the one place the solar curve is legible: at `Day` the
 * same month is a flat 2.9-13.6 V mean, and the point of the station is the shape
 * between dawn and dusk. Battery, solar and wind together, because between them
 * they are the whole of what `aisvn` is: a bank charging from 10.6 V, a panel
 * going to ~20 V, and an input the collector wired and never explained.
 *
 * Applied **once**, on the first rollup that loads, and only when that rollup is
 * this view. Re-asserting it on every change would take the range back out of the
 * reader's hands: a station that does not cover these days would open on an empty
 * chart, and the "All" preset would be a control that undid itself.
 */
export const DEFAULT_VIEW = {
  station: 'aisvn',
  year: '2021',
  month: '2021-11',
  resolution: 'hourly',
  channels: ['battery_v', 'solar_v', 'wind_v'],
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
 * DEFAULT_VIEW's channels when this is that view and it still has them — they
 * are intersected with what the rollup discovered, because a channel the station
 * did not log must never reach the chart. Everything else keeps the old rule: the
 * first two channels discovered, which are the first two voltages and the most
 * readable pair. `scripts/check_render.mjs` asserts that the intersection is not
 * silently empty for the opening view, which is the failure this hides.
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
 * runs again for the same period when the station's observed ranges arrive, and
 * again on every resolution change; clearing the range on either made the site
 * discard the view it had just opened on.
 *
 * `opening` is the range DEFAULT_VIEW asks for, honoured once. A second visit to
 * the opening view gets the reset, not the default: by then the reader has their
 * own range in those inputs, and a default that keeps reasserting itself is a
 * control that undoes itself.
 */
export function rangeForLoad({
  stationId,
  year,
  resolution,
  previous,
  opening,
  openingApplied,
}) {
  // Station and year, and nothing else. `resolution` is accepted and ignored.
  void resolution
  const period = `${stationId}:${year}`
  if (period === previous) return { changed: false, from: null, to: null, opening: false }
  if (opening && !openingApplied) {
    return { changed: true, from: opening.from, to: opening.to, opening: true }
  }
  return { changed: true, from: '', to: '', opening: false }
}

export default function StationExplorer() {
  const [stations, setStations] = useState([])
  const [bands, setBands] = useState({})
  const [ranges, setRanges] = useState(new Map())
  const [channels, setChannels] = useState([])
  const [stationId, setStationId] = useState(null)
  const [year, setYear] = useState('')
  const [resolution, setResolution] = useState(DEFAULT_VIEW.resolution)
  const [rows, setRows] = useState([])
  const [fromDay, setFromDay] = useState('')
  const [toDay, setToDay] = useState('')
  // Keyed by station *and* resolution. Keying by station alone was a bug: the
  // daily and hourly rollups do not carry the same channels, so a selection made
  // on one was silently narrowed by the other and never restored.
  const [selectionByView, setSelectionByView] = useState({})
  const [hoverRow, setHoverRow] = useState(null)
  const [hideFlagged, setHideFlagged] = useState(false)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
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

  const viewKey = `${stationId}:${resolution}`
  const selected = selectionByView[viewKey] ?? []
  // Declared here rather than next to the JSX that uses it, because the loader
  // below needs this station's `channel_units` in its dependency list and a `const`
  // cannot be read before the line that initialises it.
  const station = stations.find((s) => s.station_id === stationId) ?? null

  useEffect(() => {
    let cancelled = false
    loadStations()
      .then((list) => {
        if (cancelled) return
        // Every station with a rollup, production or not. Filtering on `published`
        // here is what made the two bench stations disappear from the site.
        setStations(list.filter((s) => s.years.length > 0))
        const opening = openingView(list)
        if (opening) {
          setStationId(opening.stationId)
          setYear(opening.year)
        }
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

  // The bands are a separate fetch because they describe every channel of every
  // station, not the one being looked at. A failure here must not blank the
  // chart: without bands the values are simply drawn unflagged, and the note
  // below says so.
  useEffect(() => {
    let cancelled = false
    loadBands()
      .then((payload) => {
        if (!cancelled) setBands(payload)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // The observed range of every channel, per station. This is a property of the
  // station rather than of the loaded year, so it is fetched once per station and
  // kept across resolution and year changes -- otherwise switching resolution
  // would change which values count as unusual, which is not a thing the reader
  // asked for.
  useEffect(() => {
    if (!stationId) return undefined
    let cancelled = false
    rangesFor(stationId)
      .then((map) => {
        if (!cancelled) setRanges(map)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [stationId])

  // Load the CSV whenever station, year or resolution changes. The From/To it
  // opens with is `rangeForLoad`'s decision, which is keyed on the *period* --
  // station and year -- and not on the resolution: switching Day for Hour is a
  // different sampling of the same days, and clearing the range there made the
  // Hour button look broken, because you pick November, press Hour to see the
  // dawn, and the chart jumps back to the whole year.
  //
  // It is also not keyed on this effect running: `ranges` is in the dependency
  // list because the channel table needs it, and it arrives after the CSV, so the
  // effect runs twice for the first period.
  useEffect(() => {
    if (!stationId || !year || !resolution) return
    let cancelled = false
    setHoverRow(null)
    Promise.all([loadRollup(stationId, resolution, year), loadBands()])
      .then(([data, bandTable]) => {
        if (cancelled) return
        setRows(data)
        const opening = isDefaultView(stationId, year, resolution)
          ? monthBounds(data, DEFAULT_VIEW.month)
          : null
        const next = rangeForLoad({
          stationId,
          year,
          resolution,
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
        discoverChannels(data, bandTable, ranges, station?.channel_units).then((found) => {
          if (cancelled) return
          setChannels(found)
          const available = found.map((c) => c.key)
          setSelectionByView((current) => {
            const key = `${stationId}:${resolution}`
            const kept = (current[key] ?? []).filter((k) => available.includes(k))
            return {
              ...current,
              // First visit: the opening view's channels, or the first two
              // discovered, which are the voltages the station reports and the
              // most readable pair.
              [key]:
                kept.length > 0
                  ? kept
                  : defaultSelection(available, stationId, year, resolution),
            }
          })
        })
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [stationId, year, resolution, ranges, station])

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

  // The headline metric is whichever is selected first, and selection order is
  // the reader's, so the tiles follow the reader rather than a fixed ranking.
  const primary = series[0] ?? null
  const summary = primary && plotted.length ? summarise(plotted, primary) : null
  const primaryStat = primary && rows.length ? statLabel(rows[0].stats[primary.channel]) : null

  function toggleMetric(key) {
    setSelectionByView((current) => {
      const existing = current[viewKey] ?? []
      let next
      if (existing.includes(key)) {
        // Keep at least one metric selected, otherwise the chart has nothing
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

  const flagged = classified.flaggedRows
  const granularityNote =
    resolution === 'hourly'
      ? 'each point is the mean of that hour’s readings'
      : 'each point is the mean of that day’s readings'
  // Bench stations are listed, in their own group, and labelled. They were hidden
  // from the site entirely until 0.7.2 because they are not solar production --
  // which was true of what they are and not a reason to withhold 38,930 readings
  // that are in the database, in the Parquet export and in the quality report. A
  // reader who is told a station is a WiFi probe can decide what to do with it; a
  // reader who is shown six of eight stations cannot.
  const production = stations.filter((s) => s.published)
  const other = stations.filter((s) => !s.published)

  return (
    <div className="explorer">
      <nav className="station-list" aria-label="Stations">
        {production.map((s) => (
          <StationButton
            key={s.station_id}
            station={s}
            active={s.station_id === stationId}
            onPick={() => {
              setStationId(s.station_id)
              setYear(s.years[s.years.length - 1])
            }}
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
                onPick={() => {
                  setStationId(s.station_id)
                  setYear(s.years[s.years.length - 1])
                }}
              />
            ))}
          </>
        )}
      </nav>

      <div className="explorer-main">
        {station && (
          <>
            <div className="station-heading">
              <h2>{station.display_name}</h2>
              <p className="muted">
                {station.location} · {station.tz} · applet <code>{station.applet}</code>
              </p>
            </div>

            {!station.published && (
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
            <StatTiles
              station={station}
              rows={plotted}
              metric={primary}
              stat={primaryStat}
              summary={summary}
              range={fromDay || toDay ? { from: fromDay || 'start', to: toDay || 'end' } : null}
            />

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
            />

            <TimeSeriesChart
              rows={plotted}
              series={series}
              resolution={resolution}
              onHover={setHoverRow}
              hoverRow={hoverRow}
            />

            <p className="chart-note muted">
              {granularityNote}, from the archive&apos;s 119-second cadence
              {resolution === 'hourly'
                ? '; the unaggregated readings are in the Parquet export'
                : ' — switch to Hour for the intraday shape'}
              .
            </p>

            {flagged.length > 0 && (
              <p className="chart-note flagged" role="status">
                {flagged.length} of {classified.plottable.length}{' '}
                {resolution === 'hourly' ? 'hours' : 'days'} in this range are
                outside their channel&apos;s recorded band or are built partly from
                samples that are
                {hideFlagged ? ', hidden at your request' : ', ringed on the chart'}.
                The values are kept everywhere &mdash; only the drawing changes.
                {Object.keys(bands).length === 0 &&
                  ' The bands could not be loaded, so nothing could be flagged.'}
              </p>
            )}

            {anyScaled(inRange) && (
              <p className="chart-note scaled" role="status">
                Values for this station are converted from the units the
                collector logged &mdash; several stations write millivolts as
                integers, so the raw number is a thousand times the reading you
                see here. The conversion is applied only to channels confirmed
                against the firmware, and each affected {resolution === 'hourly' ? 'hour' : 'day'} records
                which channels were converted.
              </p>
            )}

            {classified.unplottable.length > 0 && (
              <p className="chart-note" role="status">
                {classified.unplottable.length}{' '}
                {resolution === 'hourly' ? 'hour' : 'day'}
                {classified.unplottable.length === 1 ? '' : 's'} in this range
                recorded no readings at all and are not drawn. The rows are in
                the database and the Parquet export.
              </p>
            )}

            {channels.length > 0 && <ChannelTable channels={channels} />}

            {/* The banner above already carries this for a bench station. */}
            {station.published && station.notes && (
              <p className="station-note muted">{station.notes}</p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

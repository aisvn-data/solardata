import { useEffect, useMemo, useState } from 'react'
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
 * has a decision to make -- `aisvn`'s battery is banded 9-16 V for a 3S LiPo and
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
            const hasBand = channel.band && channel.band.lo !== null
            return (
              <tr key={channel.key}>
                <td>
                  <code>{channel.channel}</code>
                  <span className="muted small"> {channel.label}</span>
                </td>
                <td className="num">{(r?.n ?? 0).toLocaleString()}</td>
                <td className="num">
                  {r ? `${fmt(r.min)} … ${fmt(r.max)}` : '—'}
                  {r?.unit ? ` ${r.unit}` : ''}
                </td>
                <td className="num">
                  {hasBand ? (
                    <span className={disagrees(r, channel.band) ? 'band-warn' : ''}>
                      {channel.band.lo} … {channel.band.hi}
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
function disagrees(range, band) {
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

export default function StationExplorer() {
  const [stations, setStations] = useState([])
  const [bands, setBands] = useState({})
  const [ranges, setRanges] = useState(new Map())
  const [channels, setChannels] = useState([])
  const [stationId, setStationId] = useState(null)
  const [year, setYear] = useState('')
  const [resolution, setResolution] = useState('daily')
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

  const viewKey = `${stationId}:${resolution}`
  const selected = selectionByView[viewKey] ?? []

  useEffect(() => {
    let cancelled = false
    loadStations()
      .then((list) => {
        if (cancelled) return
        const published = list.filter((s) => s.published && s.years.length > 0)
        setStations(published)
        if (published.length > 0) {
          const biggest = published.reduce((a, b) =>
            (b.n_readings ?? 0) > (a.n_readings ?? 0) ? b : a,
          )
          setStationId(biggest.station_id)
          setYear(biggest.years[biggest.years.length - 1])
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

  // Load the CSV whenever station, year or resolution changes. The range resets
  // because the days that exist in 2021 have nothing to do with 2022.
  useEffect(() => {
    if (!stationId || !year || !resolution) return
    let cancelled = false
    setHoverRow(null)
    Promise.all([loadRollup(stationId, resolution, year), loadBands()])
      .then(([data, bandTable]) => {
        if (cancelled) return
        setRows(data)
        setFromDay('')
        setToDay('')
        discoverChannels(data, bandTable, ranges).then((found) => {
          if (cancelled) return
          setChannels(found)
          const available = found.map((c) => c.key)
          setSelectionByView((current) => {
            const key = `${stationId}:${resolution}`
            const kept = (current[key] ?? []).filter((k) => available.includes(k))
            return {
              ...current,
              // First visit: start on the first two channels, which are the
              // voltages the station reports and the most readable pair.
              [key]: kept.length > 0 ? kept : available.slice(0, 2),
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
  }, [stationId, year, resolution, ranges])

  const station = stations.find((s) => s.station_id === stationId) ?? null
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
    // Bound the range by the data, not the calendar: a month a station reported
    // only partly is bounded by the days it actually has, so choosing it never
    // produces a range padded with empty days.
    const inMonth = rows.filter((row) => row.nSamples > 0 && row.dateDay.slice(0, 7) === key)
    if (inMonth.length === 0) return
    setFromDay(inMonth[0].dateDay)
    setToDay(inMonth[inMonth.length - 1].dateDay)
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

  return (
    <div className="explorer">
      <nav className="station-list" aria-label="Stations">
        {stations.map((s) => (
          <button
            key={s.station_id}
            type="button"
            className={s.station_id === stationId ? 'active' : ''}
            onClick={() => {
              setStationId(s.station_id)
              setYear(s.years[s.years.length - 1])
            }}
          >
            <strong>{s.display_name}</strong>
            <span className="muted">{s.location}</span>
            <span className="badge">
              {(s.n_readings ?? 0).toLocaleString()} readings
            </span>
            <span className="muted small">
              {s.first_ts_utc?.slice(0, 10)} → {s.last_ts_utc?.slice(0, 10)}
            </span>
          </button>
        ))}
      </nav>

      <div className="explorer-main">
        {station && (
          <>
            <div className="station-heading">
              <h2>{station.display_name}</h2>
              <p className="muted">
                {station.location} · {station.tz} · applet{' '}
                <code>{station.applet}</code>
              </p>
            </div>

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
              metrics={channels}
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

            <StatTiles
              station={station}
              rows={plotted}
              metric={primary}
              stat={primaryStat}
              summary={summary}
              range={fromDay || toDay ? { from: fromDay || 'start', to: toDay || 'end' } : null}
            />

            <p className="chart-note muted">
              {granularityNote}, from the archive&apos;s 119-second cadence
              {resolution === 'hourly'
                ? '; the unaggregated readings are in the Parquet export'
                : ' — switch to Hour for the intraday shape'}
              .
            </p>

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

            {channels.length > 0 && <ChannelTable channels={channels} />}

            <TimeSeriesChart
              rows={plotted}
              series={series}
              resolution={resolution}
              onHover={setHoverRow}
              hoverRow={hoverRow}
            />

            {station.notes && <p className="station-note muted">{station.notes}</p>}
          </>
        )}
      </div>
    </div>
  )
}

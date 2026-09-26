import { useEffect, useMemo, useState } from 'react'
import {
  METRIC_BY_KEY,
  anyScaled,
  availableMetrics,
  classifyRows,
  filterByRange,
  loadBands,
  loadRollup,
  loadStations,
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
export default function StationExplorer() {
  const [stations, setStations] = useState([])
  const [bands, setBands] = useState({})
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

  // Load the CSV whenever station, year or resolution changes. The range resets
  // because the days that exist in 2021 have nothing to do with 2022.
  useEffect(() => {
    if (!stationId || !year || !resolution) return
    let cancelled = false
    setHoverRow(null)
    loadRollup(stationId, resolution, year)
      .then((data) => {
        if (cancelled) return
        setRows(data)
        setFromDay('')
        setToDay('')
        const available = availableMetrics(data)
        setSelectionByView((current) => {
          const key = `${stationId}:${resolution}`
          const kept = (current[key] ?? []).filter((k) => available.includes(k))
          return {
            ...current,
            // First visit to this station and resolution: start on the first two
            // channels it has, in the order METRICS declares them.
            [key]: kept.length > 0 ? kept : available.slice(0, 2).map((m) => m.key),
          }
        })
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [stationId, year, resolution])

  const station = stations.find((s) => s.station_id === stationId) ?? null
  const metrics = useMemo(() => availableMetrics(rows), [rows])
  const inRange = useMemo(() => filterByRange(rows, fromDay, toDay), [rows, fromDay, toDay])
  const series = useMemo(
    () => selected.map((key) => METRIC_BY_KEY[key]).filter(Boolean),
    [selected],
  )
  const classified = useMemo(
    () => classifyRows(inRange, series, bands),
    [inRange, series, bands],
  )
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
  const primaryStat = primary && rows.length ? statLabel(rows[0].stats[primary.channels[0]]) : null

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
              metrics={metrics}
              selected={selected}
              onMetricToggle={toggleMetric}
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
                {resolution === 'hourly' ? 'hours' : 'days'} in this range carry a
                value outside the band the pipeline records for that channel
                {hideFlagged ? ', hidden at your request' : ', ringed on the chart'}.
                The values are kept everywhere &mdash; only the drawing changes.{' '}
                {Object.keys(bands).length === 0 &&
                  'The bands could not be loaded, so nothing could be flagged.'}
              </p>
            )}

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

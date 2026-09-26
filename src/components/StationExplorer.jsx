import { useEffect, useMemo, useState } from 'react'
import {
  METRIC_BY_KEY,
  availableMetrics,
  filterByRange,
  loadDaily,
  loadStations,
  summarise,
  trustworthyRows,
} from '../data.js'
import StatTiles from './StatTiles.jsx'
import TimeControls from './TimeControls.jsx'
import TimeSeriesChart from './TimeSeriesChart.jsx'

/**
 * Station explorer: pick a station and a period, see the values.
 *
 * State is deliberately local and explicit rather than in a store. The only
 * things that need to survive a re-render are the current station, year, range
 * and metric selection, and they are passed down as props. Adding a state
 * library for that would be more code than the state.
 */
export default function StationExplorer() {
  const [stations, setStations] = useState([])
  const [stationId, setStationId] = useState(null)
  const [year, setYear] = useState('')
  const [rows, setRows] = useState([])
  const [fromDay, setFromDay] = useState('')
  const [toDay, setToDay] = useState('')
  // Keyed by station so switching back to a station restores what you were
  // looking at, instead of resetting to whatever is first in the list -- which is
  // what made the selection look predetermined.
  const [selectionByStation, setSelectionByStation] = useState({})
  const [hoverDay, setHoverDay] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const selected = selectionByStation[stationId] ?? []

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

  // Load the CSV whenever station or year changes. The range resets because the
  // days that exist in 2021 have nothing to do with 2022.
  useEffect(() => {
    if (!stationId || !year) return
    let cancelled = false
    setHoverDay(null)
    loadDaily(stationId, year)
      .then((data) => {
        if (cancelled) return
        setRows(data)
        setFromDay('')
        setToDay('')
        const available = availableMetrics(data)
        setSelectionByStation((current) => {
          const kept = (current[stationId] ?? []).filter((k) => available.includes(k))
          return {
            ...current,
            // First visit to a station: start on the first two channels it has.
            [stationId]: kept.length > 0 ? kept : available.slice(0, 2).map((m) => m.key),
          }
        })
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [stationId, year])

  const station = stations.find((s) => s.station_id === stationId) ?? null
  const metrics = useMemo(() => availableMetrics(rows), [rows])
  const inRange = useMemo(() => filterByRange(rows, fromDay, toDay), [rows, fromDay, toDay])
  const series = useMemo(
    () => selected.map((key) => METRIC_BY_KEY[key]).filter(Boolean),
    [selected],
  )
  // Days that are artefacts rather than measurements: no samples at all, or
  // every value a spike against its own channel's distribution.
  const plotted = useMemo(() => trustworthyRows(inRange, series), [inRange, series])

  const primary = series[0] ?? null
  const summary = primary && plotted.length ? summarise(plotted, primary) : null

  function toggleMetric(key) {
    setSelectionByStation((current) => {
      const existing = current[stationId] ?? []
      let next
      if (existing.includes(key)) {
        // Keep at least one metric selected, otherwise the chart has nothing
        // to draw and the user has no way back except re-picking.
        next = existing.length === 1 ? existing : existing.filter((k) => k !== key)
      } else {
        next = [...existing, key]
      }
      return { ...current, [stationId]: next }
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
    setFromDay(from < rows[0].day ? rows[0].day : from)
    setToDay(last.day)
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
            />

            <StatTiles
              station={station}
              rows={plotted}
              metric={primary}
              summary={summary}
              range={fromDay || toDay ? { from: fromDay || 'start', to: toDay || 'end' } : null}
            />

            {plotted.droppedDays > 0 && (
              <p className="chart-note" role="status">
                {plotted.droppedDays} day{plotted.droppedDays === 1 ? '' : 's'} in
                this range excluded from the chart as artefacts &mdash; either no
                readings at all, or values sitting far outside that channel&apos;s
                own distribution. The rows are kept in the database and the
                Parquet export; only the drawing is filtered.
              </p>
            )}

            <TimeSeriesChart
              rows={plotted}
              series={series}
              onHover={setHoverDay}
              hoverDay={hoverDay}
            />

            {station.notes && <p className="station-note muted">{station.notes}</p>}
          </>
        )}
      </div>
    </div>
  )
}

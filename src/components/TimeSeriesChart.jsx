import { useMemo, useRef, useState } from 'react'
import { MS_PER_DAY, get, pick, statLabel } from '../data.js'

/**
 * A time-series chart in plain SVG.
 *
 * No charting library: a line chart with axes is all that is needed, and
 * `AGENTS.md` asks to keep the frontend dependency free until there is a reason
 * not to. A chart package would be ~100 kB of JavaScript to draw two paths.
 *
 * Four things it has to get right, all of them consequences of the data
 * rather than of the drawing:
 *
 * - **Gaps break the line.** A `null` is "not measured", not zero, so the path
 *   is emitted as separate `M`/`L` runs. Joining across a gap would draw a
 *   straight line through a period with no data and imply a measurement that
 *   does not exist.
 * - **Points are hit-tested, not the path.** The mouse target is the nearest
 *   row by date, so a day with a `null` still shows a tooltip saying so.
 * - **The y-domain is shared across series.** Overlaying solar voltage and
 *   temperature on independent axes would let any two curves be made to cross
 *   anywhere, which is meaningless. Sharing one axis also means "no data" reads
 *   honestly as an empty band.
 * - **An out-of-band value is marked, not hidden.** A value outside the band
 *   the pipeline records for its channel is drawn as usual and ringed, because
 *   `AGENTS.md` rule 2 is that a flagged reading is kept. The marker says "look
 *   at this"; it does not say "this is not real".
 */

const PADDING = { top: 16, right: 18, bottom: 34, left: 56 }

//: Above this many flagged points the rings stop being a warning and start being
//: a texture, so the chart says so instead of drawing them all.
const MAX_BREACH_MARKERS = 400

export default function TimeSeriesChart({
  rows,
  series,
  resolution = 'daily',
  height = 340,
  onHover,
  hoverRow,
}) {
  const svgRef = useRef(null)
  const [pointer, setPointer] = useState(null)

  const width = 1000 // viewBox width; the SVG scales to its container

  const geometry = useMemo(() => {
    if (!rows.length || !series.length) return null

    const dates = rows.map((row) => row.date)
    const minDate = Math.min(...dates)
    const maxDate = Math.max(...dates)
    // A single bucket has zero extent, which would divide by zero. Give it a
    // one-day window so the point sits in the middle instead.
    const span = maxDate - minDate || MS_PER_DAY

    const values = []
    for (const row of rows) {
      for (const item of series) {
        const value = get(row, item)
        if (value !== null) values.push(value)
      }
    }
    if (values.length === 0) return null

    let lo = Math.min(...values)
    let hi = Math.max(...values)
    if (lo === hi) {
      // A flat series (power is 0.0 for all of phumy2) still needs a band, or
      // every point lands exactly on the axis and the chart looks broken.
      lo -= 0.5
      hi += 0.5
    } else {
      const pad = (hi - lo) * 0.08
      lo -= pad
      hi += pad
    }

    const plotW = width - PADDING.left - PADDING.right
    const plotH = height - PADDING.top - PADDING.bottom

    const x = (date) => PADDING.left + ((date - minDate) / span) * plotW
    const y = (value) => PADDING.top + plotH - ((value - lo) / (hi - lo)) * plotH

    return { minDate, maxDate, span, lo, hi, plotW, plotH, x, y }
  }, [rows, series, height])

  const markers = useMemo(() => {
    const found = []
    for (const row of rows) {
      for (const breach of row.breaches ?? []) {
        found.push({ row, breach })
      }
    }
    return { shown: found.slice(0, MAX_BREACH_MARKERS), total: found.length }
  }, [rows])

  if (!geometry) {
    return (
      <div className="chart-empty">
        <p>No data for this station and period.</p>
        <p className="muted">
          The {resolution} rollups for this selection contain no values for the
          chosen metrics. That usually means the station did not have the
          channel, not that readings are missing &mdash; see the Data quality tab.
        </p>
      </div>
    )
  }

  const { lo, hi, plotW, plotH, x, y } = geometry
  const yTicks = niceTicks(lo, hi, 5)
  const xTicks = buildDateTicks(rows, resolution)
  const noun = resolution === 'hourly' ? 'hours' : 'days'

  function handleMove(event) {
    const svg = svgRef.current
    if (!svg || !rows.length) return
    const box = svg.getBoundingClientRect()
    // Map client pixels into viewBox units, since the SVG is scaled by CSS.
    const scale = width / box.width
    const vx = (event.clientX - box.left) * scale

    let best = rows[0]
    let bestDistance = Infinity
    for (const row of rows) {
      const distance = Math.abs(x(row.date) - vx)
      if (distance < bestDistance) {
        bestDistance = distance
        best = row
      }
    }
    setPointer({ row: best, vx: x(best.date) })
    onHover?.(best)
  }

  function handleLeave() {
    setPointer(null)
    onHover?.(null)
  }

  return (
    <div className="chart-wrap">
      <svg
        ref={svgRef}
        className="chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Time series for ${series.length} metric(s) over ${rows.length} ${noun}`}
        onMouseMove={handleMove}
        onMouseLeave={handleLeave}
      >
        {yTicks.map((tick) => (
          <g key={tick}>
            <line
              className="grid"
              x1={PADDING.left}
              x2={PADDING.left + plotW}
              y1={y(tick)}
              y2={y(tick)}
            />
            <text className="axis-label" x={PADDING.left - 10} y={y(tick) + 4}>
              {formatTick(tick)}
            </text>
          </g>
        ))}

        <line
          className="axis"
          x1={PADDING.left}
          x2={PADDING.left + plotW}
          y1={PADDING.top + plotH}
          y2={PADDING.top + plotH}
        />

        {xTicks.map((tick) => (
          <text
            key={tick.key}
            className="axis-label"
            x={x(tick.date)}
            y={PADDING.top + plotH + 20}
            textAnchor="middle"
          >
            {tick.label}
          </text>
        ))}

        {series.map((item) => (
          <path
            key={item.key}
            className="series-line"
            d={buildPath(rows, item, x, y)}
            stroke={item.colour}
            fill="none"
          />
        ))}

        {markers.shown.map(({ row, breach }) => {
          const metric = series.find((item) => item.key === breach.metric)
          if (!metric) return null
          return (
            <rect
              key={`${row.key}:${breach.metric}`}
              className="breach-marker"
              x={x(row.date) - 4}
              y={y(breach.value) - 4}
              width={8}
              height={8}
              transform={`rotate(45 ${x(row.date)} ${y(breach.value)})`}
              fill="#fff"
              stroke={metric.colour}
            />
          )
        })}

        {pointer && (
          <g className="hover">
            <line
              className="crosshair"
              x1={pointer.vx}
              x2={pointer.vx}
              y1={PADDING.top}
              y2={PADDING.top + plotH}
            />
            {series.map((item) => {
              const value = get(pointer.row, item)
              if (value === null) return null
              return (
                <circle
                  key={item.key}
                  cx={pointer.vx}
                  cy={y(value)}
                  r={4}
                  fill={item.colour}
                  stroke="#fff"
                  strokeWidth={2}
                />
              )
            })}
          </g>
        )}
      </svg>

      {markers.total > markers.shown.length && (
        <p className="chart-note">
          {markers.total} values in this range fall outside their channel&apos;s
          recorded band; the first {markers.shown.length} are ringed. Use the
          table below to list them all.
        </p>
      )}

      {hoverRow && <Readout row={hoverRow} series={series} />}

      {rows.some((row) => (row.breaches ?? []).length > 0) && (
        <FlaggedTable rows={rows} series={series} />
      )}
    </div>
  )
}

/**
 * The hover readout.
 *
 * Names the statistic behind each number, because the daily battery column is a
 * day's minimum and the hourly one is the hour's mean, and a reader comparing
 * the two views would otherwise be comparing different things under one label.
 */
function Readout({ row, series }) {
  return (
    <div className="chart-readout" role="status">
      <strong>{row.day}</strong>
      <span className="muted">
        {row.nSamples ?? 0} samples over {row.nHours ?? 0} h
        {row.nOutOfRange > 0 && (
          <>
            {' · '}
            {row.nOutOfRange} flagged out-of-range
          </>
        )}
      </span>
      {series.map((item) => {
        const { value, channel, stat } = pick(row, item)
        const breach = (row.breaches ?? []).find((b) => b.metric === item.key)
        return (
          <span key={item.key} className="readout-item">
            <i style={{ background: item.colour }} />
            {item.label}
            {stat && <em className="muted"> ({statLabel(stat)})</em>}:{' '}
            {value === null ? (
              <em className="muted">no data</em>
            ) : (
              `${value.toFixed(item.decimals ?? 1)} ${item.unit}`
            )}
            {breach && (
              <em className="breach">
                {' '}
                outside the recorded {breach.band.lo}&ndash;{breach.band.hi} {item.unit}{' '}
                band for {breach.channel}
              </em>
            )}
          </span>
        )
      })}
    </div>
  )
}

/**
 * Every flagged value in the current range, listed.
 *
 * The rings on the chart are a visual cue; this is the part a reader can cite.
 * Without it, "marked, never dropped" is a claim they have to take on trust.
 */
function FlaggedTable({ rows, series }) {
  const entries = []
  for (const row of rows) {
    for (const breach of row.breaches ?? []) {
      entries.push({ row, breach })
    }
  }
  return (
    <details className="flagged-table">
      <summary>
        {entries.length} flagged value{entries.length === 1 ? '' : 's'} in this
        range &mdash; every one kept in the data
      </summary>
      <table>
        <thead>
          <tr>
            <th>Bucket</th>
            <th>Metric</th>
            <th>Channel</th>
            <th>Value</th>
            <th>Recorded band</th>
            <th>Samples</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(({ row, breach }) => (
            <tr key={`${row.key}:${breach.metric}`}>
              <td>{row.day}</td>
              <td>{breach.metric}</td>
              <td>
                <code>{breach.channel}</code>
              </td>
              <td>
                {breach.value} {breach.band.unit}
              </td>
              <td>
                {breach.band.lo}&ndash;{breach.band.hi} {breach.band.unit}
              </td>
              <td>
                {row.nSamples ?? 0}
                {row.nOutOfRange > 0 && (
                  <span className="muted"> ({row.nOutOfRange} flagged)</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}

/**
 * Build an SVG path, starting a new subpath whenever a value is null.
 *
 * This is the whole reason the chart is not a one-liner: a missing bucket must
 * leave a hole, and `M`/`L` pairs are how SVG expresses that.
 */
function buildPath(rows, metric, x, y) {
  let d = ''
  let penDown = false
  for (const row of rows) {
    const value = get(row, metric)
    if (value === null) {
      penDown = false
      continue
    }
    d += `${penDown ? 'L' : 'M'}${x(row.date).toFixed(2)},${y(value).toFixed(2)} `
    penDown = true
  }
  return d.trim()
}

/** Round tick values to something a human would write down. */
function niceTicks(lo, hi, count) {
  const span = hi - lo
  if (span <= 0) return [lo]
  const rawStep = span / count
  const magnitude = 10 ** Math.floor(Math.log10(rawStep))
  const normalised = rawStep / magnitude
  const step = (normalised >= 5 ? 10 : normalised >= 2 ? 5 : normalised >= 1 ? 2 : 1) * magnitude

  const ticks = []
  for (let tick = Math.ceil(lo / step) * step; tick <= hi; tick += step) {
    // Avoid 0.30000000000000004-style labels from float accumulation.
    ticks.push(Number(tick.toFixed(10)))
  }
  return ticks
}

function formatTick(value) {
  const abs = Math.abs(value)
  if (abs >= 1000) return value.toFixed(0)
  if (abs >= 10) return value.toFixed(0)
  if (abs >= 1) return value.toFixed(1)
  return value.toFixed(2)
}

/** About 6 date labels, taken from the rows actually present. */
function buildDateTicks(rows, resolution) {
  if (rows.length === 0) return []
  const target = 6
  const stride = Math.max(1, Math.round(rows.length / target))
  const ticks = []
  for (let i = 0; i < rows.length; i += stride) {
    const row = rows[i]
    ticks.push({ key: `${i}`, date: row.date, label: shortDate(row.day, resolution) })
  }
  // Always label the final bucket, so the range end is unambiguous.
  const last = rows[rows.length - 1]
  if (ticks[ticks.length - 1]?.key !== `${rows.length - 1}`) {
    ticks.push({
      key: `${rows.length - 1}`,
      date: last.date,
      label: shortDate(last.day, resolution),
    })
  }
  return ticks
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/**
 * A compact label. An hourly row is `YYYY-MM-DD HH:MM`, and printing the hour
 * matters: a year of hourly data whose axis says only "1 Jan" gives no clue
 * whether the line is a daily envelope or a single day.
 */
function shortDate(day, resolution) {
  const [date, time] = day.split(' ')
  const [year, month, dom] = date.split('-')
  const base = resolution === 'hourly' && time ? `${time} ${dom} ${MONTHS[Number(month) - 1]}` : `${dom} ${MONTHS[Number(month) - 1]}`
  return base === '1 Jan' || month === '01' ? year : base
}

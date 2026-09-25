import { useMemo, useRef, useState } from 'react'
import { MS_PER_DAY } from '../data.js'

/**
 * A time-series chart in plain SVG.
 *
 * No charting library: the dataset is daily rollups, so a line chart with axes
 * is all that is needed, and `AGENTS.md` asks to keep the frontend dependency
 * free until there is a reason not to. A chart package would be ~100 kB of
 * JavaScript to draw two paths.
 *
 * Three things it has to get right, all of them consequences of the data
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
 */

const PADDING = { top: 16, right: 18, bottom: 34, left: 56 }

export default function TimeSeriesChart({
  rows,
  series,
  height = 340,
  onHover,
  hoverDay,
}) {
  const svgRef = useRef(null)
  const [pointer, setPointer] = useState(null)

  const width = 1000 // viewBox width; the SVG scales to its container

  const geometry = useMemo(() => {
    if (!rows.length || !series.length) return null

    const dates = rows.map((row) => row.date)
    const minDate = Math.min(...dates)
    const maxDate = Math.max(...dates)
    // A single day has zero extent, which would divide by zero. Give it a
    // one-day window so the point sits in the middle instead.
    const span = maxDate - minDate || MS_PER_DAY

    const values = []
    for (const row of rows) {
      for (const item of series) {
        const value = item.get(row)
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

  if (!geometry) {
    return (
      <div className="chart-empty">
        <p>No data for this station and period.</p>
        <p className="muted">
          The daily rollups for this selection contain no values for the chosen
          metrics. That usually means the station did not have the channel, not
          that readings are missing &mdash; see the Data quality tab.
        </p>
      </div>
    )
  }

  const { lo, hi, plotW, plotH, x, y } = geometry
  const yTicks = niceTicks(lo, hi, 5)
  const xTicks = buildDateTicks(rows, x)

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
        aria-label={`Time series for ${series.length} metric(s) over ${rows.length} days`}
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
            key={tick.day}
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
            d={buildPath(rows, item.get, x, y)}
            stroke={item.colour}
            fill="none"
          />
        ))}

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
              const value = item.get(pointer.row)
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

      {hoverDay && (
        <div className="chart-readout" role="status">
          <strong>{hoverDay.day}</strong>
          <span className="muted">
            {hoverDay.nSamples ?? 0} samples over {hoverDay.nHours ?? 0} h
          </span>
          {series.map((item) => {
            const value = item.get(hoverDay)
            return (
              <span key={item.key} className="readout-item">
                <i style={{ background: item.colour }} />
                {item.label}:{' '}
                {value === null ? (
                  <em className="muted">no data</em>
                ) : (
                  `${value.toFixed(item.decimals ?? 1)} ${item.unit}`
                )}
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
}

/**
 * Build an SVG path, starting a new subpath whenever a value is null.
 *
 * This is the whole reason the chart is not a one-liner: a missing day must
 * leave a hole, and `M`/`L` pairs are how SVG expresses that.
 */
function buildPath(rows, get, x, y) {
  let d = ''
  let penDown = false
  for (const row of rows) {
    const value = get(row)
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
function buildDateTicks(rows, x) {
  if (rows.length === 0) return []
  const target = 6
  const stride = Math.max(1, Math.round(rows.length / target))
  const ticks = []
  for (let i = 0; i < rows.length; i += stride) {
    const row = rows[i]
    ticks.push({ day: row.day, date: row.date, label: shortDate(row.day) })
  }
  // Always label the final day, so the range end is unambiguous.
  const last = rows[rows.length - 1]
  if (ticks[ticks.length - 1]?.day !== last.day) {
    ticks.push({ day: last.day, date: last.date, label: shortDate(last.day) })
  }
  void x
  return ticks
}

function shortDate(day) {
  const [year, month, date] = day.split('-')
  const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const label = `${date} ${names[Number(month) - 1]}`
  return label === '1 Jan' || month === '01' ? year : label
}

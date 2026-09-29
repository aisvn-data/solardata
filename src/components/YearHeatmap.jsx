import { useMemo, useState } from 'react'

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const COLORS = {
  outside: '#cbd5e1', // Gray: outside collection window
  nodata: '#0f172a', // Black: inside collection, but no data submitted
  clean: '#22c55e', // Green: all values within range
  single_oor: '#f97316', // Orange: 1 channel out of range
  multi_oor: '#ef4444', // Red: >1 channels out of range
}

function isLeapYear(year) {
  const y = Number(year)
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0
}

/**
 * Format Date object to YYYY-MM-DD
 */
function toDateStr(year, month, day) {
  const m = String(month + 1).padStart(2, '0')
  const d = String(day).padStart(2, '0')
  return `${year}-${m}-${d}`
}

export default function YearHeatmap({
  station,
  year,
  dailyRows = [],
  fromDay,
  toDay,
  onSelectDay,
}) {
  const [hoverDay, setHoverDay] = useState(null)

  const numYear = Number(year) || 2020
  const daysInYear = isLeapYear(numYear) ? 366 : 365

  // Map daily rows by dateDay (YYYY-MM-DD)
  const rowMap = useMemo(() => {
    const map = new Map()
    for (const r of dailyRows) {
      if (r?.dateDay) {
        map.set(r.dateDay, r)
      }
    }
    return map
  }, [dailyRows])

  // Collection boundary strings in local wall clock date
  const { startDay, endDay } = useMemo(() => {
    if (!station) return { startDay: null, endDay: null }
    // first_ts_utc and last_ts_utc are in UTC. Vietnam is UTC+7.
    let start = null
    let end = null
    if (station.first_ts_utc) {
      const d = new Date(Date.parse(station.first_ts_utc) + 7 * 3600 * 1000)
      start = d.toISOString().slice(0, 10)
    }
    if (station.last_ts_utc) {
      const d = new Date(Date.parse(station.last_ts_utc) + 7 * 3600 * 1000)
      end = d.toISOString().slice(0, 10)
    }
    return { startDay: start, endDay: end }
  }, [station])

  // Build grid of days
  const { days, monthLabels, totalWeeks } = useMemo(() => {
    const list = []
    const mLabels = []
    let currentMonth = -1

    // First day of the year (0 = Sun, 1 = Mon ... 6 = Sat)
    const jan1 = new Date(Date.UTC(numYear, 0, 1))
    // Convert to Monday = 0 ... Sunday = 6
    const jan1DayOfWeek = (jan1.getUTCDay() + 6) % 7

    let dayCounter = 0
    let col = 0
    let row = jan1DayOfWeek

    const d = new Date(Date.UTC(numYear, 0, 1))

    while (dayCounter < daysInYear) {
      const m = d.getUTCMonth()
      const dayNum = d.getUTCDate()
      const dateStr = toDateStr(numYear, m, dayNum)

      if (m !== currentMonth) {
        currentMonth = m
        mLabels.push({ month: MONTH_NAMES[m], col })
      }

      // Determine quality / status
      const isOutside = (startDay && dateStr < startDay) || (endDay && dateStr > endDay)
      const rowData = rowMap.get(dateStr)

      let status = 'outside'
      let oorChannels = []
      let nSamples = 0

      if (isOutside) {
        status = 'outside'
      } else if (!rowData || (rowData.nSamples ?? 0) === 0) {
        status = 'nodata'
      } else {
        nSamples = rowData.nSamples ?? 0
        // Find channels with out-of-range counts
        if (rowData.oor) {
          for (const [ch, count] of Object.entries(rowData.oor)) {
            if (count > 0) {
              oorChannels.push({ channel: ch, count })
            }
          }
        }
        if (oorChannels.length === 0) {
          status = 'clean'
        } else if (oorChannels.length === 1) {
          status = 'single_oor'
        } else {
          status = 'multi_oor'
        }
      }

      const isSelected = fromDay && toDay && dateStr >= fromDay && dateStr <= toDay

      list.push({
        date: dateStr,
        dayNum,
        month: m,
        col,
        row,
        status,
        color: COLORS[status],
        nSamples,
        oorChannels,
        isSelected,
      })

      // Advance one day
      dayCounter++
      d.setUTCDate(d.getUTCDate() + 1)
      row++
      if (row > 6) {
        row = 0
        col++
      }
    }

    return { days: list, monthLabels: mLabels, totalWeeks: col + 1 }
  }, [numYear, daysInYear, startDay, endDay, rowMap, fromDay, toDay])

  const cellSize = 13
  const cellGap = 3
  const leftPad = 32
  const topPad = 22
  const svgWidth = leftPad + totalWeeks * (cellSize + cellGap) + 16
  const svgHeight = topPad + 7 * (cellSize + cellGap) + 8

  return (
    <div className="year-heatmap-container">
      <div className="heatmap-header">
        <div className="heatmap-title">
          <h4>{year} Annual Data Quality Heatmap</h4>
          <span className="muted small">
            365-day calendar of telemetry collection and physical band conformance for {station?.display_name || station?.station_id}
          </span>
        </div>
      </div>

      <div className="heatmap-svg-scroll">
        <svg
          className="heatmap-svg"
          width={svgWidth}
          height={svgHeight}
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        >
          {/* Month labels */}
          {monthLabels.map((lbl, idx) => (
            <text
              key={idx}
              x={leftPad + lbl.col * (cellSize + cellGap)}
              y={topPad - 6}
              className="heatmap-month-label"
            >
              {lbl.month}
            </text>
          ))}

          {/* Day of week labels */}
          {DAY_LABELS.map((dayName, rIdx) => {
            if (rIdx % 2 !== 0) return null // Show Mon, Wed, Fri, Sun
            return (
              <text
                key={dayName}
                x={leftPad - 8}
                y={topPad + rIdx * (cellSize + cellGap) + cellSize - 2}
                className="heatmap-day-label"
                textAnchor="end"
              >
                {dayName}
              </text>
            )
          })}

          {/* Day cells */}
          {days.map((d) => {
            const x = leftPad + d.col * (cellSize + cellGap)
            const y = topPad + d.row * (cellSize + cellGap)

            return (
              <rect
                key={d.date}
                x={x}
                y={y}
                width={cellSize}
                height={cellSize}
                rx={2.5}
                ry={2.5}
                fill={d.color}
                className={`heatmap-cell ${d.isSelected ? 'cell-selected' : ''}`}
                stroke={d.isSelected ? '#0284c7' : 'none'}
                strokeWidth={d.isSelected ? 2 : 0}
                onMouseEnter={() => setHoverDay(d)}
                onMouseLeave={() => setHoverDay(null)}
                onClick={() => onSelectDay && onSelectDay(d.date)}
              />
            )
          })}
        </svg>
      </div>

      {/* Tooltip banner / details */}
      <div className="heatmap-status-bar">
        {hoverDay ? (
          <div className="heatmap-tooltip">
            <strong>{hoverDay.date}</strong> &bull;{' '}
            {hoverDay.status === 'outside' && <span className="muted">Outside collection window</span>}
            {hoverDay.status === 'nodata' && (
              <span className="badge-status-black">No telemetry logged (0 samples)</span>
            )}
            {hoverDay.status === 'clean' && (
              <span className="badge-status-green">
                ✓ All channels within range ({hoverDay.nSamples.toLocaleString()} samples)
              </span>
            )}
            {hoverDay.status === 'single_oor' && (
              <span className="badge-status-orange">
                ⚠ 1 channel out of range ({hoverDay.oorChannels[0].channel}: {hoverDay.oorChannels[0].count} readings)
              </span>
            )}
            {hoverDay.status === 'multi_oor' && (
              <span className="badge-status-red">
                ✕ {hoverDay.oorChannels.length} channels out of range ({hoverDay.oorChannels.map((c) => c.channel).join(', ')})
              </span>
            )}
            <span className="muted small" style={{ marginLeft: '12px' }}>
              (Click to focus chart on this day)
            </span>
          </div>
        ) : (
          <div className="heatmap-tooltip muted small">
            Hover over any day to inspect sample count and plausibility flags &bull; Click to filter range
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="heatmap-legend">
        <span className="legend-label">Legend:</span>
        <div className="legend-item">
          <span className="legend-swatch" style={{ background: COLORS.outside }} />
          <span>Outside Collection Window</span>
        </div>
        <div className="legend-item">
          <span className="legend-swatch" style={{ background: COLORS.nodata }} />
          <span>No Data Submitted</span>
        </div>
        <div className="legend-item">
          <span className="legend-swatch" style={{ background: COLORS.clean }} />
          <span>All Channels Within Range</span>
        </div>
        <div className="legend-item">
          <span className="legend-swatch" style={{ background: COLORS.single_oor }} />
          <span>1 Channel Out of Range</span>
        </div>
        <div className="legend-item">
          <span className="legend-swatch" style={{ background: COLORS.multi_oor }} />
          <span>&gt;1 Channel Out of Range</span>
        </div>
      </div>
    </div>
  )
}

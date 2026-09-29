import { useMemo, useState } from 'react'

const X_TICKS = [
  { min: 0, label: '00:00' },
  { min: 180, label: '03:00' },
  { min: 360, label: '06:00' },
  { min: 540, label: '09:00' },
  { min: 720, label: '12:00 (Noon)' },
  { min: 900, label: '15:00' },
  { min: 1080, label: '18:00' },
  { min: 1260, label: '21:00' },
  { min: 1440, label: '24:00' },
]

function fmtVal(val, decimals = 1) {
  if (val === null || val === undefined) return '—'
  const abs = Math.abs(val)
  if (abs >= 1000 || abs >= 10) return val.toFixed(0)
  return val.toFixed(decimals ?? 1)
}

function computeBounds(values) {
  if (values.length === 0) return { min: 0, max: 1 }
  let min = values[0]
  let max = values[0]
  for (let i = 1; i < values.length; i++) {
    const v = values[i]
    if (v < min) min = v
    if (v > max) max = v
  }
  if (min === max) {
    min = min > 0 ? 0 : min - 1
    max = max < 0 ? 0 : max + 1
  }
  const span = max - min
  const pad = span * 0.05
  return { min: min - pad, max: max + pad, dataMin: min, dataMax: max }
}

export default function DiurnalChart({
  rows = [],
  channels = [],
  station,
  resolution,
}) {
  const publishedChannels = useMemo(
    () => channels.filter((c) => c.kind !== 'text'),
    [channels],
  )

  const [leftKey, setLeftKey] = useState('')
  const [rightKey, setRightKey] = useState('')
  const [showAverage, setShowAverage] = useState(true)

  // Initialize selected channels if unset
  const activeLeftKey = leftKey || publishedChannels[0]?.channel || ''
  const activeRightKey =
    rightKey !== undefined && rightKey !== ''
      ? rightKey
      : publishedChannels.length > 1
      ? publishedChannels[1]?.channel
      : ''

  const leftChannel = publishedChannels.find((c) => c.channel === activeLeftKey) ?? publishedChannels[0] ?? null
  const rightChannel = activeRightKey ? publishedChannels.find((c) => c.channel === activeRightKey) ?? null : null

  // Group readings by day and extract minute of day
  const { dayGroups, leftBounds, rightBounds, hourlyLeftAvg, hourlyRightAvg } = useMemo(() => {
    const groups = new Map()
    const leftVals = []
    const rightVals = []
    const leftHourlySums = Array(24).fill(0)
    const leftHourlyCounts = Array(24).fill(0)
    const rightHourlySums = Array(24).fill(0)
    const rightHourlyCounts = Array(24).fill(0)

    for (const r of rows) {
      if (!r.day) continue
      const dateDay = r.dateDay || r.day.slice(0, 10)
      const timeStr = r.day.slice(11, 16)
      if (!timeStr || !timeStr.includes(':')) continue

      const [hStr, mStr] = timeStr.split(':')
      const hour = parseInt(hStr, 10)
      const minute = parseInt(mStr, 10)
      if (Number.isNaN(hour) || Number.isNaN(minute)) continue

      const minOfDay = hour * 60 + minute

      const lVal = leftChannel ? r.values?.[leftChannel.channel] ?? null : null
      const rVal = rightChannel ? r.values?.[rightChannel.channel] ?? null : null

      if (lVal !== null) {
        leftVals.push(lVal)
        if (hour >= 0 && hour < 24) {
          leftHourlySums[hour] += lVal
          leftHourlyCounts[hour] += 1
        }
      }
      if (rVal !== null) {
        rightVals.push(rVal)
        if (hour >= 0 && hour < 24) {
          rightHourlySums[hour] += rVal
          rightHourlyCounts[hour] += 1
        }
      }

      if (!groups.has(dateDay)) {
        groups.set(dateDay, [])
      }
      groups.get(dateDay).push({
        minOfDay,
        hour,
        lVal,
        rVal,
      })
    }

    // Sort entries within each day by minute
    for (const pts of groups.values()) {
      pts.sort((a, b) => a.minOfDay - b.minOfDay)
    }

    const lBounds = computeBounds(leftVals)
    const rBounds = computeBounds(rightVals)

    const leftAvg = leftHourlySums.map((sum, h) =>
      leftHourlyCounts[h] > 0 ? sum / leftHourlyCounts[h] : null,
    )
    const rightAvg = rightHourlySums.map((sum, h) =>
      rightHourlyCounts[h] > 0 ? sum / rightHourlyCounts[h] : null,
    )

    return {
      dayGroups: [...groups.entries()],
      leftBounds: lBounds,
      rightBounds: rBounds,
      hourlyLeftAvg: leftAvg,
      hourlyRightAvg: rightAvg,
    }
  }, [rows, leftChannel, rightChannel])

  const width = 960
  const height = 380
  const padLeft = 68
  const padRight = rightChannel ? 68 : 24
  const padTop = 36
  const padBottom = 40
  const chartW = width - padLeft - padRight
  const chartH = height - padTop - padBottom

  const scaleX = (min) => padLeft + (min / 1440) * chartW
  const scaleYLeft = (val) => {
    if (val === null || val === undefined) return null
    const norm = (val - leftBounds.min) / (leftBounds.max - leftBounds.min || 1)
    return padTop + (1 - norm) * chartH
  }
  const scaleYRight = (val) => {
    if (val === null || val === undefined) return null
    const norm = (val - rightBounds.min) / (rightBounds.max - rightBounds.min || 1)
    return padTop + (1 - norm) * chartH
  }

  // Generate SVG path for a day
  function buildPath(pts, valGetter, scaleFn) {
    let d = ''
    let active = false
    for (const pt of pts) {
      const v = valGetter(pt)
      if (v === null || v === undefined) {
        active = false
        continue
      }
      const x = scaleX(pt.minOfDay)
      const y = scaleFn(v)
      if (!active) {
        d += `M${x.toFixed(1)},${y.toFixed(1)}`
        active = true
      } else {
        d += ` L${x.toFixed(1)},${y.toFixed(1)}`
      }
    }
    return d
  }

  // Generate 5 tick marks for Y axis
  function buildYTicks(bounds) {
    const ticks = []
    for (let i = 0; i <= 4; i++) {
      const ratio = i / 4
      const val = bounds.min + ratio * (bounds.max - bounds.min)
      ticks.push(val)
    }
    return ticks
  }

  const leftTicks = buildYTicks(leftBounds)
  const rightTicks = buildYTicks(rightBounds)

  const leftColor = leftChannel?.colour || '#2563eb'
  const rightColor = rightChannel?.colour || '#ea580c'

  // Average line paths
  const leftAvgPath = useMemo(() => {
    let d = ''
    let active = false
    for (let h = 0; h < 24; h++) {
      const v = hourlyLeftAvg[h]
      if (v === null) continue
      const x = scaleX(h * 60 + 30)
      const y = scaleYLeft(v)
      if (!active) {
        d += `M${x.toFixed(1)},${y.toFixed(1)}`
        active = true
      } else {
        d += ` L${x.toFixed(1)},${y.toFixed(1)}`
      }
    }
    return d
  }, [hourlyLeftAvg, leftBounds])

  const rightAvgPath = useMemo(() => {
    if (!rightChannel) return ''
    let d = ''
    let active = false
    for (let h = 0; h < 24; h++) {
      const v = hourlyRightAvg[h]
      if (v === null) continue
      const x = scaleX(h * 60 + 30)
      const y = scaleYRight(v)
      if (!active) {
        d += `M${x.toFixed(1)},${y.toFixed(1)}`
        active = true
      } else {
        d += ` L${x.toFixed(1)},${y.toFixed(1)}`
      }
    }
    return d
  }, [hourlyRightAvg, rightBounds, rightChannel])

  return (
    <div className="diurnal-chart-container">
      <div className="diurnal-header">
        <div className="diurnal-title">
          <h4>24-Hour Diurnal Overlay (0:00 &ndash; 24:00 Local Time)</h4>
          <span className="muted small">
            Superimposes all {dayGroups.length} days in the active timeframe over a single diurnal progression with independent left and right Y-axes.
          </span>
        </div>

        <div className="diurnal-controls">
          <label className="diurnal-picker">
            <span style={{ color: leftColor, fontWeight: '700' }}>Left Axis (Y1):</span>
            <select
              value={leftChannel?.channel || ''}
              onChange={(e) => setLeftKey(e.target.value)}
            >
              {publishedChannels.map((c) => (
                <option key={c.channel} value={c.channel}>
                  {c.label} ({c.unit || 'unitless'})
                </option>
              ))}
            </select>
          </label>

          <label className="diurnal-picker">
            <span style={{ color: rightColor, fontWeight: '700' }}>Right Axis (Y2):</span>
            <select
              value={rightChannel?.channel || ''}
              onChange={(e) => setRightKey(e.target.value)}
            >
              <option value="">(None)</option>
              {publishedChannels.map((c) => (
                <option key={c.channel} value={c.channel}>
                  {c.label} ({c.unit || 'unitless'})
                </option>
              ))}
            </select>
          </label>

          <label className="diurnal-checkbox">
            <input
              type="checkbox"
              checked={showAverage}
              onChange={(e) => setShowAverage(e.target.checked)}
            />
            <span>Show Mean Profile</span>
          </label>
        </div>
      </div>

      <div className="diurnal-svg-wrap">
        <svg
          className="diurnal-svg"
          viewBox={`0 0 ${width} ${height}`}
          width="100%"
          height="100%"
        >
          {/* Background grid */}
          <rect
            x={padLeft}
            y={padTop}
            width={chartW}
            height={chartH}
            fill="#ffffff"
            stroke="#e2e8f0"
            strokeWidth={1}
          />

          {/* Vertical hour guidelines */}
          {X_TICKS.map((t) => {
            const x = scaleX(t.min)
            const isNoon = t.min === 720
            const isDawnDusk = t.min === 360 || t.min === 1080
            return (
              <g key={t.min}>
                <line
                  x1={x}
                  y1={padTop}
                  x2={x}
                  y2={padTop + chartH}
                  stroke={isNoon ? '#cbd5e1' : isDawnDusk ? '#e2e8f0' : '#f1f5f9'}
                  strokeWidth={isNoon ? 1.5 : 1}
                  strokeDasharray={isNoon ? 'none' : '3 3'}
                />
                <text
                  x={x}
                  y={padTop + chartH + 16}
                  textAnchor="middle"
                  className={`diurnal-x-label ${isNoon ? 'bold' : ''}`}
                >
                  {t.label}
                </text>
              </g>
            )
          })}

          {/* Left Y Axis Ticks */}
          {leftTicks.map((val, idx) => {
            const y = scaleYLeft(val)
            return (
              <g key={idx}>
                <line
                  x1={padLeft - 5}
                  y1={y}
                  x2={padLeft}
                  y2={y}
                  stroke={leftColor}
                  strokeWidth={1.2}
                />
                <line
                  x1={padLeft}
                  y1={y}
                  x2={padLeft + chartW}
                  y2={y}
                  stroke="#f8fafc"
                  strokeWidth={1}
                />
                <text
                  x={padLeft - 8}
                  y={y + 4}
                  textAnchor="end"
                  fill={leftColor}
                  className="diurnal-axis-text"
                >
                  {fmtVal(val, leftChannel?.decimals)}
                </text>
              </g>
            )
          })}

          {/* Left Axis Title */}
          {leftChannel && (
            <text
              x={padLeft}
              y={padTop - 12}
              textAnchor="start"
              fill={leftColor}
              fontWeight="700"
              fontSize="0.82rem"
            >
              {leftChannel.label} ({leftChannel.unit || 'unitless'})
            </text>
          )}

          {/* Right Y Axis Ticks */}
          {rightChannel &&
            rightTicks.map((val, idx) => {
              const y = scaleYRight(val)
              return (
                <g key={idx}>
                  <line
                    x1={padLeft + chartW}
                    y1={y}
                    x2={padLeft + chartW + 5}
                    y2={y}
                    stroke={rightColor}
                    strokeWidth={1.2}
                  />
                  <text
                    x={padLeft + chartW + 8}
                    y={y + 4}
                    textAnchor="start"
                    fill={rightColor}
                    className="diurnal-axis-text"
                  >
                    {fmtVal(val, rightChannel?.decimals)}
                  </text>
                </g>
              )
            })}

          {/* Right Axis Title */}
          {rightChannel && (
            <text
              x={padLeft + chartW}
              y={padTop - 12}
              textAnchor="end"
              fill={rightColor}
              fontWeight="700"
              fontSize="0.82rem"
            >
              {rightChannel.label} ({rightChannel.unit || 'unitless'})
            </text>
          )}

          {/* Overlaid Daily Curves - Left Channel */}
          {leftChannel &&
            dayGroups.map(([dateDay, pts]) => {
              const d = buildPath(pts, (p) => p.lVal, scaleYLeft)
              if (!d) return null
              return (
                <path
                  key={`left-${dateDay}`}
                  d={d}
                  fill="none"
                  stroke={leftColor}
                  strokeWidth={1.2}
                  strokeOpacity={0.28}
                  className="diurnal-trace"
                />
              )
            })}

          {/* Overlaid Daily Curves - Right Channel */}
          {rightChannel &&
            dayGroups.map(([dateDay, pts]) => {
              const d = buildPath(pts, (p) => p.rVal, scaleYRight)
              if (!d) return null
              return (
                <path
                  key={`right-${dateDay}`}
                  d={d}
                  fill="none"
                  stroke={rightColor}
                  strokeWidth={1.2}
                  strokeOpacity={0.28}
                  className="diurnal-trace"
                />
              )
            })}

          {/* Mean Profile Overlays */}
          {showAverage && leftAvgPath && (
            <path
              d={leftAvgPath}
              fill="none"
              stroke={leftColor}
              strokeWidth={3}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}

          {showAverage && rightAvgPath && (
            <path
              d={rightAvgPath}
              fill="none"
              stroke={rightColor}
              strokeWidth={3}
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeDasharray="4 2"
            />
          )}
        </svg>
      </div>

      <div className="diurnal-footer">
        <div className="diurnal-summary">
          {leftChannel && (
            <span className="diurnal-badge" style={{ borderColor: leftColor, color: leftColor }}>
              ● {leftChannel.label}: {fmtVal(leftBounds.dataMin, leftChannel.decimals)} &hellip;{' '}
              {fmtVal(leftBounds.dataMax, leftChannel.decimals)} {leftChannel.unit}
            </span>
          )}
          {rightChannel && (
            <span
              className="diurnal-badge"
              style={{ borderColor: rightColor, color: rightColor, marginLeft: '8px' }}
            >
              ● {rightChannel.label}: {fmtVal(rightBounds.dataMin, rightChannel.decimals)} &hellip;{' '}
              {fmtVal(rightBounds.dataMax, rightChannel.decimals)} {rightChannel.unit}
            </span>
          )}
          {showAverage && (
            <span className="muted small" style={{ marginLeft: '16px' }}>
              Thick lines show the average 24-hour diurnal profile across all selected days.
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

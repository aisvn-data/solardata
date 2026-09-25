import { METRICS } from '../data.js'

/**
 * Year, date range and metric selection.
 *
 * The date inputs are constrained to the loaded year's bounds and to days that
 * actually have rows. Offering 2020-02-29 for phumy2, which starts in June,
 * would just produce an empty chart with no explanation.
 */
export default function TimeControls({
  years,
  year,
  onYearChange,
  rows,
  fromDay,
  toDay,
  onRangeChange,
  metrics,
  selected,
  onMetricToggle,
  onRangePreset,
}) {
  const firstDay = rows[0]?.day ?? ''
  const lastDay = rows[rows.length - 1]?.day ?? ''

  return (
    <div className="controls">
      <div className="control-row">
        <label className="control">
          <span>Year</span>
          <select value={year} onChange={(e) => onYearChange(e.target.value)}>
            {years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </label>

        <label className="control">
          <span>From</span>
          <input
            type="date"
            value={fromDay}
            min={firstDay}
            max={toDay || lastDay}
            onChange={(e) => onRangeChange(e.target.value, toDay)}
          />
        </label>

        <label className="control">
          <span>To</span>
          <input
            type="date"
            value={toDay}
            min={fromDay || firstDay}
            max={lastDay}
            onChange={(e) => onRangeChange(fromDay, e.target.value)}
          />
        </label>

        <div className="control presets">
          <span>Quick range</span>
          <div className="preset-buttons">
            {[
              ['Full year', null],
              ['Last 30 days', -30],
              ['Last 90 days', -90],
            ].map(([label, days]) => (
              <button key={label} type="button" onClick={() => onRangePreset(days)}>
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <fieldset className="metric-picker">
        <legend>Metrics</legend>
        {METRICS.map((metric) => {
          // A metric with no data for this station is shown disabled rather
          // than hidden, so it is visible *why* a channel is missing.
          const available = metrics.includes(metric.key)
          const checked = selected.includes(metric.key)
          return (
            <label key={metric.key} className={available ? '' : 'unavailable'}>
              <input
                type="checkbox"
                checked={checked}
                disabled={!available}
                onChange={() => onMetricToggle(metric.key)}
              />
              <i style={{ background: available ? metric.colour : '#cbd5e0' }} />
              {metric.label}
              <em>{metric.unit}</em>
            </label>
          )
        })}
      </fieldset>
    </div>
  )
}

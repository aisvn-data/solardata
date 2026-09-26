import { GRANULARITIES } from '../data.js'

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/**
 * Year, resolution, month, date range and metric selection.
 *
 * The date inputs are constrained to the loaded year's bounds and to days that
 * actually have rows. Offering 2020-02-29 for phumy2, which starts in June,
 * would just produce an empty chart with no explanation.
 *
 * The month select is a shortcut over the same range, not an independent filter:
 * it offers only the months that have data, and choosing one moves the From/To
 * inputs to that month's bounds in the data. See `activeMonth` in
 * `StationExplorer` for why it is derived rather than stored.
 *
 * The resolution switch changes the rollup, not the filter: `Day` reads
 * `readings_daily` (a mean over the day's hours) and `Hour` reads
 * `readings_hourly` (a mean over the hour's ~30 samples). Both are means, so
 * `Hour` is the finest the site goes -- the native 119 s cadence is in the
 * Parquet export, which is a download rather than something a browser fetches.
 */
export default function TimeControls({
  years,
  year,
  onYearChange,
  rows,
  fromDay,
  toDay,
  onRangeChange,
  channels,
  selected,
  onMetricToggle,
  onRangePreset,
  months,
  activeMonth,
  onMonthChange,
  granularities,
  resolution,
  onResolutionChange,
  hideFlagged,
  onHideFlaggedChange,
}) {
  const firstDay = rows[0]?.dateDay ?? ''
  const lastDay = rows[rows.length - 1]?.dateDay ?? ''
  const available = GRANULARITIES.filter((g) => granularities.includes(g.folder))

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

        <div className="control">
          <span>Resolution</span>
          <div className="preset-buttons">
            {available.map((g) => (
              <button
                key={g.folder}
                type="button"
                className={resolution === g.folder ? 'active' : ''}
                aria-pressed={resolution === g.folder}
                onClick={() => onResolutionChange(g.folder)}
              >
                {g.label}
              </button>
            ))}
          </div>
        </div>

        <label className="control">
          <span>Month</span>
          <select value={activeMonth} onChange={(e) => onMonthChange(e.target.value)}>
            <option value="">All</option>
            {months.map((key) => {
              const [y, m] = key.split('-')
              return (
                <option key={key} value={key}>
                  {MONTH_NAMES[Number(m) - 1]} {y}
                </option>
              )
            })}
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
        <legend>Channels ({channels.length})</legend>
        {channels.map((channel) => {
          const checked = selected.includes(channel.key)
          return (
            <label key={channel.key} className={checked ? 'picked' : ''}>
              <input
                type="checkbox"
                checked={checked}
                onChange={() => onMetricToggle(channel.key)}
              />
              <i style={{ background: channel.colour }} />
              {channel.label}
              {channel.unit && <em>{channel.unit}</em>}
            </label>
          )
        })}
        {channels.length === 0 && (
          <p className="muted small">
            This station has no numeric channels in the published rollups. That is
            why it is not offered in the station list.
          </p>
        )}
      </fieldset>

      <p className="control-hint">
        <label className="flag-toggle">
          <input
            type="checkbox"
            checked={hideFlagged}
            onChange={(e) => onHideFlaggedChange(e.target.checked)}
          />
          Hide values that are outside their channel&apos;s recorded band, or built
          partly from samples that are
        </label>
        {' · '}
        The band is the one the pipeline applies to every raw reading. Hiding one
        is a reading aid, not a judgement: the value stays in the database, in the
        Parquet export and in the flagged list under the chart.
      </p>
    </div>
  )
}

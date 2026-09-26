/**
 * Headline numbers for the current station, year and date range.
 *
 * `stat` is the statistic behind the plotted column, and it is named in the
 * labels because it is not always a mean: the daily battery column is the day's
 * minimum, so a "Battery mean" tile over daily rows would be a mean of minima.
 * The min and max tiles are still taken across the plotted values, which for a
 * minimum column makes the "max" the highest of the day's lows.
 */
export default function StatTiles({ station, rows, metric, stat, summary, range }) {
  const tiles = []
  const noun = stat === 'min' ? 'lowest daily' : 'mean'

  if (summary && summary.count > 0) {
    const decimals = metric.decimals ?? 1
    tiles.push({
      label: `${metric.label} ${noun}`,
      value: summary.mean.toFixed(decimals),
      unit: metric.unit,
    })
    tiles.push({
      label: `${metric.label} min`,
      value: summary.min.toFixed(decimals),
      unit: metric.unit,
    })
    tiles.push({
      label: `${metric.label} max`,
      value: summary.max.toFixed(decimals),
      unit: metric.unit,
    })
    if (summary.total !== null) {
      tiles.push({
        label: 'Energy total',
        value: Math.round(summary.total).toLocaleString(),
        unit: 'Wh',
      })
    }
  }

  // Coverage, not values: how much of the selected range actually reported.
  // A bucket with no samples is a real gap and belongs next to the statistics.
  const withSamples = rows.filter((row) => (row.nSamples ?? 0) > 0)
  const totalSamples = withSamples.reduce((sum, row) => sum + (row.nSamples ?? 0), 0)
  const totalHours = withSamples.reduce((sum, row) => sum + (row.nHours ?? 0), 0)
  const bucket = rows.length > 1 && rows[0]?.nHours === 1 ? 'Hours' : 'Days'
  tiles.push({
    label: `${bucket} reported`,
    value: `${withSamples.length}/${rows.length}`,
    unit: '',
  })
  tiles.push({ label: 'Readings', value: totalSamples.toLocaleString(), unit: '' })
  tiles.push({ label: 'Hours covered', value: totalHours.toLocaleString(), unit: 'h' })

  return (
    <div className="stat-tiles">
      {tiles.map((tile) => (
        <div className="stat-tile" key={tile.label}>
          <span className="stat-label">{tile.label}</span>
          <span className="stat-value">
            {tile.value}
            {tile.unit && <em>{tile.unit}</em>}
          </span>
        </div>
      ))}
      <div className="stat-tile context">
        <span className="stat-label">Station</span>
        <span className="stat-value small">{station?.display_name ?? '—'}</span>
        <span className="muted">
          {range ? `${range.from} → ${range.to}` : 'full year'}
        </span>
      </div>
    </div>
  )
}

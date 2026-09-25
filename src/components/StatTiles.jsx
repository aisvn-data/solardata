/** Headline numbers for the current station, year and date range. */
export default function StatTiles({ station, rows, metric, summary, range }) {
  const tiles = []

  if (summary && summary.count > 0) {
    const decimals = metric.decimals ?? 1
    tiles.push({
      label: `${metric.label} mean`,
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
  // A day with no samples is a real gap and belongs next to the statistics.
  const withSamples = rows.filter((row) => (row.nSamples ?? 0) > 0)
  const totalSamples = withSamples.reduce((sum, row) => sum + (row.nSamples ?? 0), 0)
  const totalHours = withSamples.reduce((sum, row) => sum + (row.nHours ?? 0), 0)
  tiles.push({
    label: 'Days reported',
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

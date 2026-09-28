import { useEffect, useState } from 'react'
import { loadQuality, loadCuration, loadNormalization } from '../data.js'

/**
 * The per-station data-quality report.
 *
 * This is the view a maintainer needs before trusting a chart. The station
 * explorer shows values; this shows what each station records, what it actually
 * recorded, what the pipeline expects of it, and what nobody could resolve.
 *
 * It reads `quality.json`, written by `python -m etl report` from the same
 * `etl.report.collect` call that produces the committed
 * `data/processed/quality_report.md`, so the two cannot disagree.
 *
 * The shape follows the pipeline. Bands belong to a (station, channel) pair, so
 * the tables are per station rather than per column: a reader comparing two
 * stations' batteries is comparing two different sensors, and 0.8's single
 * column-name-keyed table could not show that.
 */
export default function QualityInspector() {
  const [report, setReport] = useState(null)
  const [curation, setCuration] = useState(null)
  const [normalization, setNormalization] = useState(null)
  const [error, setError] = useState(null)
  const [section, setSection] = useState('stations')
  const [editingBands, setEditingBands] = useState(false)
  const [bandEdits, setBandEdits] = useState({})

  useEffect(() => {
    let cancelled = false
    loadQuality()
      .then((data) => {
        if (!cancelled) setReport(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    loadCuration()
      .then((data) => {
        if (!cancelled) setCuration(data)
      })
      .catch(() => {
        // Curation is optional fallback
      })
    loadNormalization()
      .then((data) => {
        if (!cancelled) setNormalization(data)
      })
      .catch(() => {
        // Normalization is optional fallback
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return (
      <div className="error-box">
        <h3>Could not load the quality report</h3>
        <p className="muted">{error}</p>
        <p>
          Run <code>python -m etl report</code> to write{' '}
          <code>public/data/quality.json</code>.
        </p>
      </div>
    )
  }
  if (!report) return <p className="muted">Loading quality report…</p>

  const totals = report.totals
  const flags = report.flag_totals ?? {}
  const audit = report.band_audit ?? { rows: [], threshold: 0.01, unjustified: [] }
  const windows = report.windows ?? { null: [], bad: [] }
  const notes = report.notes ?? []
  const rejects = report.rejects_by_reason ?? []
  const first = report.stations.reduce(
    (a, s) => (a === null || (s.first_ts_utc ?? '') < a ? s.first_ts_utc : a),
    null,
  )
  const last = report.stations.reduce(
    (a, s) => (a === null || (s.last_ts_utc ?? '') > a ? s.last_ts_utc : a),
    null,
  )

  const tabs = [
    ['stations', `Stations (${report.stations.length})`],
    ['bands', `Bands & Curation (${audit.rows.length})`],
    ['normalization', 'Hardware & Normalization'],
    ['flags', `Flags (${Object.keys(flags).length})`],
    ['windows', `Windows (${windows.null.length + windows.bad.length})`],
    ['notes', `Notes (${notes.length})`],
    ['rejects', `Rejected cells (${totals.rejects.toLocaleString()})`],
  ]

  const handleDownloadCuration = () => {
    const base = curation ?? { stations: {} }
    const updated = JSON.parse(JSON.stringify(base))
    for (const [key, edit] of Object.entries(bandEdits)) {
      const [stationId, channel] = key.split('.')
      if (!updated.stations) updated.stations = {}
      if (!updated.stations[stationId]) {
        updated.stations[stationId] = { open_questions: [], channels: {} }
      }
      if (!updated.stations[stationId].channels) {
        updated.stations[stationId].channels = {}
      }
      if (!updated.stations[stationId].channels[channel]) {
        updated.stations[stationId].channels[channel] = {
          label: channel,
          description: '',
          band: [null, null],
          band_note: '',
          publish: true,
          exclude: null,
          exclude_note: '',
          stats: ['avg', 'min', 'max'],
        }
      }
      const chObj = updated.stations[stationId].channels[channel]
      const lo = edit.band_lo === '' || edit.band_lo === null ? null : Number(edit.band_lo)
      const hi = edit.band_hi === '' || edit.band_hi === null ? null : Number(edit.band_hi)
      chObj.band = [Number.isNaN(lo) ? null : lo, Number.isNaN(hi) ? null : hi]
      if (edit.note !== undefined) {
        chObj.band_note = edit.note
      }
    }
    const blob = new Blob([JSON.stringify(updated, null, 2) + '\n'], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'curation.json'
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleProposeGitHubIssue = () => {
    const editKeys = Object.keys(bandEdits)
    if (editKeys.length === 0) return
    const rowsMarkdown = editKeys
      .map((key) => {
        const [stationId, channel] = key.split('.')
        const originalRow = audit.rows.find(
          (r) => r.station_id === stationId && r.channel === channel,
        )
        const edit = bandEdits[key]
        const origBand = originalRow
          ? `${originalRow.band[0] ?? '−∞'} … ${originalRow.band[1] ?? '∞'}`
          : '—'
        const newLo = edit.band_lo === '' || edit.band_lo === null ? '−∞' : edit.band_lo
        const newHi = edit.band_hi === '' || edit.band_hi === null ? '∞' : edit.band_hi
        const newBand = `${newLo} … ${newHi}`
        const note = edit.note !== undefined ? edit.note : originalRow?.note || ''
        return `| \`${stationId}\` | \`${channel}\` | ${origBand} | ${newBand} | ${note} |`
      })
      .join('\n')

    const stationsList = [...new Set(editKeys.map((k) => k.split('.')[0]))].join(', ')
    const title = encodeURIComponent(`curation: propose boundary adjustments for ${stationsList}`)
    const body = encodeURIComponent(
      `### Proposed Curation Changes\n\n` +
        `Boundary and note adjustments tuned in the Data Quality Inspector:\n\n` +
        `| Station | Channel | Current Band | Proposed Band | Note |\n` +
        `|---|---|---|---|---|\n` +
        `${rowsMarkdown}\n\n` +
        `### Rationale\n\n` +
        `*Describe why these physical limits or notes should be updated.*\n`,
    )
    window.open(
      `https://github.com/aisvn-data/solardata/issues/new?title=${title}&body=${body}`,
      '_blank',
    )
  }

  return (
    <div className="inspector">
      <div className="inspector-tabs" role="tablist">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={section === key}
            className={section === key ? 'active' : ''}
            onClick={() => setSection(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {section === 'overview' && (
        <div className="panel">
          <div className="stat-tiles">
            <Tile label="Readings" value={totals.readings.toLocaleString()} />
            <Tile label="Stations" value={totals.stations} />
            <Tile label="Raw files" value={totals.files} />
            <Tile label="Hourly buckets" value={totals.hourly_buckets.toLocaleString()} />
            <Tile label="Daily buckets" value={totals.daily_buckets.toLocaleString()} />
            <Tile label="Out of range" value={(totals.out_of_range ?? 0).toLocaleString()} />
          </div>
        </div>
      )}

      {section === 'stations' && (
        <div className="panel">
          <div className="stat-tiles">
            <Tile label="Readings" value={totals.readings.toLocaleString()} />
            <Tile label="Stations" value={totals.stations} />
            <Tile label="Raw files" value={totals.files} />
            <Tile label="Hourly buckets" value={totals.hourly_buckets.toLocaleString()} />
            <Tile label="Daily buckets" value={totals.daily_buckets.toLocaleString()} />
            <Tile label="Out of range" value={flags.out_of_range?.toLocaleString() ?? '0'} />
          </div>

          <p className="muted">
            Range <code>{first?.slice(0, 10)}</code> → <code>{last?.slice(0, 10)}</code>. Every
            number here comes from a real run over the {totals.files}-file archive, and
            the same counts are enforced by <code>python -m etl verify</code> in CI.
          </p>

          <h3>Per station</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Station</th>
                <th>Table</th>
                <th className="num">Readings</th>
                <th className="num">Channels</th>
                <th className="num">Shown</th>
                <th>Coverage (UTC)</th>
                <th className="num">Median gap</th>
              </tr>
            </thead>
            <tbody>
              {report.stations.map((s) => {
                const coverage = (report.coverage ?? []).find((c) => c.station_id === s.station_id)
                return (
                  <tr key={s.station_id}>
                    <td>
                      {s.display_name}
                      {!s.is_production && <span className="muted small"> (bench)</span>}
                    </td>
                    <td>
                      <code>{s.table}</code>
                    </td>
                    <td className="num">{(s.n_readings ?? 0).toLocaleString()}</td>
                    <td className="num">{s.channels.length}</td>
                    <td className="num">{s.published_channels.length}</td>
                    <td>
                      {s.first_ts_utc?.slice(0, 10)} → {s.last_ts_utc?.slice(0, 10)}
                    </td>
                    <td className="num">{coverage?.median_gap_seconds ?? '—'} s</td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          {report.stations.map((s) => (
            <details key={s.station_id} className="station-detail">
              <summary>
                {s.display_name} — {s.channels.length} channel
                {s.channels.length === 1 ? '' : 's'}, {s.published_channels.length} charted
              </summary>
              <p className="muted">{s.notes}</p>

              <table className="data-table">
                <thead>
                  <tr>
                    <th>Channel</th>
                    <th>Unit</th>
                    <th className="num">n</th>
                    <th className="num">min</th>
                    <th className="num">median</th>
                    <th className="num">mean</th>
                    <th className="num">max</th>
                    <th className="num">zeros</th>
                    <th>Band</th>
                    <th className="num">Out</th>
                  </tr>
                </thead>
                <tbody>
                  {s.channels.map((ch) => {
                    const o = ch.observed ?? {}
                    const band =
                      ch.band_lo === null && ch.band_hi === null
                        ? '—'
                        : `${ch.band_lo ?? '−∞'} … ${ch.band_hi ?? '∞'}`
                    return (
                      <tr key={ch.channel} className={ch.published ? '' : 'muted'}>
                        <td>
                          <code>{ch.channel}</code>
                          <div className="muted small">{ch.description}</div>
                        </td>
                        <td>
                          {ch.unit || '—'}
                          {ch.scale !== 1 && (
                            <div className="muted small">×{ch.scale} from {ch.raw_unit}</div>
                          )}
                        </td>
                        <td className="num">{(o.n_values ?? 0).toLocaleString()}</td>
                        <td className="num">{fmt(o.min)}</td>
                        <td className="num">{fmt(o.p50)}</td>
                        <td className="num">{fmt(o.mean)}</td>
                        <td className="num">{fmt(o.max)}</td>
                        <td className="num">{(o.n_zero ?? 0).toLocaleString()}</td>
                        <td>{band}</td>
                        <td className="num">{(o.n_out_of_range ?? 0).toLocaleString()}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>

              {s.hidden_channels.length > 0 && (
                <>
                  <h4>Recorded but not charted</h4>
                  <ul>
                    {s.hidden_channels.map((h) => (
                      <li key={h.channel}>
                        <code>{h.channel}</code> — <strong>{h.reason}</strong>. {h.note}
                      </li>
                    ))}
                  </ul>
                </>
              )}

              {Object.keys(s.flag_totals ?? {}).length > 0 && (
                <>
                  <h4>Flags on this station&apos;s readings</h4>
                  <p className="muted small">
                    {Object.entries(s.flag_totals)
                      .map(([flag, n]) => `${flag}: ${n.toLocaleString()}`)
                      .join(' · ')}
                  </p>
                </>
              )}

              {(s.rejects ?? []).length > 0 && (
                <p className="muted small">
                  Rejected cells:{' '}
                  {s.rejects.map((r) => `${r.reason} ${r.n.toLocaleString()}`).join(' · ')}
                </p>
              )}

              {(s.open_questions ?? []).length > 0 && (
                <>
                  <h4>Open questions</h4>
                  <ul>
                    {s.open_questions.map((q) => (
                      <li key={q}>{q}</li>
                    ))}
                  </ul>
                </>
              )}
            </details>
          ))}
        </div>
      )}

      {section === 'bands' && (
        <div className="panel">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '12px', marginBottom: '14px' }}>
            <p className="muted" style={{ margin: 0, maxWidth: '650px' }}>
              How much of each channel&apos;s own record its own band rejects. A band is
              a claim about a sensor at a site, and a flag that fires on more than{' '}
              {((audit.threshold ?? 0.01) * 100).toFixed(0)}% of a channel cannot mark a
              contaminated aggregate &mdash; it is reporting a unit mismatch. Every row
              above the threshold carries a note saying why the fire is the finding.
            </p>
            <button
              type="button"
              className={editingBands ? 'active' : ''}
              onClick={() => setEditingBands(!editingBands)}
              style={{
                padding: '6px 14px',
                borderRadius: '6px',
                border: '1px solid #94a3b8',
                background: editingBands ? '#102a43' : '#f8fafc',
                color: editingBands ? '#fff' : '#1e293b',
                fontWeight: '600',
                cursor: 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              {editingBands ? '✓ Close Tuning' : '✎ Tune Boundaries & Curation'}
            </button>
          </div>

          {Object.keys(bandEdits).length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#f0f9ff', border: '1px solid #bae6fd', borderRadius: '8px', padding: '10px 14px', marginBottom: '16px', flexWrap: 'wrap', gap: '10px' }}>
              <span style={{ fontSize: '0.85rem', fontWeight: '600', color: '#0369a1' }}>
                {Object.keys(bandEdits).length} channel{Object.keys(bandEdits).length === 1 ? '' : 's'} tuned
              </span>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button
                  type="button"
                  onClick={handleDownloadCuration}
                  style={{
                    padding: '5px 12px',
                    borderRadius: '6px',
                    border: '1px solid #0284c7',
                    background: '#0284c7',
                    color: '#fff',
                    fontWeight: '600',
                    fontSize: '0.82rem',
                    cursor: 'pointer',
                  }}
                >
                  📥 Download curation.json
                </button>
                <button
                  type="button"
                  onClick={handleProposeGitHubIssue}
                  style={{
                    padding: '5px 12px',
                    borderRadius: '6px',
                    border: '1px solid #16a34a',
                    background: '#16a34a',
                    color: '#fff',
                    fontWeight: '600',
                    fontSize: '0.82rem',
                    cursor: 'pointer',
                  }}
                >
                  🐙 Propose via GitHub Issue
                </button>
                <button
                  type="button"
                  onClick={() => setBandEdits({})}
                  style={{
                    padding: '5px 10px',
                    borderRadius: '6px',
                    border: '1px solid #cbd5e1',
                    background: '#fff',
                    color: '#64748b',
                    fontSize: '0.82rem',
                    cursor: 'pointer',
                  }}
                >
                  Reset
                </button>
              </div>
            </div>
          )}

          <table className="data-table">
            <thead>
              <tr>
                <th>Station</th>
                <th>Channel</th>
                <th>Unit</th>
                <th>Band</th>
                <th className="num">n</th>
                <th className="num">Out of range</th>
                <th className="num">Share</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {audit.rows.map((r) => {
                const key = `${r.station_id}.${r.channel}`
                const edit = bandEdits[key]
                const isModified = edit !== undefined
                const bandLo = isModified ? edit.band_lo : (r.band[0] ?? '')
                const bandHi = isModified ? edit.band_hi : (r.band[1] ?? '')
                const currentNote = isModified ? (edit.note ?? '') : (r.note ?? '')
                const needsNote = r.over_threshold && !currentNote.trim()

                return (
                  <tr
                    key={key}
                    className={(r.over_threshold && !r.justified) || needsNote ? 'band-warn' : ''}
                    style={isModified ? { background: '#f0fdf4' } : undefined}
                  >
                    <td>
                      {r.station_id}
                      {isModified && (
                        <span style={{ marginLeft: '6px', fontSize: '0.7rem', color: '#16a34a', fontWeight: 'bold' }}>
                          (modified)
                        </span>
                      )}
                    </td>
                    <td>
                      <code>{r.channel}</code>
                    </td>
                    <td>{r.unit || '—'}</td>
                    <td>
                      {editingBands ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                          <input
                            type="number"
                            step="any"
                            placeholder="−∞"
                            value={bandLo}
                            onChange={(e) => {
                              const val = e.target.value
                              setBandEdits((prev) => ({
                                ...prev,
                                [key]: {
                                  band_lo: val,
                                  band_hi: prev[key]?.band_hi ?? (r.band[1] ?? ''),
                                  note: prev[key]?.note ?? (r.note ?? ''),
                                },
                              }))
                            }}
                            style={{ width: '65px', padding: '2px 4px', fontSize: '0.8rem', border: '1px solid #cbd5e1', borderRadius: '4px' }}
                          />
                          <span>…</span>
                          <input
                            type="number"
                            step="any"
                            placeholder="∞"
                            value={bandHi}
                            onChange={(e) => {
                              const val = e.target.value
                              setBandEdits((prev) => ({
                                ...prev,
                                [key]: {
                                  band_lo: prev[key]?.band_lo ?? (r.band[0] ?? ''),
                                  band_hi: val,
                                  note: prev[key]?.note ?? (r.note ?? ''),
                                },
                              }))
                            }}
                            style={{ width: '65px', padding: '2px 4px', fontSize: '0.8rem', border: '1px solid #cbd5e1', borderRadius: '4px' }}
                          />
                        </div>
                      ) : (
                        `${bandLo !== '' ? bandLo : '−∞'} … ${bandHi !== '' ? bandHi : '∞'}`
                      )}
                    </td>
                    <td className="num">{r.n_values.toLocaleString()}</td>
                    <td className="num">{r.n_out_of_range.toLocaleString()}</td>
                    <td className="num">{(r.fraction * 100).toFixed(2)}%</td>
                    <td className="small">
                      {editingBands ? (
                        <div>
                          <input
                            type="text"
                            placeholder={r.over_threshold ? 'Reason required (>1% fires)' : 'Optional note'}
                            value={currentNote}
                            onChange={(e) => {
                              const val = e.target.value
                              setBandEdits((prev) => ({
                                ...prev,
                                [key]: {
                                  band_lo: prev[key]?.band_lo ?? (r.band[0] ?? ''),
                                  band_hi: prev[key]?.band_hi ?? (r.band[1] ?? ''),
                                  note: val,
                                },
                              }))
                            }}
                            style={{
                              width: '100%',
                              padding: '2px 6px',
                              fontSize: '0.78rem',
                              border: needsNote ? '1px solid #ef4444' : '1px solid #cbd5e1',
                              borderRadius: '4px',
                            }}
                          />
                          {needsNote && (
                            <div style={{ color: '#dc2626', fontSize: '0.7rem', marginTop: '2px' }}>
                              ⚠️ Missing note (&gt;1% fires)
                            </div>
                          )}
                        </div>
                      ) : (
                        r.note
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {audit.unjustified.length > 0 && (
            <p className="error-box">
              {audit.unjustified.length} band
              {audit.unjustified.length === 1 ? '' : 's'} fire on more than the threshold
              with no note explaining why. <code>python -m etl audit</code> fails on
              this.
            </p>
          )}
        </div>
      )}

      {section === 'normalization' && (
        <div className="panel">
          <h3>Normalization: Raw Telemetry to Standard Physical Units</h3>
          <p className="muted">
            The normalization stage converts raw, inconsistent XLSX exports into compact, queryable store
            (<code>solardata_raw.db</code>, 22.7 MB, with <code>ts INTEGER PRIMARY KEY</code> as Unix epoch seconds
            and integer channel telemetry). It applies physical hardware scale factors (e.g. mV to V, mA to A)
            and dated hardware corrections confirmed against the hardware.
          </p>

          <h4>Physical Scale Factors (Hardware Multipliers)</h4>
          <table className="data-table">
            <thead>
              <tr>
                <th>Station</th>
                <th>Channel</th>
                <th>Raw Unit</th>
                <th>Stored Unit</th>
                <th>Scale Factor</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {report.stations.flatMap((s) =>
                s.channels
                  .filter((ch) => ch.scale !== 1.0)
                  .map((ch) => (
                    <tr key={`${s.station_id}.${ch.channel}`}>
                      <td>{s.display_name} (<code>{s.station_id}</code>)</td>
                      <td><code>{ch.channel}</code></td>
                      <td>{ch.raw_unit}</td>
                      <td>{ch.unit}</td>
                      <td><strong>×{ch.scale}</strong></td>
                      <td className="small">{ch.description}</td>
                    </tr>
                  )),
              )}
            </tbody>
          </table>

          <h4>Dated Hardware Fault Corrections</h4>
          <p className="muted small">
            Hardware faults dated and declared by the collector. Applied once during ingest, after scaling.
          </p>
          <div className="window-card">
            <div className="window-head">
              <strong>AISVN #1 Current Sensor Sign/Offset Fault</strong>
              <span className="badge">aisvn.current_a</span>
            </div>
            <p className="window-why">
              From 2020-08-24 18:42:00 local: Collector hardware failure caused the current sensor to read 6.6 A too low.
              Normalized by applying <code>add 6.6</code>.
            </p>
          </div>
          <div className="window-card">
            <div className="window-head">
              <strong>AISVN #1 Power Inversion & Gain Fault</strong>
              <span className="badge">aisvn.power_w</span>
            </div>
            <p className="window-why">
              From 2020-08-24 18:42:00 local: Power channel output was inverted and 4× too large.
              Normalized by applying <code>factor -0.25</code>.
            </p>
          </div>

          <h4>Raw Telemetry Layouts</h4>
          <table className="data-table">
            <thead>
              <tr>
                <th>Station</th>
                <th>Channels</th>
                <th>Source Definition</th>
                <th>Mapped Channels</th>
              </tr>
            </thead>
            <tbody>
              {report.stations.map((s) => (
                <tr key={s.station_id}>
                  <td>{s.display_name} (<code>{s.station_id}</code>)</td>
                  <td className="num">{s.channels.length}</td>
                  <td>{s.applet ? `Applet: ${s.applet}` : 'Declared Layout'}</td>
                  <td>
                    {s.channels.map((c) => c.channel).join(', ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {section === 'flags' && (
        <div className="panel">
          <p className="muted">
            A flag describes the value that was stored. Nothing is ever dropped for
            being implausible; a value outside its band is kept and marked, and a
            placeholder is stored as a gap with the original cell in the rejects
            table.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Flag</th>
                <th className="num">Readings</th>
                <th>Meaning</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(flags).map(([flag, n]) => (
                <tr key={flag}>
                  <td>
                    <code>{flag}</code>
                  </td>
                  <td className="num">{n.toLocaleString()}</td>
                  <td className="small">{report.flag_names?.[flag] ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {section === 'windows' && (
        <div className="panel">
          <h3>Windows whose values were stored as a gap</h3>
          <p className="muted small">
            A window over which the collector says the input was not connected, so the
            number the sheet logged was a claim rather than a measurement. The values
            are NULL and every affected cell is in the rejects table.
          </p>
          <ul>
            {windows.null.map((w) => (
              <li key={`${w.station_id}-${w.valid_from}`}>
                <strong>{w.station_id}</strong> — {w.columns.join(', ')}, {w.valid_from} to{' '}
                {w.valid_to}, {w.n_rows.toLocaleString()} cells. {w.why}
              </li>
            ))}
          </ul>

          <h3>Windows whose values were kept and flagged</h3>
          <p className="muted small">
            A human has said not to believe these levels, but the samples are real, so
            they stay.
          </p>
          <ul>
            {windows.bad.map((w) => (
              <li key={`${w.station_id}-${w.valid_from}`}>
                <strong>{w.station_id}</strong> — {w.columns.join(', ')}, {w.valid_from} to{' '}
                {w.valid_to}. {w.why}
              </li>
            ))}
          </ul>

          <h3>Excluded files and rows</h3>
          <ul>
            {(report.exclusions?.files ?? []).map((f) => (
              <li key={f.path}>
                <code>{f.path}</code> — {f.why}
              </li>
            ))}
            {(report.exclusions?.rows ?? []).map((r) => (
              <li key={r.path}>
                rows before {r.from_row} of <code>{r.path}</code> — {r.why}
              </li>
            ))}
          </ul>
        </div>
      )}

      {section === 'notes' && (
        <div className="panel">
          <p className="muted">
            Prose found in a data cell, anchored to the instant and the spreadsheet
            column it came from. These are the collector&apos;s own words and the only
            thing in the archive that says what was happening at the sites.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Station</th>
                <th>When (UTC)</th>
                <th>Cell</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {notes.map((n) => (
                <tr key={`${n.station_id}-${n.ts_utc}-${n.column_name}-${n.note}`}>
                  <td>{n.station_id}</td>
                  <td>{n.ts_utc?.slice(0, 19).replace('T', ' ') ?? '—'}</td>
                  <td>{n.column_name ?? '—'}</td>
                  <td>{n.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {section === 'rejects' && (
        <div className="panel">
          <p className="muted">
            Every cell that did not become a reading, grouped by why.{' '}
            <code>reason</code> is a stable category and never a sentence: the prose
            for each window is published once, above, and repeated on 220,074 rows
            it used to cost 80.6 MiB.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Reason</th>
                <th className="num">Cells</th>
              </tr>
            </thead>
            <tbody>
              {rejects.map((r) => (
                <tr key={r.reason}>
                  <td>
                    <code>{r.reason}</code>
                  </td>
                  <td className="num">{r.n.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Per archive folder</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Folder</th>
                <th>Station</th>
                <th className="num">Files</th>
                <th className="num">With header</th>
                <th className="num">Ingested</th>
                <th className="num">Dup ts</th>
                <th>Coverage</th>
              </tr>
            </thead>
            <tbody>
              {(report.source_files ?? []).map((row) => (
                <tr key={row.source_dir}>
                  <td>
                    <code>{row.source_dir}</code>
                  </td>
                  <td>{row.station_id}</td>
                  <td className="num">{row.files}</td>
                  <td className="num">{row.with_header}</td>
                  <td className="num">{row.n_ingested.toLocaleString()}</td>
                  <td className="num">{row.n_duplicate_ts.toLocaleString()}</td>
                  <td>
                    {row.min_ts_utc?.slice(0, 10)} → {row.max_ts_utc?.slice(0, 10)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function Tile({ label, value }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
    </div>
  )
}

function fmt(value) {
  if (value === null || value === undefined) return '—'
  const abs = Math.abs(value)
  if (abs >= 1000) return value.toFixed(0)
  if (abs >= 10) return value.toFixed(1)
  return value.toFixed(2)
}

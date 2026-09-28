import { useEffect, useState } from 'react'
import { loadQuality } from '../data.js'

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
  const [error, setError] = useState(null)
  const [section, setSection] = useState('stations')

  useEffect(() => {
    let cancelled = false
    loadQuality()
      .then((data) => {
        if (!cancelled) setReport(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
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
    ['bands', `Bands (${audit.rows.length})`],
    ['flags', `Flags (${Object.keys(flags).length})`],
    ['windows', `Windows (${windows.null.length + windows.bad.length})`],
    ['notes', `Notes (${notes.length})`],
    ['rejects', `Rejected cells (${totals.rejects.toLocaleString()})`],
  ]

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
          <p className="muted">
            How much of each channel&apos;s own record its own band rejects. A band is
            a claim about a sensor at a site, and a flag that fires on more than{' '}
            {((audit.threshold ?? 0.01) * 100).toFixed(0)}% of a channel cannot mark a
            contaminated aggregate &mdash; it is reporting a unit mismatch. Every row
            above the threshold carries a note saying why the fire is the finding.
          </p>
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
              {audit.rows.map((r) => (
                <tr
                  key={`${r.station_id}.${r.channel}`}
                  className={r.over_threshold && !r.justified ? 'band-warn' : ''}
                >
                  <td>{r.station_id}</td>
                  <td>
                    <code>{r.channel}</code>
                  </td>
                  <td>{r.unit || '—'}</td>
                  <td>
                    {r.band[0] ?? '−∞'} … {r.band[1] ?? '∞'}
                  </td>
                  <td className="num">{r.n_values.toLocaleString()}</td>
                  <td className="num">{r.n_out_of_range.toLocaleString()}</td>
                  <td className="num">{(r.fraction * 100).toFixed(2)}%</td>
                  <td className="small">{r.note}</td>
                </tr>
              ))}
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

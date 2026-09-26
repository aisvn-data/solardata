import { useEffect, useState } from 'react'
import { loadQuality } from '../data.js'

/**
 * Database inspector: what the build did, and what it is unsure about.
 *
 * This is the view a maintainer needs before trusting a chart. The station
 * explorer shows values; this shows the caveats that come with them -- which
 * columns were mapped by inference, which readings were flagged, which scale
 * changes are still unconfirmed, and what the collector wrote in the margin.
 *
 * It reads `quality.json`, written by `python -m etl export` from the same
 * `etl.report.collect` call that produces the committed
 * `data/processed/quality_report.md`, so the two cannot disagree.
 */
export default function QualityInspector() {
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)
  const [section, setSection] = useState('overview')

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
          Run <code>python -m etl export</code> to write{' '}
          <code>public/data/quality.json</code>.
        </p>
      </div>
    )
  }
  if (!report) return <p className="muted">Loading quality report…</p>

  const totals = report.totals
  const flags = report.flag_totals ?? {}
  const unconfirmed = (report.regimes ?? []).filter((r) => r.status === 'unconfirmed')

  return (
    <div className="inspector">
      <div className="inspector-tabs" role="tablist">
        {[
          ['overview', 'Overview'],
          ['flags', `Flags${Object.keys(flags).length ? ` (${Object.keys(flags).length})` : ''}`],
          ['regimes', `Scale regimes (${unconfirmed.length})`],
          ['channels', 'Channel coverage'],
          ['notes', `Collector notes (${report.notes?.length ?? 0})`],
          ['rejects', `Rejected cells (${report.rejects?.total ?? 0})`],
        ].map(([key, label]) => (
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
            <Tile label="Raw files" value={report.source_files.total} />
            <Tile label="Days covered" value={totals.days.toLocaleString()} />
            <Tile
              label="Files without a header"
              value={`${report.source_files.without_header} / ${report.source_files.total}`}
            />
            <Tile label="Unconfirmed regimes" value={unconfirmed.length} />
          </div>

          <p className="muted">
            Range <code>{totals.first_ts}</code> → <code>{totals.last_ts}</code>.
            Every number on this page comes from a real run over the 364-file
            archive, and the same counts are enforced by{' '}
            <code>python -m etl verify</code> in CI.
          </p>

          <h3>Per folder</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Folder</th>
                <th>Station</th>
                <th className="num">Files</th>
                <th className="num">With header</th>
                <th className="num">Rows</th>
                <th className="num">Dup ts</th>
                <th>Range</th>
              </tr>
            </thead>
            <tbody>
              {report.source_files.per_folder.map((row) => (
                <tr key={row.source_dir}>
                  <td>
                    <code>{row.source_dir}</code>
                  </td>
                  <td>{row.station_id}</td>
                  <td className="num">{row.files}</td>
                  <td className="num">{row.with_header}</td>
                  <td className="num">{(row.rows_ingested ?? 0).toLocaleString()}</td>
                  <td className="num">{row.duplicate_ts ?? 0}</td>
                  <td className="small muted">
                    {row.first_ts?.slice(0, 10)} → {row.last_ts?.slice(0, 10)}
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
            Flags never remove a value. <code>sentinel</code> means the raw cell
            was an IFTTT missing-value marker (−992/−1) and is stored as NULL;
            <code> out_of_range</code> means the value is kept but falls outside
            the channel&apos;s plausible band, which is how calibration changes
            get noticed.
          </p>
          {Object.keys(flags).length === 0 ? (
            <p>No readings are flagged.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Flag</th>
                  <th className="num">Readings</th>
                  <th className="num">Share</th>
                  <th>Meaning</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(flags).map(([flag, count]) => (
                  <tr key={flag}>
                    <td>
                      <code>{flag}</code>
                    </td>
                    <td className="num">{count.toLocaleString()}</td>
                    <td className="num">
                      {((count / totals.readings) * 100).toFixed(1)}%
                    </td>
                    <td className="muted small">{flagMeaning(flag)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3>Channel plausibility</h3>
          <p className="muted small">
            Ranges as stored, before any scale regime is applied. A station with no
            value in a column did not have that channel — NULL, not zero.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Station</th>
                <th className="num">solar_v</th>
                <th className="num">battery_v</th>
                <th className="num">temp_c</th>
              </tr>
            </thead>
            <tbody>
              {(report.channel_plausibility ?? []).map((row) => (
                <tr key={row.station_id}>
                  <td>
                    <code>{row.station_id}</code>
                  </td>
                  <td className="num">{span(row.solar_min, row.solar_max)}</td>
                  <td className="num">{span(row.batt_min, row.batt_max)}</td>
                  <td className="num">{span(row.temp_min, row.temp_max)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {section === 'regimes' && (
        <div className="panel">
          <p className="muted">
            The collector changed sensor scaling mid-record without changing the
            column names. These are <strong>proposals</strong>: no value has been
            rescaled, and a human has to accept or reject each one.
          </p>
          {unconfirmed.length === 0 ? (
            <p>No scale regimes detected.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Station</th>
                  <th>Column</th>
                  <th>Window</th>
                  <th className="num">Scale</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {unconfirmed.map((regime, index) => (
                  <tr key={`${regime.station_id}-${regime.column}-${index}`}>
                    <td>
                      <code>{regime.station_id}</code>
                    </td>
                    <td>
                      <code>{regime.column}</code>
                    </td>
                    <td className="small">
                      {regime.valid_from?.slice(0, 10)} →{' '}
                      {regime.valid_to ? regime.valid_to.slice(0, 10) : 'open'}
                    </td>
                    <td className="num">×{regime.scale}</td>
                    <td>
                      <span className={`confidence ${regime.confidence}`}>
                        {regime.confidence}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {section === 'channels' && (
        <div className="panel">
          <p className="muted">
            How each raw column of each archive folder was mapped.{' '}
            <code>inferred</code> means the file had no header row and borrowed the
            layout from an earlier sibling — 305 of 364 files, so most mappings
            are inherited rather than read.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Station</th>
                <th>Folder</th>
                <th className="num">Col</th>
                <th>Raw header</th>
                <th>Canonical</th>
                <th>Unit</th>
                <th>Confidence</th>
                <th className="num">Files</th>
              </tr>
            </thead>
            <tbody>
              {(report.metric_defs ?? []).map((def, index) => (
                <tr key={`${def.station_id}-${def.source_dir}-${def.col_index}-${index}`}>
                  <td>
                    <code>{def.station_id}</code>
                  </td>
                  <td className="small">{def.source_dir}</td>
                  <td className="num">{def.col_index}</td>
                  <td>
                    <code>{def.raw_name || '—'}</code>
                  </td>
                  <td>
                    <code>{def.canonical_col || 'unmapped'}</code>
                  </td>
                  <td>{def.unit || '—'}</td>
                  <td>
                    <span className={`confidence ${def.confidence}`}>
                      {def.confidence}
                    </span>
                  </td>
                  <td className="num">{def.n_files}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {section === 'notes' && (
        <div className="panel">
          <p className="muted">
            Prose the collector wrote into a spare column, recovered with its
            timestamp. These are the only records of *why* the data looks the way
            it does — several of them explain a calibration change.
          </p>
          {(report.notes ?? []).length === 0 ? (
            <p>No notes recovered.</p>
          ) : (
            <ul className="note-list">
              {report.notes.map((note, index) => (
                <li key={index}>
                  <span className="muted small">
                    {note.rel_path?.split('/').pop()}
                    {note.ts_utc ? ` · ${note.ts_utc.slice(0, 10)}` : ''}
                  </span>
                  <p>{note.note}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {section === 'rejects' && (
        <div className="panel">
          <p className="muted">
            Cells that did not become readings. Duplicate timestamps are recorded
            here as well, so the absorbed copies stay individually inspectable
            rather than being only a count.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Reason</th>
                <th className="num">Count</th>
              </tr>
            </thead>
            <tbody>
              {(report.rejects?.by_reason ?? []).map((row) => (
                <tr key={row.reason}>
                  <td>
                    <code>{row.reason}</code>
                  </td>
                  <td className="num">{row.n.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>Samples</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>File</th>
                <th className="num">Row</th>
                <th>Value</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {(report.rejects?.samples ?? []).map((row, index) => (
                <tr key={index}>
                  <td className="small">{row.rel_path}</td>
                  <td className="num">{row.sheet_row}</td>
                  <td className="small">{row.raw_value}</td>
                  <td className="small muted">{row.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/**
 * What each quality flag means, and where the ones with no explanation come from.
 *
 * Two of these carry the 226,000 rows that `out_of_range` alone cannot account
 * for, and both are *parameterised by column* -- `bad_window:temp_c`,
 * `no_signal:solar2_v` -- so a flat lookup table left a quarter of all flagged
 * readings rendering as a bare dash. `flagMeaning` handles the prefix.
 *
 * The three marked "never assigned" are declared in `etl/config.py` and have a
 * detector or a constant behind them, but nothing in the pipeline calls one.
 * They are listed so their absence from the table above is visibly deliberate
 * rather than an oversight; see `AGENTS.md`'s open questions.
 */
const FLAG_MEANINGS = {
  sentinel: 'Raw cell was -992 or -1: the input was floating. Stored as NULL.',
  out_of_range:
    'Value kept, but outside the channel’s plausible band. Ringed on the chart, never removed. Usually how a scale change gets noticed.',
  'bad_window': 'A named window in etl/config.py where the reading is kept but should not be believed.',
  no_signal:
    'A named window in etl/config.py where the input was disconnected, so the stored value is a false reading. Nulled, with a rejects row carrying the reason.',
  schema_misaligned:
    'The row did not match the donor schema. Unreachable now that donor selection matches on width.',
  duplicate_ts: 'Same station, same instant: absorbed by the primary key, recorded in rejects.',
  clip: 'Repeated identical value long enough to be a rail artefact. Never assigned — the detector is not wired into the ingest.',
  non_monotonic:
    'A counter went backwards, i.e. the logger rebooted. Never assigned — no reboot detector runs.',
  free_text: 'Prose in a numeric cell. Never assigned — unmapped columns are skipped before this point.',
}

function flagMeaning(flag) {
  if (FLAG_MEANINGS[flag]) return FLAG_MEANINGS[flag]
  const [family, column] = flag.split(':')
  if (column && FLAG_MEANINGS[family]) return `${FLAG_MEANINGS[family]} Channel: ${column}.`
  return '—'
}

function span(min, max) {
  if (min === null || min === undefined) return '—'
  return `${Number(min).toFixed(2)} … ${Number(max).toFixed(2)}`
}

function Tile({ label, value }) {
  return (
    <div className="stat-tile">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  )
}

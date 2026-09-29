import { useEffect, useMemo, useState } from 'react'
import {
  initRawDatabase,
  isRawDbReady,
  loadBands,
  loadCuration,
  loadNormalization,
  loadStations,
  onRawDbReady,
} from '../data.js'

function applyCorrection(val, tsUtc, corrections) {
  if (val === null || val === undefined) return { val: null, applied: null }
  let current = val
  let appliedCorrection = null
  for (const corr of corrections ?? []) {
    if (corr.from_ts && tsUtc < corr.from_ts) continue
    if (corr.to_ts && tsUtc >= corr.to_ts) continue
    if (corr.op === 'add') {
      current += corr.value
      appliedCorrection = corr
    } else if (corr.op === 'factor') {
      current *= corr.value
      appliedCorrection = corr
    }
  }
  return {
    val: typeof current === 'number' ? Math.round(current * 1e9) / 1e9 : current,
    applied: appliedCorrection,
  }
}

export default function DiffInspector() {
  const [subTab, setSubTab] = useState('diff') // 'diff' | 'pipeline'
  const [stations, setStations] = useState([])
  const [normalization, setNormalization] = useState(null)
  const [curation, setCuration] = useState(null)
  const [bands, setBands] = useState({})
  const [rawDbReady, setRawDbReady] = useState(isRawDbReady())

  // Diff query controls
  const [stationId, setStationId] = useState('aisvn')
  const [selectedChannel, setSelectedChannel] = useState('')
  const [queryDate, setQueryDate] = useState('2020-08-25')
  const [diffRows, setDiffRows] = useState([])
  const [queryLoading, setQueryLoading] = useState(false)
  const [queryError, setQueryError] = useState(null)

  // Pipeline configuration edits
  const [normEdits, setNormEdits] = useState({})
  const [curationEdits, setCurationEdits] = useState({})

  useEffect(() => {
    let cancelled = false
    Promise.all([loadStations(), loadNormalization(), loadCuration(), loadBands()])
      .then(([stList, normData, curData, bandsData]) => {
        if (cancelled) return
        setStations(stList)
        setNormalization(normData)
        setCuration(curData)
        setBands(bandsData)
        if (stList.length > 0 && !stationId) {
          setStationId(stList[0].station_id)
        }
      })
      .catch((e) => console.error('Failed to load metadata in DiffInspector:', e))

    initRawDatabase().catch((e) => console.warn('Raw DB load error:', e))
    const unsubscribe = onRawDbReady(() => setRawDbReady(true))
    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [])

  const currentStation = stations.find((s) => s.station_id === stationId) ?? null
  const currentChannels = useMemo(
    () => (currentStation?.channels ?? []).filter((c) => c.published),
    [currentStation],
  )

  // Execute interactive raw vs curated diff query
  useEffect(() => {
    if (!rawDbReady || !stationId || !queryDate || !normalization) return

    let cancelled = false
    setQueryLoading(true)
    setQueryError(null)

    const runQuery = async () => {
      try {
        const db = await initRawDatabase()
        const tableName = `r_${stationId.replace(/-/g, '_')}`
        const minTs = Math.floor(Date.parse(`${queryDate}T00:00:00Z`) / 1000)
        const maxTs = Math.floor(Date.parse(`${queryDate}T23:59:59.999Z`) / 1000)

        const res = db.exec(
          `SELECT * FROM ${tableName} WHERE ts >= ${minTs} AND ts <= ${maxTs} ORDER BY ts ASC LIMIT 600`,
        )

        if (cancelled) return
        if (!res || res.length === 0) {
          setDiffRows([])
          setQueryLoading(false)
          return
        }

        const { columns, values } = res[0]
        const tsIndex = columns.indexOf('ts')
        const stationNorm = normalization?.stations?.[stationId] ?? {}
        const stationCur = curation?.stations?.[stationId] ?? {}

        const parsed = []
        for (const rowArr of values) {
          const ts = rowArr[tsIndex]
          const instant = new Date(ts * 1000).toISOString()
          const stamp = instant.replace('.000Z', 'Z')
          const timeUtc = stamp.slice(11, 16)
          const localDate = new Date(ts * 1000 + 7 * 3600 * 1000)
          const timeLocal = localDate.toISOString().slice(11, 16)

          const channelsToInspect = selectedChannel
            ? currentChannels.filter((c) => c.channel === selectedChannel)
            : currentChannels

          for (const ch of channelsToInspect) {
            const colIdx = columns.indexOf(ch.channel)
            if (colIdx === -1) continue

            const rawVal = rowArr[colIdx]
            const chNorm = stationNorm.channels?.[ch.channel] ?? {}
            const chCur = stationCur.channels?.[ch.channel] ?? {}

            const rawUnit = chNorm.raw_unit || ch.unit
            const scale = chNorm.scale ?? 1.0
            const scaledVal =
              rawVal !== null && rawVal !== undefined
                ? scale !== 1.0
                  ? Math.round(rawVal * scale * 1e9) / 1e9
                  : rawVal
                : null

            const { val: correctedVal, applied: appliedCorr } = applyCorrection(
              scaledVal,
              stamp,
              chNorm.corrections,
            )

            // Check bands
            const bandKey = `${stationId}.${ch.channel}`
            const band = bands[bandKey]?.band
            let breach = null
            if (correctedVal !== null && band) {
              const [lo, hi] = band
              if (lo !== null && correctedVal < lo) breach = { side: 'floor', bound: lo }
              else if (hi !== null && correctedVal > hi) breach = { side: 'ceiling', bound: hi }
            }

            parsed.push({
              ts,
              timeUtc,
              timeLocal,
              channel: ch.channel,
              label: ch.label,
              unit: ch.unit,
              rawUnit,
              rawVal,
              scale,
              scaledVal,
              appliedCorr,
              curatedVal: correctedVal,
              breach,
              band,
              hasTransform:
                scale !== 1.0 || appliedCorr !== null || breach !== null,
            })
          }
        }

        setDiffRows(parsed)
      } catch (err) {
        if (!cancelled) setQueryError(err.message)
      } finally {
        if (!cancelled) setQueryLoading(false)
      }
    }

    runQuery()
    return () => {
      cancelled = true
    }
  }, [rawDbReady, stationId, queryDate, selectedChannel, normalization, curation, bands, currentChannels])

  // Count changes in pipeline configuration editor
  const normDiffCount = Object.keys(normEdits).length
  const curDiffCount = Object.keys(curationEdits).length
  const totalEditsCount = normDiffCount + curDiffCount

  const handleDownloadNorm = () => {
    if (!normalization) return
    const updated = JSON.parse(JSON.stringify(normalization))
    for (const [key, edit] of Object.entries(normEdits)) {
      const [stId, chName, field] = key.split('.')
      if (updated.stations[stId]?.channels[chName]) {
        updated.stations[stId].channels[chName][field] = edit
      }
    }
    const blob = new Blob([JSON.stringify(updated, null, 2) + '\n'], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'normalization.json'
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleDownloadCuration = () => {
    if (!curation) return
    const updated = JSON.parse(JSON.stringify(curation))
    for (const [key, edit] of Object.entries(curationEdits)) {
      const [stId, chName, field] = key.split('.')
      if (updated.stations[stId]?.channels[chName]) {
        updated.stations[stId].channels[chName][field] = edit
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

  return (
    <div className="diff-inspector">
      <div className="diff-subnav">
        <button
          type="button"
          className={subTab === 'diff' ? 'active' : ''}
          onClick={() => setSubTab('diff')}
        >
          Raw vs Curated Diff
        </button>
        <button
          type="button"
          className={subTab === 'pipeline' ? 'active' : ''}
          onClick={() => setSubTab('pipeline')}
        >
          Pipeline Steps & Configuration Editor
          {totalEditsCount > 0 && <span className="diff-pill">{totalEditsCount}</span>}
        </button>
      </div>

      {subTab === 'diff' ? (
        <section className="diff-section">
          <div className="diff-controls-panel">
            <div className="control-row">
              <label className="control">
                <span>Station</span>
                <select value={stationId} onChange={(e) => setStationId(e.target.value)}>
                  {stations.map((s) => (
                    <option key={s.station_id} value={s.station_id}>
                      {s.display_name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="control">
                <span>Date (UTC)</span>
                <input
                  type="date"
                  value={queryDate}
                  onChange={(e) => setQueryDate(e.target.value)}
                />
              </label>

              <label className="control">
                <span>Channel Filter</span>
                <select
                  value={selectedChannel}
                  onChange={(e) => setSelectedChannel(e.target.value)}
                >
                  <option value="">All Published Channels</option>
                  {currentChannels.map((c) => (
                    <option key={c.channel} value={c.channel}>
                      {c.label} ({c.channel})
                    </option>
                  ))}
                </select>
              </label>

              <div className="control">
                <span>Raw SQLite Engine</span>
                <div className="status-indicator">
                  <span className={`status-dot ${rawDbReady ? 'ready' : 'loading'}`} />
                  {rawDbReady ? 'Connected (solardata_raw.db)' : 'Loading Wasm SQLite…'}
                </div>
              </div>
            </div>
          </div>

          {queryLoading && <p className="muted">Querying solardata_raw.db…</p>}
          {queryError && (
            <div className="error-box">
              <p>Query error: {queryError}</p>
            </div>
          )}

          {!queryLoading && diffRows.length === 0 && (
            <div className="empty-box">
              <p>No raw readings found for {stationId} on {queryDate}. Try selecting another date within the station's recording period.</p>
            </div>
          )}

          {diffRows.length > 0 && (
            <div className="diff-table-container">
              <table className="diff-table">
                <thead>
                  <tr>
                    <th>Time (UTC / Local)</th>
                    <th>Channel</th>
                    <th>1. Raw Value (solardata_raw.db)</th>
                    <th>2. Normalization Scale</th>
                    <th>3. Hardware Correction</th>
                    <th>4. Curated Value</th>
                    <th>Plausibility Band</th>
                  </tr>
                </thead>
                <tbody>
                  {diffRows.map((r, idx) => (
                    <tr
                      key={`${r.ts}-${r.channel}-${idx}`}
                      className={r.hasTransform ? 'row-transformed' : ''}
                    >
                      <td className="mono">
                        {r.timeUtc} <span className="muted">({r.timeLocal})</span>
                      </td>
                      <td>
                        <strong>{r.label}</strong> <span className="muted small">{r.channel}</span>
                      </td>
                      <td className="mono">
                        {r.rawVal !== null ? `${r.rawVal} ${r.rawUnit || ''}` : <span className="muted">NULL</span>}
                      </td>
                      <td>
                        {r.scale !== 1.0 ? (
                          <span className="step-badge scale">
                            &times; {r.scale} &rarr; {r.scaledVal} {r.unit}
                          </span>
                        ) : (
                          <span className="muted">&times; 1.0</span>
                        )}
                      </td>
                      <td>
                        {r.appliedCorr ? (
                          <span className="step-badge correction" title={r.appliedCorr.note}>
                            {r.appliedCorr.op === 'add'
                              ? `+ ${r.appliedCorr.value}`
                              : `&times; ${r.appliedCorr.value}`}{' '}
                            &rarr; {r.curatedVal} {r.unit}
                          </span>
                        ) : (
                          <span className="muted">none</span>
                        )}
                      </td>
                      <td className="mono curated-val">
                        <strong>
                          {r.curatedVal !== null ? `${r.curatedVal} ${r.unit}` : <span className="muted">NULL</span>}
                        </strong>
                      </td>
                      <td>
                        {r.breach ? (
                          <span className="step-badge breach">
                            {r.breach.side === 'floor' ? 'Below min' : 'Above max'} ({r.breach.bound} {r.unit})
                          </span>
                        ) : r.band ? (
                          <span className="badge in-band">
                            [{r.band[0] ?? '-\u221E'}, {r.band[1] ?? '+\u221E'}] {r.unit}
                          </span>
                        ) : (
                          <span className="muted">unbanded</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : (
        <section className="pipeline-section">
          <div className="pipeline-header">
            <div>
              <h3>Channel Transformation Pipeline Rules</h3>
              <p className="muted">
                Inspect physical measurement declarations from <code>normalization.json</code> and
                analytical curation rules from <code>curation.json</code>. You can test updates
                and download the updated configuration files.
              </p>
            </div>
            <div className="pipeline-actions">
              <button
                type="button"
                className="button-secondary"
                disabled={totalEditsCount === 0}
                onClick={() => {
                  setNormEdits({})
                  setCurationEdits({})
                }}
              >
                Reset All Edits
              </button>
              <button
                type="button"
                className="button"
                onClick={handleDownloadNorm}
              >
                Export normalization.json {normDiffCount > 0 && `(${normDiffCount})`}
              </button>
              <button
                type="button"
                className="button"
                onClick={handleDownloadCuration}
              >
                Export curation.json {curDiffCount > 0 && `(${curDiffCount})`}
              </button>
            </div>
          </div>

          <div className="station-rule-cards">
            {stations.map((s) => {
              const stNorm = normalization?.stations?.[s.station_id] ?? {}
              const stCur = curation?.stations?.[s.station_id] ?? {}
              return (
                <div key={s.station_id} className="rule-card">
                  <div className="rule-card-header">
                    <h4>{s.display_name}</h4>
                    <span className="muted small">{s.location} &bull; {s.channels.length} channels</span>
                  </div>
                  <div className="rule-channels-list">
                    {s.channels.map((ch) => {
                      const chNorm = stNorm.channels?.[ch.channel] ?? {}
                      const chCur = stCur.channels?.[ch.channel] ?? {}
                      const scaleKey = `${s.station_id}.${ch.channel}.scale`
                      const currentScale = normEdits[scaleKey] ?? chNorm.scale ?? 1.0

                      const bandKey = `${s.station_id}.${ch.channel}.band`
                      const currentBand = curationEdits[bandKey] ?? chCur.band ?? ch.band ?? [null, null]

                      return (
                        <div key={ch.channel} className="rule-channel-item">
                          <div className="rule-col-meta">
                            <strong>{ch.label}</strong>
                            <code>{ch.channel}</code>
                            <span className="badge-unit">{ch.unit || 'unitless'}</span>
                          </div>

                          <div className="rule-col-norm">
                            <label className="inline-label">
                              <span>Scale Factor:</span>
                              <input
                                type="number"
                                step="any"
                                value={currentScale}
                                onChange={(e) => {
                                  const val = parseFloat(e.target.value) || 1.0
                                  setNormEdits({ ...normEdits, [scaleKey]: val })
                                }}
                              />
                            </label>
                            {chNorm.corrections?.length > 0 && (
                              <div className="rule-correction-info">
                                <span className="small muted">
                                  {chNorm.corrections.length} hardware correction(s) active
                                </span>
                              </div>
                            )}
                          </div>

                          <div className="rule-col-cur">
                            <label className="inline-label">
                              <span>Plausibility Band:</span>
                              <div className="band-inputs">
                                <input
                                  type="number"
                                  placeholder="min"
                                  value={currentBand[0] ?? ''}
                                  onChange={(e) => {
                                    const lo = e.target.value === '' ? null : parseFloat(e.target.value)
                                    setCurationEdits({
                                      ...curationEdits,
                                      [bandKey]: [lo, currentBand[1]],
                                    })
                                  }}
                                />
                                &ndash;
                                <input
                                  type="number"
                                  placeholder="max"
                                  value={currentBand[1] ?? ''}
                                  onChange={(e) => {
                                    const hi = e.target.value === '' ? null : parseFloat(e.target.value)
                                    setCurationEdits({
                                      ...curationEdits,
                                      [bandKey]: [currentBand[0], hi],
                                    })
                                  }}
                                />
                              </div>
                            </label>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}

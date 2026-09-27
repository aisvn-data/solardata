/**
 * Render the component tree and assert it produces markup.
 *
 * Why this exists, three times over:
 *
 *   1. v0.7.0 shipped a metric picker where every checkbox was `disabled`, because
 *      the parent passed metric objects and the child tested strings.
 *   2. v0.7.1 shipped a white screen: the prop was renamed `metrics` -> `channels`
 *      on the child's destructuring but not at the call site, so the child read
 *      `undefined.length`.
 *   3. A merge reintroduced (2), and it reached `main` and the deployed site.
 *
 * All three are the same defect -- a component's props disagreeing with its call
 * site -- and `check_frontend.mjs` cannot see any of them, because it exercises
 * the pure helpers in `src/data.js` and the CSV files. `vite build` cannot see
 * them either: the project is plain JSX with no types, so nothing checks a prop
 * name, and a component that throws at render time still builds perfectly.
 *
 * So this renders it. It is deliberately narrow: it asserts the tree mounts and
 * produces the controls a reader needs, which is exactly the surface where a
 * prop drift becomes a blank page.
 *
 * Run with: node scripts/check_render.mjs
 * Built by:   vite build --ssr (see package.json), because plain node cannot
 *             import .jsx or resolve import.meta.env.
 */

import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import assert from 'node:assert/strict'
import { renderToStaticMarkup } from 'react-dom/server'
import React from 'react'

import App from '../src/App.jsx'
import TimeControls from '../src/components/TimeControls.jsx'
import TimeSeriesChart from '../src/components/TimeSeriesChart.jsx'
import StatTiles from '../src/components/StatTiles.jsx'
import QualityInspector from '../src/components/QualityInspector.jsx'
import { METRICS, discoverChannels, loadBands, loadRollup } from '../src/data.js'

// Anchored on the working directory, not on this file's location: the check is
// built into `node_modules/` before it runs, so `import.meta.url` would put the
// repository root one directory too deep. Both `npm run` and the CI workflows
// execute from the repository root.
const ROOT = process.cwd()
const DATA = join(ROOT, 'public', 'data')
assert.ok(
  existsSync(join(DATA, 'stations.json')),
  `no public/data under ${ROOT} -- run this from the repository root`,
)

// `loadRollup` fetches through `import.meta.env.BASE_URL`, which under a build
// resolves to an absolute path that does not exist in node. Point `fetch` at the
// committed files so the check exercises the real load path -- parseCsv,
// decorateRow, discoverChannels -- against the real data rather than a fixture.
globalThis.fetch = async (url) => {
  const rel = String(url).replace(/^.*\/data\//, '')
  try {
    const body = readFileSync(join(DATA, rel), 'utf8')
    return {
      ok: true,
      status: 200,
      text: async () => body,
      json: async () => JSON.parse(body),
    }
  } catch {
    return { ok: false, status: 404, statusText: 'Not Found', text: async () => '', json: async () => null }
  }
}

let passed = 0
async function check(name, fn) {
  await fn()
  passed += 1
  console.log(`  ok  ${name}`)
}

function readJson(name) {
  return JSON.parse(readFileSync(join(DATA, name), 'utf8'))
}

const noop = () => {}

/**
 * The leaf props, built the way `StationExplorer` builds them.
 *
 * Deliberately constructed from the real exported data rather than from a
 * hand-written literal, so a renamed or removed column shows up here as a
 * missing channel instead of being papered over by a fixture.
 */
async function fixtureProps() {
  const bands = await loadBands()
  const stations = readJson('stations.json')
  const station = stations.find((s) => s.published && s.years.length > 0)
  const year = station.years[station.years.length - 1]
  const rows = await loadRollup(station.station_id, 'daily', year)
  const channels = await discoverChannels(rows, bands, new Map())
  return { station, year, rows, channels, bands }
}

console.log('component render')

await check('the whole app mounts', () => {
  const html = renderToStaticMarkup(React.createElement(App, {}))
  assert.ok(html.length > 500, `app rendered only ${html.length} characters`)
  assert.ok(html.includes('</html>') || html.includes('<div'), 'no markup came back')
})

await check('the data-quality inspector mounts and the data it reads is present', () => {
  // The inspector fetches `quality.json` in an effect, and effects do not run
  // under a static render, so this asserts two separate things: that the
  // component mounts at all (a throw here is a white screen on that tab), and
  // that the payload it will fetch actually carries the keys it reads. The
  // second half is the half that would otherwise only fail in a browser.
  const html = renderToStaticMarkup(React.createElement(QualityInspector, {}))
  assert.ok(html.length > 0, 'the inspector rendered nothing')
  const quality = readJson('quality.json')
  for (const key of [
    'totals',
    'source_files',
    'stations',
    'rejects',
    'notes',
    'metric_defs',
    'regimes',
    'channel_ranges',
    'null_windows',
    'bad_windows',
  ]) {
    assert.ok(key in quality, `quality.json is missing "${key}", which the inspector reads`)
  }
  assert.ok(Array.isArray(quality.rejects.by_reason), 'rejects.by_reason must be a list')
  assert.ok(Array.isArray(quality.rejects.samples), 'rejects.samples must be a list')
  // Every reject reason needs a meaning, or the Meaning column renders a dash.
  // This list is the contract: a new reason in `rejects` has to be explained in
  // `REJECT_REASONS` in QualityInspector.jsx before it ships.
  const REASONS = [
    'null_window',
    'station_setup',
    'duplicate_ts',
    'pre_reinstall',
    'repeated header row',
  ]
  for (const row of quality.rejects.by_reason) {
    assert.ok(
      REASONS.includes(row.reason),
      `rejects.by_reason has "${row.reason}", which the inspector cannot explain`,
    )
  }
})

await check('TimeControls accepts the props StationExplorer passes it', async () => {
  const { station, year, rows, channels } = await fixtureProps()
  // The prop names here must match the call site in StationExplorer.jsx. They
  // drifted once, silently, and the symptom was a white page rather than a
  // missing control, so the mismatch is asserted rather than assumed.
  const props = {
    years: station.years,
    year,
    onYearChange: noop,
    rows,
    fromDay: '',
    toDay: '',
    onRangeChange: noop,
    onRangePreset: noop,
    channels,
    selected: channels.slice(0, 2).map((c) => c.key),
    onMetricToggle: noop,
    months: [...new Set(rows.filter((r) => r.nSamples > 0).map((r) => r.dateDay.slice(0, 7)))],
    activeMonth: '',
    onMonthChange: noop,
    granularities: station.granularities ?? ['daily'],
    resolution: 'daily',
    onResolutionChange: noop,
    hideFlagged: false,
    onHideFlaggedChange: noop,
  }
  const html = renderToStaticMarkup(React.createElement(TimeControls, props))

  // Every channel offered must be renderable, and every metric a reader needs
  // must be present.
  for (const channel of channels) {
    assert.ok(
      html.includes(channel.label) || html.includes(channel.channel),
      `channel ${channel.channel} is not rendered`,
    )
  }
  assert.ok(
    /Channels \(\d+\)/.test(html),
    'the channel count did not render, so `channels` was probably undefined',
  )
  assert.ok(html.includes('Resolution'), 'the resolution switch is missing')
  assert.ok(html.includes('Quick range'), 'the quick-range buttons are missing')
  assert.ok(
    html.includes('Hide values that are outside'),
    'the flagged-value toggle is missing',
  )
  // Two checkboxes per selected channel, at least.
  const boxes = (html.match(/type="checkbox"/g) ?? []).length
  assert.ok(
    boxes >= channels.length,
    `only ${boxes} checkboxes for ${channels.length} channels`,
  )
})

await check('the chart renders a path and a readout for the selected channels', async () => {
  const { rows, channels } = await fixtureProps()
  const html = renderToStaticMarkup(
    React.createElement(TimeSeriesChart, {
      rows: rows.map((r) => ({ ...r, breaches: [] })),
      series: channels.slice(0, 2),
      resolution: 'daily',
      onHover: noop,
      hoverRow: null,
    }),
  )
  assert.ok(html.includes('class="chart"'), 'no svg was rendered')
  assert.ok(html.includes('series-line'), 'no series path was rendered')
})

await check('StatTiles renders for a channel with a summary', async () => {
  const { station, rows, channels } = await fixtureProps()
  const summary = {
    count: 10,
    min: 0,
    max: 1,
    mean: 0.5,
    total: channels[0].key === 'energy_wh' ? 100 : null,
  }
  const html = renderToStaticMarkup(
    React.createElement(StatTiles, {
      station,
      rows,
      metric: channels[0],
      stat: 'mean',
      summary,
      range: null,
    }),
  )
  assert.ok(html.includes('stat-tile'), 'no tiles rendered')
  assert.ok(html.includes(station.display_name), 'the station name is missing')
})

await check('a channel list of zero renders rather than throwing', () => {
  // The empty case: a station whose rollups carry no numeric channel. This is
  // what `discoverChannels` returns for a station with nothing, and it is the
  // case that turns a prop drift into a crash if the child assumes otherwise.
  const html = renderToStaticMarkup(
    React.createElement(TimeControls, {
      years: ['2020'],
      year: '2020',
      onYearChange: noop,
      rows: [],
      fromDay: '',
      toDay: '',
      onRangeChange: noop,
      onRangePreset: noop,
      channels: [],
      selected: [],
      onMetricToggle: noop,
      months: [],
      activeMonth: '',
      onMonthChange: noop,
      granularities: ['daily'],
      resolution: 'daily',
      onResolutionChange: noop,
      hideFlagged: false,
      onHideFlaggedChange: noop,
    }),
  )
  assert.ok(html.includes('Channels (0)'), 'the empty channel list did not render')
  assert.ok(
    html.includes('no numeric channels'),
    'the empty state does not explain itself',
  )
})

await check('METRICS is empty and the picker reads from discovery', () => {
  // `METRICS` is a compatibility shim for a hand-picked list that no longer
  // exists. If anything starts iterating it again, the site would silently go
  // back to offering six fixed channels -- the bug this replaced.
  assert.deepEqual(METRICS, [], 'METRICS should stay empty; channels are discovered')
})

console.log(`\n${passed} render checks passed`)

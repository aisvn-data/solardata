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
import {
  DEFAULT_VIEW,
  defaultSelection,
  monthBounds,
  openingView,
  rangeForLoad,
} from '../src/components/StationExplorer.jsx'
import { METRICS, discoverChannels, filterByRange, loadBands, loadRollup } from '../src/data.js'

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
  const channels = await discoverChannels(rows, bands, new Map(), station.channel_units)
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

await check('the view the site opens on exists, and opens on a chart', async () => {
  // `StationExplorer` picks its opening station, year, month, resolution and
  // channels from DEFAULT_VIEW, and every one of those is a claim about the
  // archive: a name that no longer resolves, a year the station did not report, a
  // month it did not log, a resolution it has no rollup for, or a channel the
  // exporter dropped. The component degrades rather than throws in every one of
  // those cases -- which is right for the reader and the wrong way to find out,
  // because the symptom is a default the reader never asked for and cannot
  // account for.
  //
  // So the opening state is resolved here, against the real `stations.json` and
  // the real rollup, exactly as the component's effects would. Effects do not run
  // under a static render, so this drives the two exported helpers plus the same
  // `loadRollup` -> `discoverChannels` path the component uses.
  const stations = readJson('stations.json')
  const opening = openingView(stations)
  assert.ok(opening, 'no station to open on')
  const station = stations.find((s) => s.station_id === opening.stationId)
  assert.ok(
    station.years.includes(opening.year),
    `the opening view is ${opening.stationId} ${opening.year}, which was not reported`,
  )
  assert.ok(
    station.granularities.includes(DEFAULT_VIEW.resolution),
    `${station.station_id} has no ${DEFAULT_VIEW.resolution} rollup to open on`,
  )
  assert.equal(opening.stationId, DEFAULT_VIEW.station, 'the opening station changed')
  assert.equal(opening.year, DEFAULT_VIEW.year, 'the opening year changed')

  const bands = await loadBands()
  const rows = await loadRollup(opening.stationId, DEFAULT_VIEW.resolution, opening.year)
  const channels = await discoverChannels(rows, bands, new Map(), station.channel_units)
  const available = channels.map((c) => c.key)

  // Every named channel survived discovery, so the opening chart really is the
  // battery/solar/wind trio rather than a fallback selection.
  assert.deepEqual(
    defaultSelection(available, opening.stationId, opening.year, DEFAULT_VIEW.resolution),
    DEFAULT_VIEW.channels,
    `the opening channels are not in the ${opening.stationId} ${opening.year} rollup`,
  )

  // And the named month is a month with data in it, bounded the way the component
  // bounds it: the first and last day of the month that carry samples.
  // `monthBounds` is that rule, and `activeMonth` is what makes the month control
  // read "November 2021" rather than "All" beside a From/To that says November, so
  // both halves are asserted.
  const bounds = monthBounds(rows, DEFAULT_VIEW.month)
  assert.ok(bounds, `the opening month ${DEFAULT_VIEW.month} is empty`)
  const from = bounds.from
  const to = bounds.to
  assert.equal(from.slice(0, 7), DEFAULT_VIEW.month, 'the opening range starts outside its month')
  assert.equal(to.slice(0, 7), DEFAULT_VIEW.month, 'the opening range ends outside its month')
  assert.equal(
    from,
    rows.find((r) => r.nSamples > 0 && r.dateDay.slice(0, 7) === DEFAULT_VIEW.month).dateDay,
    'the opening range does not start on the first day the station reported',
  )
  const months = [...new Set(rows.filter((r) => r.nSamples > 0).map((r) => r.dateDay.slice(0, 7)))]
  assert.ok(
    months.includes(DEFAULT_VIEW.month),
    `${DEFAULT_VIEW.month} is not offered by the month control`,
  )
  // `filterByRange` is what the chart receives, so an empty result would be a
  // chart that renders an axis and no line, and a bucket with no samples would
  // break the line in two.
  const inRange = filterByRange(rows, from, to)
  assert.ok(inRange.length > 0, `the opening range ${from}..${to} is empty`)
  assert.equal(
    inRange.filter((r) => (r.nSamples ?? 0) === 0).length,
    0,
    'the opening range contains a bucket with no readings, so the chart opens on a gap',
  )
  for (const key of DEFAULT_VIEW.channels) {
    assert.ok(
      inRange.some((r) => r.values[key] !== null),
      `the opening range has no ${key} value to draw`,
    )
  }

  // The one-shot default is not the same as a default the reader cannot escape:
  // for any other view the selection is the old rule, so a station without these
  // channels still gets a chart.
  const other = stations.find(
    (s) => s.station_id !== DEFAULT_VIEW.station && s.years.length > 0,
  )
  const otherYear = other.years[0]
  const otherRows = await loadRollup(other.station_id, 'daily', otherYear)
  const otherKeys = (
    await discoverChannels(otherRows, bands, new Map(), other.channel_units)
  ).map((c) => c.key)
  const picked = defaultSelection(otherKeys, other.station_id, otherYear, 'daily')
  assert.ok(picked.length > 0, `${other.station_id} would open on an empty chart`)
  for (const key of picked) {
    assert.ok(otherKeys.includes(key), `${key} is not a channel of ${other.station_id}`)
  }
})

await check('every station with data is listed, production or not', () => {
  // The site showed six of the eight stations the project documents until 0.7.2,
  // because the picker filtered on `published` and the two bench stations were not
  // published. Their readings were in the database, in the Parquet export and in
  // the quality report the whole time, so the omission was a hole in the one place
  // a reader looks rather than a decision about what counts as solar.
  //
  // `stations.json` marks them `published: false` -- they are not production, and
  // the site groups them separately -- so the check is that every station in the
  // manifest is reachable, and that the two are still flagged as what they are.
  const stations = readJson('stations.json')
  const listed = stations.filter((s) => s.years.length > 0)
  assert.equal(
    listed.length,
    stations.length,
    `${stations.length - listed.length} station(s) have readings but no published rollup`,
  )
  for (const station of listed) {
    for (const folder of station.granularities) {
      for (const year of station.years) {
        assert.ok(
          existsSync(join(DATA, station.station_id, folder, `${year}.csv`)),
          `${station.station_id} ${folder} ${year} is listed but not on disk`,
        )
      }
    }
  }
  // Still marked, so the site's "Not solar production" group cannot silently
  // become the main one.
  const bench = stations.filter((s) => !s.published).map((s) => s.station_id)
  assert.ok(bench.includes('test'), bench.join(','))
  assert.ok(bench.includes('voltage-phumy'), bench.join(','))
})

await check("a station that stores a channel in another unit is drawn in that unit", async () => {
  // `test` records `temp_c` in hundredths of a degree; every other station uses
  // tenths, and the rollup column is the same `temp_deci_c_avg` either way. The
  // unit and the band travel with the station in `stations.json`, from the
  // pipeline's own `CHANNEL_UNITS` table, and `displayDivisor` reads the
  // coefficient off the unit string. Miss any of the three and the site prints
  // 280 degC for a 28 degC afternoon -- a plausible-looking wrong number, which
  // is the one failure this project is built to avoid. It was invisible while the
  // station was unpublished, so it is asserted now that it is published.
  const stations = readJson('stations.json')
  const test = stations.find((s) => s.station_id === 'test')
  assert.deepEqual(
    test.channel_units,
    { temp_c: { unit: '0.01 degC', lo: 2149, hi: 3131 } },
    `test's per-station unit is ${JSON.stringify(test.channel_units)}`,
  )
  // A station with no override carries an empty object, so the lookup is a
  // miss rather than an undefined property.
  for (const station of stations.filter((s) => s.station_id !== 'test')) {
    assert.deepEqual(
      station.channel_units,
      {},
      `${station.station_id} has a unit override it should not have`,
    )
  }

  const bands = await loadBands()
  const rows = await loadRollup('test', 'daily', test.years[0])
  const channels = await discoverChannels(rows, bands, new Map(), test.channel_units)
  const temp = channels.find((c) => c.key === 'temp_c')
  assert.ok(temp, 'test has no temp_c channel to draw')
  assert.equal(temp.divisor, 100, 'test.temp_c is not divided by 100 for display')
  assert.equal(temp.band.lo, 2149, 'test.temp_c is banded in tenths, not hundredths')
  assert.equal(temp.band.hi, 3131, 'test.temp_c is banded in tenths, not hundredths')
  // And the values land where a reader would recognise them: the probe logged
  // 24.72 to 30.09 degC, so the means must be in the twenties or thirties.
  const shown = rows.map((r) => r.values.temp_c / temp.divisor).filter((v) => v !== null)
  assert.ok(shown.length > 0, 'no test temperature to check')
  const low = Math.min(...shown)
  const high = Math.max(...shown)
  assert.ok(
    low > 0 && high < 60,
    `test temperatures draw as ${low.toFixed(1)}..${high.toFixed(1)} degC, which is not a probe in Ho Chi City`,
  )
})

await check('a resolution switch keeps the range the reader set', () => {
  // The bug: the period was keyed on station, year *and resolution*, so pressing
  // Hour cleared From and To and the chart jumped from November back to the whole
  // year. The reader picked a period; the resolution is how finely to draw it.
  //
  // `rangeForLoad` is pure and takes the resolution as an argument it ignores, so
  // the contract is askable: the same period at two resolutions must give the same
  // answer. With the period key built by the caller, a test could not see this --
  // the function passed every assertion while the component threw the range away.
  const opening = { from: '2021-11-01', to: '2021-11-30' }
  const at = (resolution, overrides = {}) =>
    rangeForLoad({
      stationId: DEFAULT_VIEW.station,
      year: DEFAULT_VIEW.year,
      resolution,
      previous: `${DEFAULT_VIEW.station}:${DEFAULT_VIEW.year}`,
      opening,
      openingApplied: true,
      ...overrides,
    })

  // The case that shipped: a month the reader chose, then a resolution switch.
  for (const resolution of ['daily', 'hourly']) {
    assert.equal(
      at(resolution).changed,
      false,
      `switching to ${resolution} cleared the range`,
    )
  }
  // The two resolutions agree, which is the assertion the bug would have failed.
  assert.deepEqual(at('daily'), at('hourly'), 'the two resolutions disagree about the range')

  // The first load of the opening view applies the default.
  assert.deepEqual(
    at(DEFAULT_VIEW.resolution, { previous: null, openingApplied: false }),
    { changed: true, from: '2021-11-01', to: '2021-11-30', opening: true },
    'the opening view does not get its range',
  )
  // ... and only once. The loader runs a second time for the same period when the
  // station's observed ranges arrive, and that second run must not clear it.
  assert.equal(
    at(DEFAULT_VIEW.resolution, { openingApplied: true }).changed,
    false,
    'the loader cleared the opening range on the second run of the effect',
  )

  // A different year does reset, because the days in 2021 have nothing to do with
  // 2022, and it does not re-apply the default even on the way back to the
  // opening view: a default that reasserts itself is a control that undoes itself.
  const nextYear = rangeForLoad({
    stationId: DEFAULT_VIEW.station,
    year: '2022',
    resolution: 'daily',
    previous: `${DEFAULT_VIEW.station}:2021`,
    opening,
    openingApplied: true,
  })
  assert.deepEqual(nextYear, { changed: true, from: '', to: '', opening: false })
  const backAgain = rangeForLoad({
    stationId: DEFAULT_VIEW.station,
    year: DEFAULT_VIEW.year,
    resolution: 'hourly',
    previous: `${DEFAULT_VIEW.station}:2022`,
    opening,
    openingApplied: true,
  })
  assert.deepEqual(backAgain, { changed: true, from: '', to: '', opening: false })
})

await check('a month is bounded by the days the station reported', () => {
  const rows = [
    { dateDay: '2021-10-31', nSamples: 12 },
    // November starts late for this station, and the 4th has no samples at all.
    { dateDay: '2021-11-02', nSamples: 30 },
    { dateDay: '2021-11-03', nSamples: 0 },
    { dateDay: '2021-11-04', nSamples: 30 },
    { dateDay: '2021-11-28', nSamples: 7 },
    { dateDay: '2021-11-30', nSamples: 0 },
    { dateDay: '2021-12-01', nSamples: 30 },
  ]
  // The calendar would say 11-01 to 11-30; the data says 11-02 to 11-28, and a
  // range padded with the two empty days is a line broken in three places.
  assert.deepEqual(monthBounds(rows, '2021-11'), { from: '2021-11-02', to: '2021-11-28' })
  // A month with nothing in it has no bounds rather than a range that draws
  // nothing, and a month of nothing but empty days is the same case.
  assert.equal(monthBounds(rows, '2021-09'), null)
  assert.equal(monthBounds([{ dateDay: '2021-09-01', nSamples: 0 }], '2021-09'), null)
  assert.equal(monthBounds([], '2021-11'), null)
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

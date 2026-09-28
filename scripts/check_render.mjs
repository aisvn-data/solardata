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
  yearForPick,
} from '../src/components/StationExplorer.jsx'
import { channelsFor, filterByRange, loadRollup } from '../src/data.js'

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
// decorateRows -- against the real data rather than a fixture.
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
  const stations = readJson('stations.json')
  const station = stations.find((s) => s.is_production && s.years.length > 0)
  const year = station.years[station.years.length - 1]
  const rollup = await loadRollup(station.station_id, 'daily', year)
  const channels = channelsFor(station).filter((c) => rollup.channels.includes(c.key))
  return { station, year, rollup, rows: rollup.rows, channels }
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
    'stations',
    'source_files',
    'rejects_by_reason',
    'reject_vocabulary',
    'notes',
    'flag_totals',
    'flag_vocabulary',
    'flag_names',
    'windows',
    'exclusions',
    'coverage',
    'band_audit',
  ]) {
    assert.ok(key in quality, `quality.json is missing "${key}", which the inspector reads`)
  }
  assert.ok(Array.isArray(quality.notes), 'notes must be a list the inspector can render')
  assert.ok(Array.isArray(quality.rejects_by_reason), 'rejects_by_reason must be a list')
  assert.ok(
    Array.isArray(quality.band_audit.rows) && quality.band_audit.rows.length > 0,
    'the band audit must be a non-empty list, or the Bands tab is empty',
  )
  // Every reject reason is a declared category, so the report can group by it and
  // the prose can live once in config.py. A sentence here is 220,069 rows of the
  // same paragraph.
  for (const row of quality.rejects_by_reason) {
    assert.ok(
      quality.reject_vocabulary.includes(row.reason),
      `rejects_by_reason has "${row.reason}", which is not a declared category`,
    )
    assert.ok(row.reason.length < 40, `"${row.reason}" is too long to be a category`)
  }
  // Every station's channels carry what the per-station table renders.
  for (const station of quality.stations) {
    assert.ok(Array.isArray(station.channels), `${station.station_id} has a channel list`)
    for (const channel of station.channels) {
      assert.ok('observed' in channel, `${station.station_id}.${channel.channel} has observations`)
      assert.ok(
        'band_lo' in channel && 'band_hi' in channel,
        `${station.station_id}.${channel.channel} has an explicit band decision`,
      )
    }
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
  // `loadRollup` -> `channelsFor` path the component uses.
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

  const rollup = await loadRollup(opening.stationId, DEFAULT_VIEW.resolution, opening.year)
  const rows = rollup.rows
  // The component offers a channel only if the station publishes it *and* the
  // loaded file carries it, and `rollup.channels` is the file's own header. Same
  // path as the effect, so a column the exporter dropped fails here rather than
  // in a browser.
  const available = channelsFor(station)
    .filter((c) => rollup.channels.includes(c.key))
    .map((c) => c.key)

  // Every named channel survived, so the opening chart really is the
  // battery/solar/temperature trio rather than a fallback selection.
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
  const otherRollup = await loadRollup(other.station_id, 'daily', otherYear)
  const otherKeys = channelsFor(other)
    .filter((c) => otherRollup.channels.includes(c.key))
    .map((c) => c.key)
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
  // become the main one. Note the flag is `is_production`, not `published`:
  // `published` means "there is something to draw" and is true for all eight,
  // because a reader told a station is a WiFi probe can decide what to do with it.
  const bench = stations.filter((s) => !s.is_production).map((s) => s.station_id)
  assert.ok(bench.includes('test'), bench.join(','))
  assert.ok(bench.includes('voltage-phumy'), bench.join(','))
  // And every station is actually published, which is the 0.7.2 fix surviving a
  // rewrite: two stations of eight used to be missing from the site entirely.
  for (const station of stations) {
    assert.ok(
      station.published,
      `${station.station_id} has a rollup but is not published, so the site cannot show it`,
    )
  }
})

await check('a value is drawn in the unit the pipeline stored it in', async () => {
  // 0.8 carried the stored unit and the display unit separately: `test` stored
  // `temp_c` in hundredths, the rollup column was `temp_deci_c_avg` for every
  // station, and `displayDivisor` read a coefficient off a unit string in
  // `metrics.json`. Miss any of the three -- the scale, the unit, the divisor --
  // and the site draws 2,769 at 280 degC for a 28 degC afternoon. That is a
  // plausible-looking wrong number with no error anywhere, and it is the one
  // failure this project is built to avoid.
  //
  // 0.9 has one number. The scale is applied once at ingest, the band is in that
  // same unit, and the CSV carries the result. So there is no divisor, and the
  // assertion is that the value the chart draws is already a temperature.
  const stations = readJson('stations.json')
  for (const station of stations) {
    assert.equal(
      station.channel_units,
      undefined,
      `${station.station_id} still carries a channel_units lookup; there is one unit now`,
    )
  }

  // The exact set of channels the pipeline converts. A fourth appearing means one
  // of these is being applied somewhere it should not be, and a missing one means
  // a sheet is being read in the wrong unit. All of them are confirmed against the
  // hardware; none is a heuristic.
  const converted = Object.fromEntries(
    stations
      .map((s) => [
        s.station_id,
        s.channels
          .filter((c) => c.published && c.scale !== 1)
          .map((c) => c.channel)
          .sort(),
      ])
      .filter(([, list]) => list.length > 0),
  )
  assert.deepEqual(
    converted,
    {
      'aisvn-solar': ['battery_v', 'lipo_v', 'solar_v'],
      aisvn2: ['battery2_v', 'lipo2_v', 'solar3_v'],
      'maker-webhooks': ['battery_v', 'lipo_v', 'load_v', 'solar2_v', 'solar_v', 'wind_v'],
      phumy2: ['current2_a', 'lipo2_v', 'solar2_v'],
      'solar-2020-05': ['lipo_v'],
    },
    'the set of converted channels changed; every one of these is confirmed against hardware',
  )
  // And no temperature is converted, which is the 0.9 finding: the sheets write
  // degrees, and 0.8 multiplied three of them to match bands that were wrong by
  // the same factor.
  for (const station of stations) {
    for (const channel of station.channels) {
      if (channel.channel !== 'temp_c') continue
      assert.equal(channel.scale, 1, `${station.station_id}.temp_c is not scaled`)
      assert.equal(channel.unit, 'degC', `${station.station_id}.temp_c is in degrees`)
    }
  }

  // The strongest form: the numbers the browser draws are already plausible.
  const test = stations.find((s) => s.station_id === 'test')
  const rollup = await loadRollup('test', 'daily', test.years[0])
  const temp = channelsFor(test).find((c) => c.key === 'temp_c')
  assert.ok(temp, 'test has no temp_c channel to draw')
  const shown = rollup.rows.map((r) => r.values.temp_c).filter((v) => v !== null)
  assert.ok(shown.length > 0, 'no test temperature to check')
  const low = Math.min(...shown)
  const high = Math.max(...shown)
  assert.ok(
    low > 0 && high < 60,
    `test temperatures draw as ${low.toFixed(1)}..${high.toFixed(1)} degC, which is not a probe in Ho Chi City`,
  )

  // And the station whose current channel 0.8 flagged on all 416,088 of its
  // readings, by testing 232 mA against a +/-50 A band.
  const phumy2 = stations.find((s) => s.station_id === 'phumy2')
  const phumyRollup = await loadRollup('phumy2', 'daily', phumy2.years[0])
  const current = channelsFor(phumy2).find((c) => c.key === 'current2_a')
  assert.ok(current, 'phumy2 has no current2_a to draw')
  assert.equal(current.unit, 'A', 'it is published in amps')
  assert.deepEqual([current.band.lo, current.band.hi], [-5, 5], 'and banded in amps')
  const amps = phumyRollup.rows
    .map((r) => r.values.current2_a)
    .filter((v) => v !== null && v !== undefined)
  assert.ok(amps.length > 0, 'no phumy2 current to check')
  assert.ok(
    Math.min(...amps) > 0 && Math.max(...amps) < 2.5,
    `phumy2 current draws as ${Math.min(...amps).toFixed(3)}..${Math.max(...amps).toFixed(3)} A`,
  )
})

await check('a station is never offered a channel it does not collect', async () => {
  // The 0.9 contract, in the place a prop drift would be caught. A station's
  // picker is its published channels intersected with its own CSV header, so
  // `phumy2` -- which has no `power_w` at all -- cannot offer one, and
  // `aisvn-solar` cannot offer a wind input that never varies or a load rail
  // whose unit nobody has established.
  const stations = readJson('stations.json')
  for (const station of stations) {
    const offered = channelsFor(station).map((c) => c.key)
    const rollup = await loadRollup(station.station_id, 'daily', station.years[0])
    for (const key of offered) {
      assert.ok(
        rollup.channels.includes(key),
        `${station.station_id} offers ${key} but its CSV has no such column`,
      )
    }
    for (const key of rollup.channels) {
      assert.ok(
        offered.includes(key),
        `${station.station_id} CSV carries ${key} but the picker does not offer it`,
      )
    }
    for (const hidden of station.hidden ?? []) {
      assert.ok(
        !offered.includes(hidden.channel),
        `${station.station_id} offers ${hidden.channel}, which is excluded for ${hidden.reason}`,
      )
      assert.ok(hidden.note && hidden.note.length > 20, 'and the exclusion is explained')
    }
  }
})

await check('the recorded-but-not-charted panel explains every excluded channel', () => {
  // A reader who knows phumy2 has a power pin will otherwise conclude the site
  // dropped a column. It has not: the pin is not a measurement, and the sentence
  // saying so ships with the station and is rendered under the chart.
  const stations = readJson('stations.json')
  let hidden = 0
  for (const station of stations) {
    for (const channel of station.hidden ?? []) {
      hidden += 1
      assert.ok(
        [
          'not_measurement',
          'constant',
          'unresolved_unit',
          'text_label',
        ].includes(channel.reason),
        `${station.station_id}.${channel.channel} is excluded for "${channel.reason}", which the panel cannot name`,
      )
      assert.ok(channel.note.length > 40, `${channel.channel} has a sentence, not a stub`)
    }
  }
  assert.ok(hidden >= 3, `only ${hidden} excluded channels in the whole archive`)
})

await check('picking a station yields a year that station actually has', () => {
  // The bug: the pick handler read `.year` off an element of `years`. `years` is
  // an array of plain strings, so that is `undefined` for every station. The
  // loader's guard then aborted, the *previous* station's rollup stayed on screen,
  // and because `channels` is the new station's metadata intersected with the old
  // file's header, the new station's bands were tested against the old station's
  // values -- so every point came back out of band and ringed, with a plausible
  // "N values fall outside their channel's recorded band" caption over the top.
  //
  // Resolved against the real `stations.json` rather than a fixture, because the
  // failure was a shape mismatch between the JSON and the handler, and a fixture
  // would only assert the shape this check already assumes.
  const stations = readJson('stations.json')
  for (const station of stations) {
    const year = yearForPick(station)
    assert.equal(
      typeof year,
      'string',
      `${station.station_id} picked a ${typeof year} year, not a year string`,
    )
    assert.ok(
      station.years.includes(year),
      `${station.station_id} picked year ${year}, which is not one of its years ${JSON.stringify(station.years)}`,
    )
  }

  // And the one the check exists for: a station with a single year must produce
  // that year, and must not produce a year belonging to a different station.
  const single = stations.find((s) => s.years.length === 1)
  assert.ok(single, 'no single-year station in the archive to check')
  assert.equal(yearForPick(single), single.years[0])
  assert.equal(yearForPick({ station_id: 'none', years: [] }), undefined)
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
  // what `channelsFor` returns for a station with nothing, and it is the
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

await check('the picker is driven by the station, not by a fixed list', () => {
  // 0.8 kept `METRICS` as an empty compatibility shim for a hand-picked list of
  // six channels and added `discoverChannels` to replace it. Both are gone in
  // 0.9: there is no fixed list to fall back on, so this asserts the absence of
  // the shim and the presence of the two things that replaced it.
  const stations = readJson('stations.json')
  const shapes = stations.map((s) => channelsFor(s).length)
  assert.ok(
    new Set(shapes).size > 1,
    `every station offers the same ${shapes[0]} channels, which means something is fixed`,
  )
  // The four that have to differ, and differ for stated reasons.
  const counts = Object.fromEntries(
    stations.map((s) => [s.station_id, channelsFor(s).length]),
  )
  assert.equal(counts.phumy2, 5, 'phumy2 offers its five real channels, not power_w')
  assert.equal(
    counts['aisvn-solar'],
    4,
    'aisvn-solar offers four: two load rails of unknown unit and two dead inputs are out',
  )
  assert.equal(counts.test, 3, 'the probe offers its three channels')
  assert.equal(counts['voltage-phumy'], 3, 'the calibration sheet offers three')
})

console.log(`\n${passed} render checks passed`)

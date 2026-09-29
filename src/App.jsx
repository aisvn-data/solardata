import { useEffect, useState } from 'react'
import { version } from '../package.json'
import DiffInspector from './components/DiffInspector.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import QualityInspector from './components/QualityInspector.jsx'
import StationExplorer from './components/StationExplorer.jsx'

const TABS = [
  ['explore', 'Explore', 'Station values over time'],
  ['quality', 'Data quality', 'What the build did, and what it is unsure about'],
  ['configuration', 'Configuration', 'Pipeline tuning, normalization scales, curation rules, and raw telemetry diff viewer'],
]

function getRouteState() {
  if (typeof window === 'undefined') return { tab: 'explore', showAbout: false }
  const raw = window.location.hash.replace(/^#/, '')
  const [route] = raw.split('?')
  if (route === 'about') return { tab: 'explore', showAbout: true }
  if (route === 'quality' || route === 'configuration') return { tab: route, showAbout: false }
  if (route === 'inspector' || route === 'setup') return { tab: 'configuration', showAbout: false }
  return { tab: 'explore', showAbout: false }
}

function App() {
  const [route, setRoute] = useState(getRouteState)
  const tab = route.tab
  const showAbout = route.showAbout

  useEffect(() => {
    if (typeof window === 'undefined') return undefined
    function syncHash() {
      setRoute(getRouteState())
    }
    window.addEventListener('hashchange', syncHash)
    window.addEventListener('popstate', syncHash)
    return () => {
      window.removeEventListener('hashchange', syncHash)
      window.removeEventListener('popstate', syncHash)
    }
  }, [])

  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="brand" href="/">
          ☀ Solar Data <span className="version-badge">v{version}</span>
        </a>
        <nav aria-label="Main navigation">
          {TABS.map(([key, label]) => (
            <a
              key={key}
              href={`#${key}`}
              className={!showAbout && tab === key ? 'active' : ''}
              onClick={() => {
                if (typeof window !== 'undefined') {
                  window.location.hash = `#${key}`
                }
              }}
            >
              {label}
            </a>
          ))}
          <a
            href="#about"
            className={showAbout ? 'active' : ''}
            onClick={() => {
              if (typeof window !== 'undefined') {
                window.location.hash = showAbout ? `#${tab}` : '#about'
              }
            }}
          >
            About
          </a>
        </nav>
      </header>

      <main>
        <ErrorBoundary>
        {showAbout ? (
          <section className="section">
            <div className="section-heading">
              <div>
                <p className="eyebrow">About this data</p>
                <h2>731,885 readings, eight stations, six years</h2>
              </div>
            </div>
            <div className="prose">
              <p>
                Collected by solar stations in Nha Be and Phu My Hung, Ho Chi
                City, between May 2020 and September 2026. Readings were sent by
                IFTTT to Google Sheets, exported as XLSX, and chunked into
                2000-row files. This site reads the cleaned daily and hourly
                rollups; the full record is in the repository.
              </p>
              <p>
                Each station is one table, holding only the channels that station
                collects, and each one of those channels declares its own unit and
                its own plausible range &mdash; because a plausible range is a claim
                about a sensor at a site, not about a column name. The same input
                called <code>solar</code> is volts at AISVN&nbsp;#1 and
                millivolts at three other stations, and a range that ignored the
                difference would flag almost every reading.
              </p>
              <p>
                The archive is not straightforward, and the site is careful not to
                hide that. 305 of the 364 raw files have no header row, the same
                column name sometimes means different things at different times,
                and a value of <code>-992</code> means the sensor was disconnected
                rather than that the reading was &minus;992. A missing value is
                shown as a gap, never as a zero. A channel the station records but
                that is not a measurement &mdash; a wired input nobody can explain, a
                power pin the hardware never implemented &mdash; is listed with the
                reason rather than quietly charted.
              </p>
              <p>
                The <strong>Data quality</strong> tab lists, station by station,
                what each channel recorded and what the pipeline expects of it.
                Read it before drawing a conclusion from a chart.
              </p>
            </div>
          </section>
        ) : (
          <>
            {TABS.map(([key, label, description]) =>
              key === tab ? (
                <section className="section" id={key} key={key}>
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">{label}</p>
                      <h2>{description}</h2>
                    </div>
                  </div>
                  {key === 'explore' ? (
                    <StationExplorer />
                  ) : key === 'quality' ? (
                    <QualityInspector />
                  ) : (
                    <DiffInspector />
                  )}
                </section>
              ) : null,
            )}
          </>
        )}
        </ErrorBoundary>
      </main>

      <footer>
        Solar Data v{version} · an open data project ·{' '}
        <a href="https://github.com/kreier/solardata">source</a>
      </footer>
    </div>
  )
}

export default App

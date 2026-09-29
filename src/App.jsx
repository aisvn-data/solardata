import { useState } from 'react'
import { version } from '../package.json'
import DiffInspector from './components/DiffInspector.jsx'
import QualityInspector from './components/QualityInspector.jsx'
import StationExplorer from './components/StationExplorer.jsx'

const TABS = [
  ['explore', 'Explore', 'Station values over time'],
  ['quality', 'Data quality', 'What the build did, and what it is unsure about'],
  ['inspector', 'Pipeline Inspector', 'Raw vs curated telemetry diff and channel pipeline step editor'],
]

function App() {
  const [tab, setTab] = useState('explore')
  const [showAbout, setShowAbout] = useState(false)

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
              className={tab === key ? 'active' : ''}
              onClick={(e) => {
                e.preventDefault()
                setTab(key)
              }}
            >
              {label}
            </a>
          ))}
          <a
            href="#about"
            className={showAbout ? 'active' : ''}
            onClick={(e) => {
              e.preventDefault()
              setShowAbout((v) => !v)
            }}
          >
            About
          </a>
        </nav>
      </header>

      <main>
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
      </main>

      <footer>
        Solar Data v{version} · an open data project ·{' '}
        <a href="https://github.com/kreier/solardata">source</a>
      </footer>
    </div>
  )
}

export default App

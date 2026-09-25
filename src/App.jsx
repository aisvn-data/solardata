import { useState } from 'react'
import QualityInspector from './components/QualityInspector.jsx'
import StationExplorer from './components/StationExplorer.jsx'

const TABS = [
  ['explore', 'Explore', 'Station values over time'],
  ['quality', 'Data quality', 'What the build did, and what it is unsure about'],
]

function App() {
  const [tab, setTab] = useState('explore')
  const [showAbout, setShowAbout] = useState(false)

  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="brand" href="/">
          ☀ Solar Data
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
                <h2>735,004 readings, eight stations, four years</h2>
              </div>
            </div>
            <div className="prose">
              <p>
                Collected by solar stations in Nha Be and Phu My Hung, Ho Chi
                City, between May 2020 and February 2024. Readings were sent by
                IFTTT to Google Sheets, exported as XLSX, and chunked into
                2000-row files. This site reads the cleaned daily rollups; the
                full record is in the repository.
              </p>
              <p>
                The archive is not straightforward, and the site is careful not
                to hide that. 305 of the 364 raw files have no header row, the
                same column name sometimes means different things at different
                times, and a value of <code>-992</code> means the sensor was
                disconnected rather than that the reading was −992. A missing
                value is shown as a gap, never as a zero.
              </p>
              <p>
                The <strong>Data quality</strong> tab lists what the pipeline
                inferred, flagged, or could not resolve. Read it before drawing a
                conclusion from a chart.
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
                  {key === 'explore' ? <StationExplorer /> : <QualityInspector />}
                </section>
              ) : null,
            )}
          </>
        )}
      </main>

      <footer>
        Solar Data · an open data project ·{' '}
        <a href="https://github.com/kreier/solardata">source</a>
      </footer>
    </div>
  )
}

export default App

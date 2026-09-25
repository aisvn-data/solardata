const stations = [
  { name: 'Nha Be', status: 'Ready', readings: '2020–2021' },
  { name: 'Phu My Hung', status: 'Ready', readings: '2020–2021' },
]

function App() {
  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="brand" href="/">☀ Solar Data</a>
        <nav aria-label="Main navigation">
          <a href="#overview">Overview</a>
          <a href="#stations">Stations</a>
          <a href="#about">About</a>
        </nav>
      </header>

      <main>
        <section className="hero" id="overview">
          <p className="eyebrow">Initial release · v0.1</p>
          <h1>Explore collected solar data.</h1>
          <p className="hero-copy">
            A starting point for cleaning, structuring, and visualizing readings
            from solar stations in Nha Be and Phu My Hung.
          </p>
          <a className="button" href="#stations">View stations</a>
        </section>

        <section className="section" id="stations">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Data sources</p>
              <h2>Stations</h2>
            </div>
            <span className="badge">2 connected</span>
          </div>
          <div className="station-grid">
            {stations.map((station) => (
              <article className="station-card" key={station.name}>
                <div className="station-icon">⌁</div>
                <div>
                  <h3>{station.name}</h3>
                  <p>{station.readings}</p>
                </div>
                <span className="status">● {station.status}</span>
              </article>
            ))}
          </div>
        </section>

        <section className="section roadmap" id="about">
          <p className="eyebrow">Roadmap</p>
          <h2>Building the foundation</h2>
          <div className="roadmap-items">
            <div><strong>01</strong><span>Import raw Google Sheets data</span></div>
            <div><strong>02</strong><span>Clean and label structured readings</span></div>
            <div><strong>03</strong><span>Search and visualize historic trends</span></div>
          </div>
        </section>
      </main>

      <footer>Solar Data · A small open data project</footer>
    </div>
  )
}

export default App

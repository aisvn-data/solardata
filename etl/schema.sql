-- Shared DDL. The eight per-station reading tables are NOT here: they are
-- generated from etl/catalog.py, because a table's columns are the channels its
-- station collects and nothing else, and a hand-written table for each would
-- drift from the catalog the moment a channel was added. See etl/db.py.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- One row per pipeline run. The database is rebuilt from scratch every time, so
-- there is exactly one of these in a fresh build and its job is to say so.
CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id       INTEGER PRIMARY KEY,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    tool_version TEXT NOT NULL,
    raw_dir      TEXT NOT NULL,
    notes        TEXT
);

-- Provenance for every raw file. sha256 lets a rebuild prove it read the same
-- bytes; n_layout_files and layout_id say which catalog layout was used, which
-- in 0.9 is the whole of a headerless file's column meaning.
CREATE TABLE IF NOT EXISTS source_files (
    file_id         INTEGER PRIMARY KEY,
    run_id          INTEGER REFERENCES ingest_runs(run_id),
    source_dir      TEXT NOT NULL,
    station_id      TEXT NOT NULL,
    filename        TEXT NOT NULL,
    rel_path        TEXT NOT NULL UNIQUE,
    sha256          TEXT,
    bytes           INTEGER,
    has_header      INTEGER NOT NULL,
    header_json     TEXT,
    n_columns       INTEGER NOT NULL,
    layout_channels TEXT,
    n_rows          INTEGER NOT NULL,
    n_ingested      INTEGER NOT NULL DEFAULT 0,
    n_rejected      INTEGER NOT NULL DEFAULT 0,
    n_duplicate_ts  INTEGER NOT NULL DEFAULT 0,
    n_notes         INTEGER NOT NULL DEFAULT 0,
    extra_blocks    INTEGER NOT NULL DEFAULT 0,
    excluded        TEXT,
    min_ts_utc      TEXT,
    max_ts_utc      TEXT
);
CREATE INDEX IF NOT EXISTS ix_source_files_station ON source_files (station_id, source_dir);

-- The station registry. Coverage columns are filled in after the ingest.
CREATE TABLE IF NOT EXISTS stations (
    station_id   TEXT PRIMARY KEY,
    table_name   TEXT NOT NULL,
    display_name TEXT NOT NULL,
    location     TEXT,
    tz           TEXT NOT NULL,
    applet       TEXT,
    source_dirs  TEXT NOT NULL,
    is_production INTEGER NOT NULL DEFAULT 1,
    published_group TEXT NOT NULL DEFAULT 'solar production',
    notes        TEXT,
    first_ts_utc TEXT,
    last_ts_utc  TEXT,
    n_readings   INTEGER
);

-- The catalog, as it landed in the database. One row per (station, channel),
-- written verbatim from etl/catalog.py, so a query against the database can
-- answer "what does this station collect, in what unit, banded how" without the
-- Python package. The report and public/data/stations.json are generated from
-- this table, not from the module, so there is one answer rather than two.
CREATE TABLE IF NOT EXISTS station_channels (
    station_id    TEXT NOT NULL,
    channel       TEXT NOT NULL,
    col_index     INTEGER,
    label         TEXT NOT NULL,
    kind          TEXT NOT NULL,
    description   TEXT NOT NULL,
    unit          TEXT NOT NULL,
    raw_unit      TEXT,
    scale         REAL NOT NULL DEFAULT 1.0,
    band_lo       REAL,
    band_hi       REAL,
    band_note     TEXT NOT NULL DEFAULT '',
    stats         TEXT NOT NULL DEFAULT '',
    published     INTEGER NOT NULL DEFAULT 1,
    exclude_reason TEXT,
    exclude_note  TEXT NOT NULL DEFAULT '',
    is_counter    INTEGER NOT NULL DEFAULT 0,
    decimals      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (station_id, channel)
);

-- The layouts, the same way. A layout is keyed by (station, width) and that is
-- the only thing that maps a raw file to its column meanings.
CREATE TABLE IF NOT EXISTS station_layouts (
    station_id  TEXT NOT NULL,
    n_columns   INTEGER NOT NULL,
    header_json TEXT NOT NULL,
    channels    TEXT NOT NULL,
    n_files     INTEGER NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (station_id, n_columns)
);

-- What each channel actually recorded, measured on the real archive. This is
-- the per-station documentation the pipeline exists to produce: start, stop,
-- count, min, max, mean, the percentiles that show bimodality, and how many
-- readings the band flagged. Written by the aggregate stage, read by the report
-- and by public/data/stations.json.
CREATE TABLE IF NOT EXISTS channel_stats (
    station_id   TEXT NOT NULL,
    channel      TEXT NOT NULL,
    n_values     INTEGER NOT NULL,
    n_nulls      INTEGER NOT NULL,
    first_ts_utc TEXT,
    last_ts_utc  TEXT,
    min          REAL,
    max          REAL,
    mean         REAL,
    p01          REAL,
    p50          REAL,
    p99          REAL,
    n_zero       INTEGER NOT NULL DEFAULT 0,
    n_sentinel   INTEGER NOT NULL DEFAULT 0,
    n_null_window INTEGER NOT NULL DEFAULT 0,
    n_out_of_range INTEGER NOT NULL DEFAULT 0,
    n_constant   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (station_id, channel)
);

-- Every cell that did not become a reading, with where it was and what it said.
-- reason is a stable category from etl.config.REASONS, never a sentence.
CREATE TABLE IF NOT EXISTS rejects (
    reject_id   INTEGER PRIMARY KEY,
    run_id      INTEGER REFERENCES ingest_runs(run_id),
    file_id     INTEGER REFERENCES source_files(file_id),
    station_id  TEXT,
    sheet_row   INTEGER,
    column_name TEXT,
    raw_value   TEXT,
    reason      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rejects_station ON rejects (station_id, reason);

-- Human prose found in a data cell, anchored to the instant and the spreadsheet
-- column it came from.
CREATE TABLE IF NOT EXISTS notes (
    note_id     INTEGER PRIMARY KEY,
    run_id      INTEGER REFERENCES ingest_runs(run_id),
    station_id  TEXT,
    file_id     INTEGER REFERENCES source_files(file_id),
    ts_utc      TEXT,
    column_name TEXT,
    note        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notes_station ON notes (station_id, ts_utc);

-- Hourly and daily rollups are NOT here either. Their value columns are the
-- union over stations of the published channels' statistics, so they are
-- generated from etl/catalog.py alongside the per-station tables. See
-- rollup_columns() and readings_ddl() in etl/db.py.

-- What each build stage produced, so a report can say what it was built from.
CREATE TABLE IF NOT EXISTS build_log (
    build_id      INTEGER PRIMARY KEY,
    run_id        INTEGER REFERENCES ingest_runs(run_id),
    artefact      TEXT NOT NULL,
    target        TEXT,
    rows_written  INTEGER,
    bytes_written INTEGER,
    created_at    TEXT NOT NULL
);

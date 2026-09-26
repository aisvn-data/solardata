-- solardata canonical schema.
--
-- Design rule: an observation is always traceable to the file and row it came
-- from, and a metric's meaning is data (metric_defs / regimes) rather than a
-- comment in the code.  The raw archive is ambiguous enough that a future
-- reader has to be able to disagree with a normalisation decision without
-- re-deriving it from scratch.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- provenance

CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id       INTEGER PRIMARY KEY,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    tool_version TEXT NOT NULL,
    raw_dir      TEXT NOT NULL,
    notes        TEXT
);

-- One row per raw XLSX file, with the structural facts we detected about it.
CREATE TABLE IF NOT EXISTS source_files (
    file_id        INTEGER PRIMARY KEY,
    run_id         INTEGER REFERENCES ingest_runs(run_id),
    source_dir     TEXT NOT NULL,   -- folder under data/raw
    station_id     TEXT NOT NULL,   -- resolved via etl.stations
    filename       TEXT NOT NULL,
    rel_path       TEXT NOT NULL UNIQUE,
    sha256         TEXT,
    bytes          INTEGER,
    has_header     INTEGER NOT NULL,  -- 0/1
    header_json    TEXT,              -- the header row, or NULL
    n_columns      INTEGER NOT NULL,
    n_body_rows    INTEGER NOT NULL,
    n_ingested     INTEGER NOT NULL DEFAULT 0,
    n_rejected     INTEGER NOT NULL DEFAULT 0,
    n_duplicate_ts INTEGER NOT NULL DEFAULT 0,
    extra_blocks   INTEGER NOT NULL DEFAULT 0,  -- side-by-side column blocks found
    repeated_headers INTEGER NOT NULL DEFAULT 0,
    -- For a headerless file: which sibling file's header row supplied the
    -- column names.  NULL when the file had its own header.
    schema_donor   TEXT,
    inferred       INTEGER NOT NULL DEFAULT 0,
    min_ts_utc     TEXT,
    max_ts_utc     TEXT,
    UNIQUE (rel_path)
);

CREATE INDEX IF NOT EXISTS ix_source_files_station
    ON source_files (station_id, source_dir);

-- Cells we refused to turn into a reading, with the reason.  Nothing vanishes.
-- This is also where a same-station duplicate timestamp lands: the primary key
-- on `readings` absorbs the second copy, and the fact is recorded here so the
-- 4,403 absorbed rows stay individually inspectable rather than becoming just
-- a count.
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

CREATE INDEX IF NOT EXISTS ix_rejects_run ON rejects (run_id, reason);

-- Human lab notes that were sitting in a data cell, e.g. the discharge-test
-- annotations in data/raw/Voltage_phumy.
CREATE TABLE IF NOT EXISTS notes (
    note_id    INTEGER PRIMARY KEY,
    run_id     INTEGER REFERENCES ingest_runs(run_id),
    station_id TEXT,
    file_id    INTEGER REFERENCES source_files(file_id),
    ts_utc     TEXT,
    column_name TEXT,
    note       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_notes_station ON notes (station_id, ts_utc);

-- ------------------------------------------------------------------ metadata

CREATE TABLE IF NOT EXISTS stations (
    station_id   TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    location     TEXT,
    tz           TEXT NOT NULL,
    applet       TEXT,
    source_dirs  TEXT NOT NULL,  -- JSON array
    notes        TEXT,
    is_production INTEGER NOT NULL DEFAULT 1,
    first_ts_utc TEXT,           -- filled in after ingest
    last_ts_utc  TEXT,
    n_readings   INTEGER
);

-- What each raw column of each station means, and how confident we are.
-- inferred = 1 means the file had no header row and the mapping was borrowed
-- from a sibling file in the same archive folder.
--
-- `n_columns` is part of the key, and it has to be. A folder is a chronological
-- run of 2000-row chunks from one applet, and the applet is allowed to change
-- its columns partway through: `aisvn` went from 10 columns to 11 on
-- 2020-06-17 when a `power` channel was added, `Maker_Webhooks_Events` did the
-- same, and `test` is two unrelated schemas in one folder (4 columns of nix/temp/
-- wifi probe for 16 of its 19 files, 11 columns of solar channels for 2).
--
-- Keyed on (station, folder, column) alone -- the obvious choice, and the one
-- this table used to use -- a folder can hold exactly one meaning per index, so
-- the second layout silently overwrote the first. For `aisvn` that produced a
-- single row saying column 4 is `load`/`load_v` for all 39 files, when in fact
-- it is `load` in one file and `power` in the other 38. The *ingest* was never
-- affected: it maps each file with its own width-matched effective header, and
-- `readings` is correct. This table is the one the report and the website's
-- channel-coverage tab present as the schema, so it was the only place the
-- archive's two layouts were conflated into one.
CREATE TABLE IF NOT EXISTS metric_defs (
    station_id      TEXT NOT NULL REFERENCES stations(station_id),
    source_dir      TEXT NOT NULL,
    n_columns       INTEGER NOT NULL,   -- width of the raw layout this describes
    col_index       INTEGER NOT NULL,   -- 0-based index in the raw sheet
    raw_name        TEXT,               -- header text, '' when headerless
    canonical_col   TEXT,               -- NULL when unmapped
    unit            TEXT,
    kind            TEXT,
    confidence      TEXT NOT NULL,      -- high | medium | low
    inferred        INTEGER NOT NULL DEFAULT 0,
    reason          TEXT,
    n_files         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (station_id, source_dir, n_columns, col_index)
);

-- Windows over which a column's scaling is believed constant.  Written by the
-- regime detector with status='unconfirmed'; a human promotes them.
CREATE TABLE IF NOT EXISTS regimes (
    regime_id   INTEGER PRIMARY KEY,
    station_id  TEXT NOT NULL REFERENCES stations(station_id),
    column      TEXT NOT NULL,
    unit        TEXT,
    valid_from  TEXT NOT NULL,
    valid_to    TEXT,
    scale       REAL NOT NULL DEFAULT 1.0,
    status      TEXT NOT NULL DEFAULT 'unconfirmed',
    detected_by TEXT NOT NULL,
    confidence  TEXT NOT NULL,
    notes       TEXT,
    evidence    TEXT,   -- JSON
    UNIQUE (station_id, column, valid_from, scale)
);

CREATE INDEX IF NOT EXISTS ix_regimes_lookup ON regimes (station_id, column, valid_from);

-- ---------------------------------------------------------------- observations

-- One row per station per instant.  Wide and sparse by design: each station
-- only populates the channels it had.  NULL means "not measured / suppressed
-- sentinel", never 0.
--
-- Why wide rather than long (station, ts, metric, value): the difficulty in
-- this dataset is not volume, it is that a column's meaning changes with time.
-- A wide table with a companion `regimes` table puts that uncertainty in a
-- place a reviewer can see and amend; a long table would have buried the same
-- information in a self-join.  735k rows is small enough that the row width
-- costs nothing, and Parquet compresses the sparse columns well regardless.
--
-- WITHOUT ROWID: the primary key *is* the row, and the natural clustering by
-- (station, ts_utc) serves the range queries the website actually runs.
CREATE TABLE IF NOT EXISTS readings (
    station_id TEXT NOT NULL REFERENCES stations(station_id),
    ts_utc     TEXT NOT NULL,   -- RFC 3339, 'Z', the primary key clock
    ts_local   TEXT NOT NULL,   -- naive local wall clock, kept for display
    tz         TEXT NOT NULL,

    solar_v     REAL, solar2_v  REAL, solar3_v  REAL,
    battery_v   REAL, battery2_v REAL,
    current_a   REAL, current2_a REAL,
    current_a_chA REAL, current_a_chB REAL,
    power_w     REAL,
    load_v      REAL, load1_v   REAL, load2_v   REAL,
    wind_v      REAL,
    temp_c      REAL,
    lipo_v      REAL, lipo2_v   REAL,

    adc_raw     REAL, voltage_adc REAL, digital_adc REAL, dump_adc REAL,
    boot_count  INTEGER, millis_ms INTEGER,
    nix_raw     REAL, wifi_raw  REAL,
    event       TEXT,

    quality_flags TEXT NOT NULL DEFAULT '',
    source_file_id INTEGER REFERENCES source_files(file_id),
    sheet_row      INTEGER,
    PRIMARY KEY (station_id, ts_utc)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS ix_readings_ts ON readings (ts_utc);
CREATE INDEX IF NOT EXISTS ix_readings_file ON readings (source_file_id);

-- Pre-aggregated views so the website never scans the raw table.
--
-- The numbered `*2` / `*3` channels matter: phumy2 logs `solar2` and aisvn2
-- logs `solar3` + `battery2`, so aggregating only `solar_v` / `battery_v` left
-- the two largest stations with nothing at all to chart. The frontend treats a
-- numbered variant as the same metric and uses whichever the station has.
--
-- `n_out_of_range` carries the quality signal into the rollup. Without it a day
-- whose only reading is an ADC test pattern (solar 123 V, battery 456 V) looks
-- exactly like a real day in a chart -- which is how one reached the website.
-- The rollup tables carry two families of column beyond the bucket key, and
-- both are declared in `etl/rollup_schema.py` so the aggregate stage and the
-- export stage cannot disagree about which channels exist or which statistic
-- each one holds. That is not hypothetical: the daily table once had no
-- `battery_v_avg` while the hourly one did, and every consumer had to know.
--
-- `<channel>_<stat>` is the aggregated value -- `avg`, `min` or `max`, chosen per
-- channel because a LiPo pack's health is its lowest reading and a power spike
-- is a peak. `<channel>_n_oor` counts the samples in the bucket whose value fell
-- outside the band `etl/normalize/metrics.py` records for that channel.
--
-- The per-metric count exists because the row-level `n_out_of_range` cannot
-- answer the question the site needs to ask. `phumy2` 2020-11-27 16:00 UTC is the
-- case: one sample reads `power_w = 19877` where its neighbours are 0, and
-- averaged with the 29 good zeros in that hour it becomes 662.57 -- *inside* the
-- +/-2000 W band, so the aggregate carries no flag. The row-level count is no
-- help either, because every sample in that hour is flagged: `current2_a` reads
-- ~232 against a +/-50 A band. One flag for the row, 30 of 30, and the one sample
-- that actually broke something is invisible. Counting per metric gives 1 of 30
-- on `power_w`, which is answerable.

CREATE TABLE IF NOT EXISTS readings_hourly (
    station_id TEXT NOT NULL,
    ts_utc     TEXT NOT NULL,
    n_samples  INTEGER NOT NULL,
    n_out_of_range INTEGER NOT NULL DEFAULT 0,
    solar_v_avg REAL,
    solar_v_max REAL,
    solar2_v_avg REAL,
    solar2_v_max REAL,
    solar3_v_avg REAL,
    solar3_v_max REAL,
    battery_v_avg REAL,
    battery_v_min REAL,
    battery_v_max REAL,
    battery2_v_min REAL,
    battery2_v_max REAL,
    lipo_v_avg REAL,
    lipo_v_min REAL,
    lipo_v_max REAL,
    lipo2_v_avg REAL,
    lipo2_v_min REAL,
    lipo2_v_max REAL,
    current_a_avg REAL,
    current_a_chA_avg REAL,
    current_a_chB_avg REAL,
    current2_a_avg REAL,
    power_w_avg REAL,
    power_w_max REAL,
    load_v_avg REAL,
    load1_v_avg REAL,
    load2_v_avg REAL,
    wind_v_avg REAL,
    temp_c_avg REAL,
    temp_c_min REAL,
    temp_c_max REAL,
    -- The two bench ADC channels, uncalibrated. solar-2020-05 is a bench sheet
    -- whose only measurements are these and a LiPo pack; without them that station
    -- offers a single channel, which reads as broken rather than small. Neither has
    -- a plausibility band, so neither gets an out-of-range count.
    voltage_adc_avg REAL, digital_adc_avg REAL,
    solar_v_n_oor INTEGER NOT NULL DEFAULT 0,
    solar2_v_n_oor INTEGER NOT NULL DEFAULT 0,
    solar3_v_n_oor INTEGER NOT NULL DEFAULT 0,
    battery_v_n_oor INTEGER NOT NULL DEFAULT 0,
    battery2_v_n_oor INTEGER NOT NULL DEFAULT 0,
    lipo_v_n_oor INTEGER NOT NULL DEFAULT 0,
    lipo2_v_n_oor INTEGER NOT NULL DEFAULT 0,
    current_a_n_oor INTEGER NOT NULL DEFAULT 0,
    current_a_chA_n_oor INTEGER NOT NULL DEFAULT 0,
    current_a_chB_n_oor INTEGER NOT NULL DEFAULT 0,
    current2_a_n_oor INTEGER NOT NULL DEFAULT 0,
    power_w_n_oor INTEGER NOT NULL DEFAULT 0,
    load_v_n_oor INTEGER NOT NULL DEFAULT 0,
    load1_v_n_oor INTEGER NOT NULL DEFAULT 0,
    load2_v_n_oor INTEGER NOT NULL DEFAULT 0,
    temp_c_n_oor INTEGER NOT NULL DEFAULT 0,
    -- never a mean: a mean across a reboot averages two boot sessions into a
    -- number that never happened, and max - min is what says whether the logger
    -- restarted inside the bucket. This is the only channel that records the
    -- hardware's own view of its uptime.
    boot_count_min INTEGER, boot_count_max INTEGER,
    energy_wh REAL,          -- power_w_avg * hours, when the hour is complete
    scaled_channels TEXT NOT NULL DEFAULT '',
    regime_ids      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (station_id, ts_utc)
) WITHOUT ROWID;


CREATE TABLE IF NOT EXISTS readings_daily (
    station_id TEXT NOT NULL,
    day        TEXT NOT NULL,   -- YYYY-MM-DD, local calendar day
    ts_utc_day TEXT NOT NULL,   -- UTC midnight of that local day
    n_samples  INTEGER NOT NULL,
    n_out_of_range INTEGER NOT NULL DEFAULT 0,
    n_hours    INTEGER,
    solar_v_avg REAL,
    solar_v_max REAL,
    solar2_v_avg REAL,
    solar2_v_max REAL,
    solar3_v_avg REAL,
    solar3_v_max REAL,
    battery_v_avg REAL,
    battery_v_min REAL,
    battery_v_max REAL,
    battery2_v_min REAL,
    battery2_v_max REAL,
    lipo_v_avg REAL,
    lipo_v_min REAL,
    lipo_v_max REAL,
    lipo2_v_avg REAL,
    lipo2_v_min REAL,
    lipo2_v_max REAL,
    current_a_avg REAL,
    current_a_chA_avg REAL,
    current_a_chB_avg REAL,
    current2_a_avg REAL,
    power_w_avg REAL,
    power_w_max REAL,
    load_v_avg REAL,
    load1_v_avg REAL,
    load2_v_avg REAL,
    wind_v_avg REAL,
    temp_c_avg REAL,
    temp_c_min REAL,
    temp_c_max REAL,
    -- The two bench ADC channels, uncalibrated. solar-2020-05 is a bench sheet
    -- whose only measurements are these and a LiPo pack; without them that station
    -- offers a single channel, which reads as broken rather than small. Neither has
    -- a plausibility band, so neither gets an out-of-range count.
    voltage_adc_avg REAL, digital_adc_avg REAL,
    solar_v_n_oor INTEGER NOT NULL DEFAULT 0,
    solar2_v_n_oor INTEGER NOT NULL DEFAULT 0,
    solar3_v_n_oor INTEGER NOT NULL DEFAULT 0,
    battery_v_n_oor INTEGER NOT NULL DEFAULT 0,
    battery2_v_n_oor INTEGER NOT NULL DEFAULT 0,
    lipo_v_n_oor INTEGER NOT NULL DEFAULT 0,
    lipo2_v_n_oor INTEGER NOT NULL DEFAULT 0,
    current_a_n_oor INTEGER NOT NULL DEFAULT 0,
    current_a_chA_n_oor INTEGER NOT NULL DEFAULT 0,
    current_a_chB_n_oor INTEGER NOT NULL DEFAULT 0,
    current2_a_n_oor INTEGER NOT NULL DEFAULT 0,
    power_w_n_oor INTEGER NOT NULL DEFAULT 0,
    load_v_n_oor INTEGER NOT NULL DEFAULT 0,
    load1_v_n_oor INTEGER NOT NULL DEFAULT 0,
    load2_v_n_oor INTEGER NOT NULL DEFAULT 0,
    temp_c_n_oor INTEGER NOT NULL DEFAULT 0,
    energy_wh REAL,          -- sum of the hourly buckets' energy
    -- Uptime counter, min and max for the same reason as the hourly rollup.
    boot_count_min INTEGER, boot_count_max INTEGER,
    scaled_channels TEXT NOT NULL DEFAULT '',
    regime_ids      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (station_id, day)
) WITHOUT ROWID;



-- ----------------------------------------------------------------- pipeline

CREATE TABLE IF NOT EXISTS build_log (
    build_id    INTEGER PRIMARY KEY,
    run_id      INTEGER REFERENCES ingest_runs(run_id),
    artefact    TEXT NOT NULL,   -- db | parquet | export | report
    target      TEXT,
    rows_written INTEGER,
    bytes_written INTEGER,
    created_at  TEXT NOT NULL
);

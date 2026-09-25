"""Convert the raw IFTTT/Google-Sheets XLSX archive into a queryable store.

The raw archive under ``data/raw`` is a pile of Google Sheets exports that were
chunked into 2000-row files.  Header rows are present in only ~10% of them,
column layouts drift between applet revisions, several sheets carry redundant
side-by-side column blocks, and sensor scaling changes over time.  This package
turns that into a single SQLite database plus Parquet and frontend exports,
without ever discarding a value silently.

Entry point: ``python -m etl``
"""

__version__ = "0.2.0"

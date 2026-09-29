"""Convert the raw IFTTT/Google-Sheets XLSX archive into a queryable store.

The raw archive under ``data/raw`` is a pile of Google Sheets exports that were
chunked into 2000-row files.  Header rows are present in only ~16% of them,
column layouts drift between applet revisions, several sheets carry redundant
side-by-side column blocks, and the same input name means different things at
different sites in different units.

0.9 is built around one idea: **a channel is a fact about a station, not a fact
about a column name.**  Each of the eight stations gets its own table holding
only what it collects, and each (station, channel) declares its own unit, its
confirmed scale and its own plausibility band in ``etl.catalog``.  0.8 declared
one band per column name and tested raw millivolt cells against it, which marked
631,252 readings out of range on arithmetic rather than on the hardware -- all
416,088 of ``phumy2``'s samples, on a sensor measuring a quarter of an amp.

The consequences run through everything:

* one unit, everywhere.  The confirmed scale is applied once, at ingest, so the
  database, the rollups, the CSVs and the browser hold the same number in the
  same unit.  There is no regime table and no out-of-range count that has to be
  recomputed after the fact.
* a headerless file's column meanings come from the catalog, keyed on
  ``(station, width)``.  The archive has nine such pairs and no station has two
  layouts of the same width, so the lookup is total and an undeclared width is a
  loud failure rather than a silent loss of 90% of a station's measurements.
* a station's CSV carries only that station's channels, so a channel a station
  does not collect is not a column a reader can mistake for a measurement.
* every channel that is recorded but not shown says why, in prose, next to the
  min and max it actually recorded.

Entry point: ``python -m etl``.  Rules that are not negotiable are in
``AGENTS.md``; the open questions are in ``docs/roadmap.md`` and at the bottom
of ``etl/catalog.py``.
"""

__version__ = "0.11.0"

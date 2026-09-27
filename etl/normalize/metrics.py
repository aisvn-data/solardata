"""Mapping raw column headers onto canonical, unit-bearing metric names.

The archive uses ~30 distinct header spellings across 8 stations, and the same
spelling does not always mean the same thing.  Two examples that make a naive
mapping wrong:

* ``load`` is a **voltage** in ``aisvn`` (14.15, 13.75) but is carried in the
  raw/unscaled channel in ``test``.
* ``boot`` is not a boolean.  It increments by one per reading
  (14875 -> 14876) and resets when the logger reboots, which makes it a
  monotonic counter and a useful reboot detector.

Anything that cannot be mapped confidently is kept in ``unmapped`` rather than
guessed at, and the raw value is still stored in the provenance tables.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    """A canonical measurement channel."""

    column: str
    unit: str
    kind: str  # voltage | current | power | temperature | count | raw | text
    description: str
    #: Plausible physical range, used only to raise ``out_of_range`` flags.
    lo: float | None = None
    hi: float | None = None
    #: Expected sampling cadence hint, purely documentary.
    expect: str = ""


#: Canonical columns, in the order they appear in the ``readings`` table.
#:
#: The ``lo``/``hi`` bands are deliberately narrow and are set from what the
#: hardware actually produces, not from textbook ranges.  ``battery_v`` is banded
#: 9-16 V because the hardware is a 12 V lead-acid pack: it rests at 12.4-12.8 V,
#: sits at 13.8-14.4 V while charging, and reads ~10.1 V deeply discharged, so
#: 9-16 V covers the range and no more.  That is what makes a window logging
#: "29.12 V" show up as ``out_of_range`` instead of blending silently into the
#: record.  These bands only ever raise a flag -- no value is ever clipped,
#: nulled, or rescaled on the strength of them.
#:
#: The band is keyed by column, so it describes the hardware at *every* station
#: that logs ``battery_v``.  ``aisvn``'s pack is confirmed by the collector to be
#: a lead car battery, not the 3S LiPo this description used to claim; the 12 V
#: lead-acid band is the right one for it and for the others, and the separate
#: ``lipo_v``/``lipo2_v`` channels (2.5-4.35 V) are where the LiPo packs are.
METRICS: tuple[Metric, ...] = (
    Metric("solar_v", "V", "voltage", "Solar panel / collector voltage", 0.0, 60.0),
    Metric("solar2_v", "V", "voltage", "Second solar input voltage", 0.0, 60.0),
    Metric("solar3_v", "V", "voltage", "Third solar input voltage", 0.0, 60.0),
    Metric("battery_v", "V", "voltage", "Battery bank voltage", 9.0, 16.0),
    Metric("battery2_v", "V", "voltage", "Second battery bank voltage", 9.0, 16.0),
    Metric("current_a", "A", "current", "Primary current", -50.0, 50.0),
    Metric("current2_a", "A", "current", "Secondary current", -50.0, 50.0),
    Metric("current_a_chA", "A", "current", "Current, channel A", -50.0, 50.0),
    Metric("current_a_chB", "A", "current", "Current, channel B", -50.0, 50.0),
    Metric("power_w", "W", "power", "Instantaneous power", -2000.0, 2000.0),
    Metric("load_v", "V", "voltage", "Load / dump rail voltage", 0.0, 60.0),
    Metric("load1_v", "V", "voltage", "Load rail 1 voltage", 0.0, 60.0),
    Metric("load2_v", "V", "voltage", "Load rail 2 voltage", 0.0, 60.0),
    # No band, deliberately. The channel is wired and it logs: `aisvn` records
    # 0-13.3 V in 2021 (non-zero in 176 of November's 696 hours) and up to 12,784 V
    # in 2020, then exactly 0 for all of 2022. A label that called it unused was
    # wrong, and the values are not a plausible generator output either, so the
    # honest answer is the name of the input and no verdict: a band would have to
    # be a guess, and a guess here would flag either every real reading or none.
    # What the input is connected to is asked of the collector, not decided here.
    Metric("wind_v", "V", "voltage", "Wind turbine input", None, None),
    Metric(
        "temp_c",
        "0.1 degC",
        "temperature",
        "Ambient temperature (Ho Chi City)",
        50.0,
        900.0,
    ),
    Metric("lipo_v", "V", "voltage", "LiPo pack voltage", 2.5, 4.35),
    Metric("lipo2_v", "V", "voltage", "Second LiPo pack voltage", 2.5, 4.35),
    Metric("adc_raw", "count", "raw", "Raw ADC reading, uncalibrated", None, None),
    Metric("voltage_adc", "count", "raw", "Raw ADC reading on the voltage channel", None, None),
    Metric("digital_adc", "count", "raw", "Raw ADC reading on a digital channel", None, None),
    Metric("dump_adc", "count", "raw", "Raw ADC reading on the dump channel", None, None),
    Metric(
        "boot_count", "count", "count", "Monotonic logger counter, resets on reboot", None, None
    ),
    Metric("millis_ms", "ms", "count", "millis() since boot", None, None),
    Metric("nix_raw", "count", "raw", "Non-solar probe channel (test bench)", None, None),
    Metric("wifi_raw", "count", "raw", "WiFi probe counter (test bench)", None, None),
    Metric("event", "", "text", "IFTTT event name, e.g. solar_reading"),
)

METRIC_BY_COLUMN: dict[str, Metric] = {m.column: m for m in METRICS}
CANONICAL_COLUMNS: tuple[str, ...] = tuple(m.column for m in METRICS)
NUMERIC_COLUMNS: tuple[str, ...] = tuple(m.column for m in METRICS if m.kind != "text")
TEXT_COLUMNS: tuple[str, ...] = tuple(m.column for m in METRICS if m.kind == "text")


@dataclass(frozen=True)
class HeaderMapping:
    """How one raw column index maps into the canonical schema."""

    index: int
    raw_name: str
    column: str | None
    confidence: str  # high | medium | low
    reason: str = ""


#: Exact header spellings seen in the archive -> canonical column.
#: Suffix matching handles solar/solar2/solar3, battery/battery2, currentA/B.
_EXACT: dict[str, str] = {
    "solar": "solar_v",
    "solar2": "solar2_v",
    "solar3": "solar3_v",
    "battery": "battery_v",
    "battery2": "battery2_v",
    "current": "current_a",
    "current2": "current2_a",
    "currenta": "current_a_chA",
    "currentb": "current_a_chB",
    "cura": "current_a_chA",
    "curb": "current_a_chB",
    "power": "power_w",
    "load": "load_v",
    "load_1": "load1_v",
    "load_2": "load2_v",
    "wind": "wind_v",
    "temp": "temp_c",
    "lipo": "lipo_v",
    "lipo2": "lipo2_v",
    "raw": "adc_raw",
    "voltage": "voltage_adc",
    "digital": "digital_adc",
    "dump": "dump_adc",
    "boot": "boot_count",
    "millis()": "millis_ms",
    "nix": "nix_raw",
    "wifi": "wifi_raw",
    # The probe sheet in `test` names this column for the field it times rather
    # than for the reading: `wifi_tx_ms` is a wifi transmit time, stored as the
    # station's raw wifi count. The old sheets said `wifi`.
    "wifi_tx_ms": "wifi_raw",
    "event": "event",
}

#: Spellings we recognise but refuse to auto-map, with the reason recorded.
_REFUSED: dict[str, str] = {
    "millis": "ambiguous between millis() and a raw ADC channel",
    "solar_reading": "event name, not a measurement",
    "current2a": "no header in the archive uses this spelling",
}


def map_header(index: int, raw_name: str) -> HeaderMapping:
    """Map one raw header cell to a canonical column.

    The time column (index 0) is handled by the caller.
    """
    key = (raw_name or "").strip().lower().replace(" ", "")

    if key in _EXACT:
        return HeaderMapping(index, raw_name, _EXACT[key], "high", "exact header match")

    if key in _REFUSED:
        return HeaderMapping(index, raw_name, None, "low", _REFUSED[key])

    # A header that is *already* a canonical column name maps to itself. The
    # repaired sheets spell their headers the way the collector names the fields
    # -- `test` now says `temp_c` where it used to say `temp` -- and there is no
    # ambiguity to resolve: the schema already has a column with that exact name.
    #
    # Placed after `_REFUSED` on purpose. A spelling listed there is refused for a
    # stated reason, and a passthrough rule must not quietly reinstate it.
    if key in METRIC_BY_COLUMN:
        return HeaderMapping(
            index, raw_name, key, "high", "header is already a canonical column name"
        )

    # Numeric-suffix rules, e.g. solar3 -> solar3_v, lipo2 -> lipo2_v.  Only the
    # suffixes that actually occur in the archive are accepted: an unrecognised
    # number is far more likely to be a different channel than a fourth solar
    # input, and guessing would put it in the wrong canonical column.
    if key.startswith("solar") and key[5:].isdigit() and key[5:] in ("2", "3"):
        return HeaderMapping(index, raw_name, f"solar{key[5:]}_v", "medium", "numeric suffix rule")
    if key.startswith("battery") and key[7:].isdigit() and key[7:] == "2":
        return HeaderMapping(index, raw_name, "battery2_v", "medium", "numeric suffix rule")
    if key.startswith("lipo") and key[4:].isdigit() and key[4:] == "2":
        return HeaderMapping(index, raw_name, "lipo2_v", "medium", "numeric suffix rule")
    if key.startswith("current") and key[7:].isdigit() and key[7:] == "2":
        return HeaderMapping(index, raw_name, "current2_a", "medium", "numeric suffix rule")

    if not key:
        return HeaderMapping(index, raw_name, None, "low", "empty header cell")

    return HeaderMapping(index, raw_name, None, "low", f"unrecognised header {raw_name!r}")


def map_headers(header: tuple[str, ...] | None) -> list[HeaderMapping]:
    """Map a whole header row, skipping the leading time column."""
    if not header:
        return []
    return [map_header(i, name) for i, name in enumerate(header) if i > 0 and name]


def build_row_mapping(header: tuple[str, ...] | None, n_columns: int) -> list[HeaderMapping]:
    """Header-driven mapping, or a placeholder per column when there is no header.

    In practice the caller passes a *donor's* header for a file that had none
    (see :func:`etl.build_db._resolve_donors`), so the placeholders below only
    apply to a file whose whole archive folder lacks a header.  In that case we
    ingest the timestamps and record that we do not know what the columns mean,
    rather than guessing: 305 of 364 files have no header of their own, and a
    wrong guess would be invisible in the output.
    """
    if header:
        return map_headers(header)
    return [
        HeaderMapping(i, "", None, "low", "no header row in this file or any sibling")
        for i in range(1, n_columns)
    ]

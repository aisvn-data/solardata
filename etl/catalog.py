"""What each of the eight stations collects, in the unit it is published in.

This module replaces the three things the 0.8 pipeline kept in separate places
and could never keep in agreement: the per-column plausibility bands in
``normalize/metrics.py``, the per-station unit overrides in ``config.py``, and
the confirmed scale factors in ``build_regimes.py``.  All three are now one
declaration, per (station, channel), here.

Why this had to be per station
------------------------------
A band is a claim about a sensor at a site, not about a column name.  ``solar_v``
is 0-29.8 V at ``aisvn``, 0-3532 at ``aisvn-solar`` (millivolts), 0-23860 at
``aisvn2`` (millivolts) and -984-34034 at ``maker-webhooks`` (millivolts).  The
0.8 pipeline carried one global band per column name, 0-60 V, and tested raw
millivolt cells against it.  The result was 631,252 ``out_of_range`` flags, of
which 627,000 were arithmetic on a unit, not a finding about the hardware.  The
single worst case was ``phumy2.current2_a``: 232 mA tested against a +/-50 A
band, so all 416,088 of the station's samples were marked out of range on a
sensor that was working perfectly.  A site decides whether a reading is
implausible, so the band has to belong to the site.

One unit, everywhere
--------------------
The 0.8 pipeline stored the raw sheet value and applied the confirmed scale only
in the rollups, which is why ``readings`` said one thing, the rollup said
another, and ``_rescale_oor_counts`` existed purely to stop the two disagreeing
about which values were out of range.  Here the confirmed scale is applied once,
on the way in, and the stored value, the rollup, the CSV, the Parquet file and
the browser all carry the same number in the same unit.  There is no second
place for a conversion to be applied or forgotten.

The scale is ``scale`` on a channel and it is applied to every reading of that
channel, for the whole record.  0.8 carried eight confirmed *windows* as well as
thirteen windowless confirmations; every one of them turned out to cover the
channel's entire extent (the seven ``aisvn`` windows were ``scale = 1.0``, kept
as a record of a recompile boundary that applies no conversion, and
``phumy2.current2_a`` starts at the first ``phumy2`` reading).  A window that
covers everything is a constant, and a constant is one number in a table rather
than a window in a regime detector.  The boundaries themselves are recorded in
the open questions at the bottom of this file and in ``docs/roadmap.md``.

Bands that would fire on most of a record are not bands
-------------------------------------------------------
``aisvn.lipo_v`` sits at 6.84 V for 14,107 of 77,526 readings and at
3.98-4.13 V for the rest.  Banded as a 1S LiPo cell (2.5-4.35 V), 20% of the
record flags as out of range, which makes the flag useless: a count that fires
on one sample in five cannot tell a contaminated aggregate from a normal day.
``BAND_FIRE_FRACTION`` exists to make that measurable, and a channel that
exceeds it must either carry a ``band_note`` saying why the fire is the finding
or must widen its band.  ``etl.audit`` enforces it on the real archive and the
build fails if it is broken.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = [
    "BAND_FIRE_FRACTION",
    "BY_ID",
    "STATIONS",
    "STATION_IDS",
    "Channel",
    "Correction",
    "Layout",
    "Station",
    "channel",
    "layout_for",
    "load_catalog",
    "published_columns",
    "station_for_source",
    "table_name",
]

# A band is meant to catch a contaminated aggregate.  If it fires on more than
# this fraction of a channel's own record it cannot do that job, so either the
# band is wrong for the station or the channel is bimodal and the band has to be
# widened until it is not firing on a mode rather than on an excursion.
BAND_FIRE_FRACTION = 0.01

Kind = Literal[
    "voltage",
    "current",
    "power",
    "temperature",
    "duration",
    "count",
    "raw",
    "digital",
    "text",
]

# Why a channel is kept out of the site's channel picker and out of the CSVs.
# The value is always still in the database: an exclusion is about what to
# publish, never about what to throw away.
#
#   not_measurement  the input is wired and logging, but is not a measurement
#                    of anything. `wind_v` reaches 12,784 V, which is not a
#                    generator output; `phumy2.power_w` is a pin the hardware
#                    was never implemented on, and 415,112 of 415,117 readings
#                    are exactly 0.
#   constant         the channel exists and logs, but never varies. Shipping an
#                    empty chart is worse than shipping nothing.
#   unresolved_unit  the station records the channel but nobody has established
#                    what unit. Publishing it would print a unit we know to be
#                    wrong; `etl.audit` will not let it into the site.
#   text_label       the cell holds a name rather than a number. `event` is the
#                    string "solar_reading"; a per-hour count of it is a
#                    constant, and a constant in a chart is a flat line at the
#                    floor.
ExcludeReason = Literal["not_measurement", "constant", "unresolved_unit", "text_label"]


@dataclass(frozen=True)
class Channel:
    """One thing one station collects.

    Attributes
    ----------
    name:
        Column name, unique within its station. This is the name in the station
        table, in the rollups, in the CSV and in the browser.
    label, description:
        What a human should call it. The label is what the channel picker
        prints; the description is the long form, shipped to the browser and
        shown beside the chart.
    kind:
        Drives the picker's grouping and the number of decimals it prints.
        ``count`` is a monotonic counter, ``raw`` an uncalibrated ADC channel,
        ``digital`` a 0/1 logic level, ``text`` a label rather than a number.
    unit:
        The unit of the stored value, i.e. after ``scale``. There is no second
        unit anywhere: not in the rollups, not in the CSV, not in the browser.
    raw_unit:
        What the sheet's own header implies the raw cell is in, kept for the
        report so a reader can see what was corrected on the way in. ``None``
        when the raw unit was never established.
    scale:
        Confirmed multiplier applied to the raw sheet cell at ingest. ``1.0``
        for the majority of channels. Never a guess: every non-1.0 value here
        was confirmed against the hardware and is listed in CHANGELOG.md.
    band:
        ``(lo, hi)`` in ``unit``, or ``None``. A channel with no band is never
        flagged, which is the correct treatment for a raw ADC count, an uptime
        counter, and for any channel whose unit is unresolved.
    band_note:
        Required, and non-empty, when the band fires on more than
        ``BAND_FIRE_FRACTION`` of the channel's own record. ``etl.audit``
        checks this against the real archive.
    stats:
        Which aggregates the rollups carry. ``("avg", "min", "max")`` for a
        quantity where all three are meaningful; ``("min", "max")`` and never
        a mean for an uptime counter, because a mean across a reboot averages
        two boot sessions into a number that never happened; ``()`` for a text
        column.
    publish:
        Whether the channel reaches ``public/data`` and the site.
    exclude:
        The ``ExcludeReason`` when ``publish`` is False. Empty when published.
    exclude_note:
        Why, in prose. This is the text the quality report prints for a channel
        the reader can see is missing, and the text a reviewer reads to decide
        whether the exclusion is still true.
    counter:
        True for ``boot_count`` and ``millis_ms``: the hardware's own view of
        its uptime. Never banded, never averaged.
    corrections:
        Declared, time-scoped changes to the value, applied after ``scale``.
        Empty for almost every channel, which is the point: 0.8 needed 22
        confirmed scale windows and 8 more for a single station, and every one of
        them turned out to cover the channel's entire extent. Two channels carry
        a correction and both say which hardware fault they undo and when.
    """

    name: str
    label: str
    kind: Kind
    description: str
    unit: str
    scale: float = 1.0
    raw_unit: str | None = None
    band: tuple[float | None, float | None] | None = None
    band_note: str = ""
    stats: tuple[str, ...] = ("avg", "min", "max")
    publish: bool = True
    exclude: ExcludeReason | None = None
    exclude_note: str = ""
    counter: bool = False
    corrections: tuple[Correction, ...] = ()

    @property
    def band_lo(self) -> float | None:
        return self.band[0] if self.band else None

    @property
    def band_hi(self) -> float | None:
        return self.band[1] if self.band else None

    @property
    def scaled(self) -> bool:
        return self.scale != 1.0

    def correct(self, value: float, ts_utc: str | None) -> float:
        """Apply every correction whose window covers this instant.

        ``ts_utc`` is ``None`` only where no clock is available, and in that case
        nothing is applied: guessing a period is what this mechanism exists to
        stop.
        """
        if ts_utc is None or not self.corrections:
            return value
        for correction in self.corrections:
            if correction.applies_at(ts_utc):
                value = correction.apply(value)
        return value

    @property
    def decimals(self) -> int:
        """Places the browser should print, derived from the unit not the kind."""
        if self.counter:
            return 0
        if self.kind in ("count", "raw"):
            return 0
        if self.kind == "digital":
            return 0
        # Voltages in millivolts still publish as volts, so the unit decides.
        return 3 if self.unit == "V" and self.band and (self.band[1] or 0) < 10 else 1

    def in_band(self, value: float) -> bool:
        if not self.band:
            return True
        lo, hi = self.band
        inside = (lo is None or value >= lo) and (hi is None or value <= hi)
        return inside

    def stat_column(self, stat: str) -> str:
        """The rollup column for one statistic, e.g. ``temp_c`` -> ``temp_c_max``."""
        return f"{self.name}_{stat}"


@dataclass(frozen=True)
class Layout:
    """One raw sheet shape a station is known to produce.

    The key is ``(station_id, n_columns)`` where ``n_columns`` counts the
    timestamp column.  The archive contains exactly nine such pairs and no
    station has two layouts of the same width, so width alone identifies a
    layout -- which is what removes 0.8's donor-inheritance machinery and its
    90%-of-the-archive failure mode.  ``make check`` asserts the width is
    unique per station and the build fails on an undeclared width, so a new
    layout cannot be ingested silently.
    """

    n_columns: int
    header: tuple[str, ...]
    channels: tuple[str, ...]
    n_files: int
    note: str = ""

    def __post_init__(self) -> None:
        if len(self.header) != self.n_columns - 1 or len(self.channels) != self.n_columns - 1:
            raise ValueError(
                f"layout of width {self.n_columns} declares "
                f"{len(self.header)} headers and {len(self.channels)} channels"
            )


@dataclass(frozen=True)
class Station:
    """One physical instrument.

    ``source_dirs`` are the folders under ``data/raw`` holding chunks of this
    station.  ``phumy2`` has three because Google Sheets split the sheet at
    2000 rows and the export was chunked per sheet; they are one instrument, not
    three.
    """

    station_id: str
    display_name: str
    location: str
    tz: str
    source_dirs: tuple[str, ...]
    applet: str
    production: bool
    notes: str
    layouts: tuple[Layout, ...]
    channels: tuple[Channel, ...]
    open_questions: tuple[str, ...] = ()
    published_group: str = "solar production"

    @property
    def table(self) -> str:
        return table_name(self.station_id)

    @property
    def by_name(self) -> dict[str, Channel]:
        return {c.name: c for c in self.channels}

    @property
    def published(self) -> tuple[Channel, ...]:
        return tuple(c for c in self.channels if c.publish)

    @property
    def by_width(self) -> dict[int, Layout]:
        return {layout.n_columns: layout for layout in self.layouts}

    def layout_for(self, n_columns: int) -> Layout | None:
        return self.by_width.get(n_columns)

    def channel(self, name: str) -> Channel:
        return self.by_name[name]


def table_name(station_id: str) -> str:
    """The station's table name. Hyphens are not legal unquoted in SQL."""
    return "s_" + station_id.replace("-", "_")


#: Why an uptime counter has no plausible range. Shared rather than repeated
#: across the six channels that are one, because a reader meeting it on
#: `boot_count` and again on `millis_ms` should read the same sentence, and
#: because ``etl.audit`` requires a bandless channel to say why and this is what
#: it says.
COUNTER_BAND_NOTE = (
    "No band, deliberately. A counter is not a physical quantity: it starts at 1 "
    "after a reboot and climbs, so the only thing a range could say is the "
    "station's uptime, which is not what the site draws it for. It is published "
    "because the logger's own view of its uptime is the only channel recording "
    "that, and a bucket whose minimum is 1 restarted."
)


@dataclass(frozen=True)
class Correction:
    """A declared change to a channel's value over a window of time.

    A hardware fault that starts at a known instant and never ends is not a
    property of the channel, it is a property of a period -- so it is declared
    here, with the instant, rather than smeared into the channel's `scale` or
    quietly averaged away.

    Applied **after** `scale`, once, at ingest, in declaration order, and only
    when `from_ts <= ts_utc < to_ts`.  Half-open, because a boundary that both
    windows claim is a boundary that is wrong twice.

    This is the opposite of what 0.8 did.  0.8 detected scale changes by
    proposing windows from the shape of the data and applying the confirmed ones
    to the rollups, which is where ``phumy2.current2_a``'s 232 mA and a
    ``battery_v`` band in the wrong unit both came from.  Here the instant is
    given by the collector, the arithmetic is a single multiply or add, and it
    touches the stored value rather than a downstream copy of it.

    `op` is deliberately only ``add`` and ``factor``.  A correction that needs a
    conditional, an absolute value or a different branch is a symptom that the
    period is being described wrongly, and the way to find that out is for the
    declaration to be impossible to write.
    """

    from_ts: str
    to_ts: str | None
    op: Literal["add", "factor"]
    value: float
    note: str

    def applies_at(self, ts_utc: str) -> bool:
        return self.from_ts <= ts_utc and (self.to_ts is None or ts_utc < self.to_ts)

    def apply(self, value: float) -> float:
        return round(value + self.value, 9) if self.op == "add" else round(value * self.value, 9)

    @property
    def window(self) -> str:
        return f"{self.from_ts} .. {self.to_ts or 'open'}"


def load_catalog(
    normalization_path: Path | None = None,
    curation_path: Path | None = None,
) -> tuple[Station, ...]:
    """Load stations from data/config/normalization.json and curation.json."""
    base_dir = Path(__file__).resolve().parent.parent / "data" / "config"
    norm_file = normalization_path or (base_dir / "normalization.json")
    cur_file = curation_path or (base_dir / "curation.json")

    with open(norm_file, encoding="utf-8") as f:
        norm_data = json.load(f)
    with open(cur_file, encoding="utf-8") as f:
        cur_data = json.load(f)

    stations = []
    for st_id, st_norm in norm_data["stations"].items():
        st_cur = cur_data["stations"].get(st_id, {})

        layouts = [
            Layout(
                n_columns=layout_data["n_columns"],
                header=tuple(layout_data["header"]),
                channels=tuple(layout_data["channels"]),
                n_files=layout_data["n_files"],
                note=layout_data.get("note", ""),
            )
            for layout_data in st_norm["layouts"]
        ]

        cur_channels = st_cur.get("channels", {})
        channels = []
        for ch_name, ch_norm in st_norm["channels"].items():
            ch_cur = cur_channels.get(ch_name, {})

            corrections = [
                Correction(
                    from_ts=corr["from_ts"],
                    to_ts=corr.get("to_ts"),
                    op=corr["op"],
                    value=float(corr["value"]),
                    note=corr.get("note", ""),
                )
                for corr in ch_norm.get("corrections", [])
            ]

            raw_band = ch_cur.get("band")
            band = tuple(raw_band) if raw_band is not None else None
            channels.append(
                Channel(
                    name=ch_norm["name"],
                    label=ch_cur.get("label", ch_norm["name"]),
                    kind=ch_norm["kind"],
                    description=ch_cur.get("description", ""),
                    unit=ch_norm["unit"],
                    scale=float(ch_norm.get("scale", 1.0)),
                    raw_unit=ch_norm.get("raw_unit"),
                    band=band,
                    band_note=ch_cur.get("band_note", ""),
                    stats=tuple(ch_cur.get("stats", ("avg", "min", "max"))),
                    publish=bool(ch_cur.get("publish", True)),
                    exclude=ch_cur.get("exclude"),
                    exclude_note=ch_cur.get("exclude_note", ""),
                    counter=bool(ch_norm.get("counter", False)),
                    corrections=tuple(corrections),
                )
            )

        station = Station(
            station_id=st_norm["station_id"],
            display_name=st_norm["display_name"],
            location=st_norm["location"],
            tz=st_norm["tz"],
            source_dirs=tuple(st_norm["source_dirs"]),
            applet=st_norm["applet"],
            production=bool(st_norm["production"]),
            published_group=st_norm.get("published_group", "solar production"),
            notes=st_norm.get("notes", ""),
            layouts=tuple(layouts),
            channels=tuple(channels),
            open_questions=tuple(st_cur.get("open_questions", ())),
        )
        stations.append(station)

    return tuple(stations)


STATIONS: tuple[Station, ...] = load_catalog()
BY_ID: dict[str, Station] = {s.station_id: s for s in STATIONS}
STATION_IDS: tuple[str, ...] = tuple(s.station_id for s in STATIONS)
BY_SOURCE_DIR: dict[str, Station] = {d: s for s in STATIONS for d in s.source_dirs}
NON_PRODUCTION: frozenset[str] = frozenset(s.station_id for s in STATIONS if not s.production)

AISVN = BY_ID["aisvn"]
AISVN2 = BY_ID["aisvn2"]
AISVN_SOLAR = BY_ID["aisvn-solar"]
MAKER_WEBHOOKS = BY_ID["maker-webhooks"]
PHUMY2 = BY_ID["phumy2"]
SOLAR_2020_05 = BY_ID["solar-2020-05"]
TEST = BY_ID["test"]
VOLTAGE_PHUMY = BY_ID["voltage-phumy"]


def station_for_source(source: str | object) -> Station | None:
    """Resolve a directory name, file name, or station_id to its Station."""
    from pathlib import Path

    p = Path(str(source))
    candidates = (p.stem, p.name, p.stem.lower(), p.name.lower())
    for candidate in candidates:
        if candidate in BY_SOURCE_DIR:
            return BY_SOURCE_DIR[candidate]
        if candidate in BY_ID:
            return BY_ID[candidate]
        alt = candidate.replace("-", "_")
        if alt in BY_SOURCE_DIR:
            return BY_SOURCE_DIR[alt]
        if alt in BY_ID:
            return BY_ID[alt]
        alt2 = candidate.replace("_", "-")
        if alt2 in BY_SOURCE_DIR:
            return BY_SOURCE_DIR[alt2]
        if alt2 in BY_ID:
            return BY_ID[alt2]
    return None


def channel(station_id: str, name: str) -> Channel:
    return BY_ID[station_id].channel(name)


def layout_for(station_id: str, n_columns: int) -> Layout | None:
    return BY_ID[station_id].layout_for(n_columns)


def published_columns(station_id: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The CSV header for one station, split into metadata and measurements.

    Every column a station's CSV carries is a channel that station collects, in
    that station's unit.  A station that logs no ``power_w`` has no
    ``power_w_avg`` column at all, so there is nothing for a reader to mistake
    for a measurement, and the browser no longer has to discover which columns
    are real.
    """
    station = BY_ID[station_id]
    meta = ("ts",)
    values: list[str] = []
    for ch in station.published:
        for stat in ch.stats:
            values.append(ch.stat_column(stat))
    return meta, tuple(values)

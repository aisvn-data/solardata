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

from dataclasses import dataclass
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
    "published_columns",
    "table_name",
]

# A band is meant to catch a contaminated aggregate.  If it fires on more than
# this fraction of a channel's own record it cannot do that job, so either the
# band is wrong for the station or the channel is bimodal and the band has to be
# widened until it is not firing on a mode rather than on an excursion.
BAND_FIRE_FRACTION = 0.01

Kind = Literal["voltage", "current", "power", "temperature", "count", "raw", "digital", "text"]

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


# ---------------------------------------------------------------------------
# aisvn -- AISVN #1.  The only station with a lead-acid bank, a real current
# sensor and a temperature probe, and the one the collector repaired twice.
# ---------------------------------------------------------------------------
# aisvn -- AISVN #1.  The only station with a lead-acid bank, a real current
# sensor and a temperature probe, and the one the collector repaired twice.
# ---------------------------------------------------------------------------

AISVN = Station(
    station_id="aisvn",
    display_name="AISVN #1",
    location="Nha Be, Ho Chi City, Vietnam",
    tz="Asia/Ho_Chi_Minh",
    source_dirs=("aisvn",),
    applet="IFTTT_aisvn",
    production=True,
    notes=(
        "11-channel logger. The applet was recompiled twice and the collector "
        "converted the sheet at source, so the whole record is volts and amps "
        "with no scale window to apply -- except for two hardware faults the "
        "collector dates exactly: the current channel gained a permanent offset "
        "at 2020-08-24 18:42 local, and the power channel's output was inverted "
        "and four times too large from the same instant. Both are declared as "
        "corrections rather than absorbed into the channel."
    ),
    layouts=(
        Layout(
            n_columns=11,
            header=(
                "solar",
                "battery",
                "current",
                "power",
                "load",
                "wind",
                "temp",
                "solar2",
                "LiPo",
                "boot",
            ),
            channels=(
                "solar_v",
                "battery_v",
                "current_a",
                "power_w",
                "load_v",
                "wind_v",
                "temp_c",
                "solar2_v",
                "lipo_v",
                "boot_count",
            ),
            n_files=39,
            note=(
                "One layout for all 39 files. 0.8.0 repaired the pre-recompile "
                "files at source, so the header now reads power, load here too "
                "and the width never changes."
            ),
        ),
    ),
    channels=(
        Channel(
            name="solar_v",
            label="Solar",
            kind="voltage",
            description=(
                "Collector input 1, the panel the station was built around. The "
                "input saturates at 29.8 V, so readings above 25 V are the rail "
                "and not the panel."
            ),
            unit="V",
            band=(0.0, 25.0),
            band_note=(
                "0.8 used 0-60 V for every station's solar_v, which flagged "
                "nothing here because 0.8's scale never reached the stored value. "
                "0.9 drew it at 25 V, below this station's 29.8 V saturation, so "
                "the readings on the rail are marked. They are the readings that "
                "coincide with the collector's reconfiguration window "
                "(2020-10-23 to 10-30), which is flagged separately and is the "
                "more specific explanation."
            ),
        ),
        Channel(
            name="solar2_v",
            label="Solar 2",
            kind="voltage",
            description=(
                "Second collector input. Sits on a rail at exactly 19.5 V for "
                "14,107 of 77,526 readings."
            ),
            unit="V",
            band=(0.0, 15.0),
            band_note=(
                "15 V, below this channel's own 19.5 V saturation, so the rail is "
                "flagged -- and that is 18.2% of the record, which is why it needs "
                "a note rather than silence. 0.8 carried one global 0-60 V band "
                "per column name, which both missed this and flagged the "
                "millivolt stations. Whether 19.5 V is a saturated input or a real "
                "ceiling is a question for the collector: 0-15 V is the ceiling "
                "of a two-cell series string and a 19.5 V rail is a plausible ADC "
                "top, and the archive cannot tell them apart."
            ),
        ),
        Channel(
            name="battery_v",
            label="Battery",
            kind="voltage",
            description=(
                "A 12 V lead-acid car battery, confirmed by the collector. Rests "
                "at 12.4-12.8 V, charges to 14.4 V, reads about 10.1 V flat."
            ),
            unit="V",
            band=(9.0, 16.0),
            band_note=(
                "Fires on 1,717 of 77,526 readings (2.2%) and that is the "
                "finding, not a false positive: the bank reaches 29.8 V in 2020 "
                "and 17.9 V in 2021, which is a second pack, a mis-scaled input "
                "or a band wrong for what is installed. The collector confirmed "
                "a lead pack, which is what makes 29.8 V a question rather than "
                "an explanation. See open question 1 in AGENTS.md."
            ),
        ),
        Channel(
            name="current_a",
            label="Current",
            kind="current",
            description=(
                "Primary current channel. On 2020-08-24 at 18:42 local the "
                "electronics latched into a state that read about 6.6 A low, so "
                "almost every later reading is negative."
            ),
            unit="A",
            band=(0.0, 3.0),
            corrections=(
                Correction(
                    "2020-08-24T11:42:00Z",
                    "2020-10-23T00:00:00Z",
                    "add",
                    6.6,
                    "The collector's dated fault: from 18:42 local on 2020-08-24 "
                    "the channel records 6.6 A below the truth until the logger "
                    "was reconfigured. The instant is the first reading that "
                    "latches -- 11:41 still reads 0.02, 11:42 reads exactly "
                    "-6.0 -- so the boundary is the second, not the hour.",
                ),
                Correction(
                    "2020-10-30T00:00:00Z",
                    None,
                    "add",
                    6.6,
                    "The same fault again after the reconfiguration window "
                    "(2020-10-23 to 10-30, which the collector says describes a "
                    "half-built logger). The channel is back on the -6.0 A offset "
                    "from 2020-10-30. Two windows rather than one because the "
                    "correction is *not* applied inside the reconfiguration "
                    "window, where the channel reads near zero rather than -6.0 "
                    "and adding 6.6 to it would invent 6.6 A of phantom load.",
                ),
            ),
            band_note=(
                "0-3 A, the collector's figure for this panel. It fires on the "
                "1,823 readings inside the 2020-10-23 to 10-30 reconfiguration "
                "window, where the raw channel reads near zero and the +6.6 A "
                "correction is deliberately not applied -- they are half-built "
                "hardware, flagged as such by BAD_WINDOWS, and their values are "
                "not measurements. Nothing outside that window is flagged. Note "
                "also that the corrected current after 2020-08-24 varies by only "
                "about 0.3 A and does not track solar, so the correction removes "
                "the sign error but does not restore a usable signal; that is "
                "what the collector's request for more insight at higher "
                "resolution is about."
            ),
        ),
        Channel(
            name="power_w",
            label="Power",
            kind="power",
            description=(
                "Reported power. From 2020-08-24 18:42 local the output is "
                "inverted and four times too large, so almost every later reading "
                "is negative."
            ),
            unit="W",
            band=(0.0, 50.0),
            corrections=(
                Correction(
                    "2020-08-24T11:42:00Z",
                    "2020-10-23T00:00:00Z",
                    "factor",
                    -0.25,
                    "The collector's dated fault, same instant as the current "
                    "channel's offset: the output is inverted and four times too "
                    "large, so multiplying by -0.25 undoes both. The corrected "
                    "curve tracks solar exactly -- 0 W at night, 34.5 W at noon -- "
                    "which is the evidence that -0.25 and not +0.25 is the right "
                    "sign.",
                ),
                Correction(
                    "2020-10-30T00:00:00Z",
                    None,
                    "factor",
                    -0.25,
                    "The same fault again after the reconfiguration window. Paused "
                    "inside it for the same reason as the current channel: the "
                    "readings there come from a half-built logger and are flagged "
                    "rather than corrected.",
                ),
            ),
            band_note=(
                "0-50 W, the collector's figure. Fires on 217 readings before the "
                "fault (40 slightly negative, 177 above 50 W) and on the "
                "reconfiguration window afterwards, where the -0.25 factor is not "
                "applied. Nothing else is flagged, and the corrected record runs "
                "0-34.5 W, which is a plausible output for this panel and tracks "
                "the solar curve. 0.8 asserted energy_wh as "
                "avg_power * n_samples * 2 / 3600 for every station, which "
                "multiplied this channel by an assumed cadence; there is no such "
                "column in 0.9."
            ),
        ),
        Channel(
            name="load_v",
            label="Load",
            kind="voltage",
            description=(
                "Load / dump rail. The collector reports 10-12 V with a load "
                "switched on and 0 with none present, saturating at 29.7 V."
            ),
            unit="V",
            band=(0.0, 20.0),
            band_note=(
                "20 V, below the rail. It fires on the same readings as solar_v "
                "and for the same reason -- the 2020-10-23 to 10-30 "
                "reconfiguration window -- which is a useful cross-check: two "
                "independent inputs flag the same 1,612 readings. The rail's true "
                "full scale is still unrecorded, so this is the collector's figure "
                "rather than a measured ceiling. Open question 6."
            ),
        ),
        Channel(
            name="wind_v",
            label="Wind",
            kind="power",
            description=(
                "The generator's output, logged as the single voltage reading of "
                "the three phases combined. Confirmed by the collector as a power "
                "measurement in watts, which is why it is charted and was not in "
                "0.9.0."
            ),
            unit="W",
            band=(0.0, 50.0),
            band_note=(
                "0-50 W, the collector's estimate for this generator. Nothing is "
                "flagged: the record runs 0-29.8 W. 0.9.0 excluded this channel "
                "because 12,784 V is not a plausible *voltage*; that reading was "
                "right and the unit was wrong, and the collector has now said so."
            ),
        ),
        Channel(
            name="temp_c",
            label="Temperature",
            kind="temperature",
            description=(
                "Ambient temperature probe. The station stands in shadow, so it "
                "reads the air rather than the panel."
            ),
            unit="degC",
            band=(0.0, 40.0),
            band_note=(
                "0-40 degC, the collector's ceiling for a probe in shadow at this "
                "site. 0.9.0 used 60 degC and flagged 119 readings; at 40 it flags "
                "the readings above it and nothing else, which is the level the "
                "hardware can actually reach here. The archive cannot say how much "
                "of the record is a shaded reading and how much a heated one, and "
                "December 2021 has several sources, so a reading above 40 degC is "
                "a question rather than a fault."
            ),
        ),
        Channel(
            name="lipo_v",
            label="LiPo",
            kind="voltage",
            description=("Single-cell LiPo pack. 14,107 of 77,526 readings sit at exactly 6.84 V."),
            unit="V",
            band=(0.0, 5.0),
            band_note=(
                "0-5 V, the collector's figure. It fires on 18.2% of the record, "
                "which is why it needs a note rather than silence, and the note is "
                "that 6.84 V is a single exact value across 14,107 readings: a "
                "plateau, which is either a saturated input or a pack being read "
                "through a divider. 0.9.0 bounded it at 0-8.7 V specifically to stop "
                "this firing, and the collector has now said the ceiling is 5 V, so "
                "it fires again and the 18.2% is the finding. Open question 2."
            ),
        ),
        Channel(
            name="boot_count",
            label="Boot counter",
            kind="count",
            description=(
                "The logger's own monotonic submission counter, reset by a "
                "reboot. It reaches 21,660 without a reset, about 75 days at the "
                "archive's cadence, so a 20,000 ceiling would not have been reached."
            ),
            unit="count",
            band_note=COUNTER_BAND_NOTE,
            stats=("min", "max"),
            counter=True,
        ),
    ),
    open_questions=(
        "battery_v reaches 29.8 V in 2020 and 17.9 V in 2021 against a confirmed "
        "12 V lead-acid pack. A second pack, a mis-scaled input, or a band "
        "wrong for what is installed.",
        "lipo_v sits on a rail at exactly 6.84 V for 18% of the record, and "
        "solar2_v on a rail at exactly 19.5 V. Saturation, a divider, or a real "
        "ceiling -- the archive cannot tell them apart.",
        "After the 2020-08-24 fault the current channel's variation is only about "
        "0.3 A and does not track solar, so the +6.6 A correction removes the sign "
        "error without restoring a usable signal. The power channel's does track "
        "solar, which is why one correction looks like a repair and the other "
        "looks like a patch.",
        "temp_c has more than one source, at least in December 2021. Which "
        "readings are which, and what the shaded ceiling really is, is asked of "
        "the collector.",
        "load_v's full scale is unresolved, and the rail's 0 V state changes "
        "behaviour on 2020-07-10 with nothing recorded to explain it.",
        "No readings at all between 2020-10-25 and 2020-11-04, and September "
        "2020 has 12 readings. The collector confirmed the collector was down "
        "and no data was lost in the export, so there is nothing to fix.",
    ),
)


# ---------------------------------------------------------------------------
# aisvn2 -- AISVN #2.  Everything in millivolts, and a current channel whose
# scale steps by ~200x partway through the record without a recompile.
# ---------------------------------------------------------------------------

AISVN2 = Station(
    station_id="aisvn2",
    display_name="AISVN #2",
    location="Nha Be, Ho Chi City, Vietnam",
    tz="Asia/Ho_Chi_Minh",
    source_dirs=("aisvn2",),
    applet="IFTTT_aisvn2",
    production=True,
    notes=(
        "8-channel logger recording millivolts throughout. The LiPo channel is a "
        "2S pack, not the 1S cell the archive once assumed."
    ),
    layouts=(
        Layout(
            n_columns=8,
            header=("solar3", "battery2", "currentA", "currentB", "LiPo2", "load", "boot"),
            channels=(
                "solar3_v",
                "battery2_v",
                "current_a_chA",
                "current_a_chB",
                "lipo2_v",
                "load_v",
                "boot_count",
            ),
            n_files=79,
            note="One layout for all 79 files. 70 of them are headerless.",
        ),
    ),
    channels=(
        Channel(
            name="solar3_v",
            label="Solar",
            kind="voltage",
            description="Third collector input. The only solar channel this station has.",
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(0.0, 30.0),
            band_note=(
                "Recorded 0-23,860 mV, 16.7 V at the 99th percentile. 0.8 stored "
                "the millivolts and tested them against a 0-60 V band, so every "
                "reading above 60 mV flagged."
            ),
        ),
        Channel(
            name="battery2_v",
            label="Battery",
            kind="voltage",
            description="Second bank, in millivolts, resting around 12.2 V.",
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(9.0, 16.0),
            band_note=(
                "Fires on 1,070 of 164,098 readings (0.65%), all of them below "
                "9 V. A lead-acid bank at 0.46 V is a disconnected or failed pack, "
                "which is what the flag is for. 0.8's override of 0-8,000 mV was "
                "not applied to the other half of the pair."
            ),
        ),
        Channel(
            name="current_a_chA",
            label="Current A",
            kind="current",
            description=(
                "Channel A. The scale steps by roughly 200x between 2021-04 and "
                "2021-10, where it pins at exactly 1240 for 1,207 readings, and "
                "the factor is not a clean power of ten. No recompile is "
                "recorded at the change, so no scale is applied and no band is "
                "asserted. The values are still published, because the station "
                "does collect them; what is missing is the unit, and the picker "
                "therefore prints none."
            ),
            unit="",
            raw_unit=None,
            band=(None, 500.0),
            band_note=(
                "Upper limit 500, from the collector; no lower bound, because "
                "55% of this channel's readings are negative and a floor at zero "
                "would flag more than half the record for a sensor that is "
                "working. It fires on 2,915 of 153,770 readings (1.9%), all of "
                "them in the 2021-10 and 2021-11 stretch where the channel pins "
                "at 1240 -- the ~200x step the collector has not yet explained. "
                "The unit is still unresolved, so no lower bound is asserted and "
                "no scale is applied. Open question 1."
            ),
        ),
        Channel(
            name="current_a_chB",
            label="Current B",
            kind="current",
            description=(
                "Channel B, same applet and the same unresolved scale as channel "
                "A: 0-1,618 with a median of 64, and a bulk between -267 and 196."
            ),
            unit="",
            raw_unit=None,
            band=None,
            band_note=(
                "Upper limit 500, from the collector, and no lower bound: 17% of "
                "this channel's readings are negative. It fires on 48 of 164,097 "
                "readings, all of them part of the same 2021-10/11 step. Open "
                "question 1."
            ),
        ),
        Channel(
            name="lipo2_v",
            label="LiPo 2",
            kind="voltage",
            description=("2S LiPo pack, pinned at 7.097 V for 161,790 of 164,097 readings."),
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(0.0, 8.7),
            band_note=(
                "Banded 0-8.7 V, the full range of a 2S pack, rather than the "
                "2.5-4.35 V of a 1S cell. A 1S band fires on 4,231 readings that "
                "are simply a two-cell pack. 0.8 carried a 0-8,000 mV override "
                "in config.CHANNEL_UNITS and read it in three places; it is a "
                "number on the channel now."
            ),
        ),
        Channel(
            name="load_v",
            label="Load",
            kind="digital",
            description=(
                "A logic level, not a voltage: the column is 0 or 1 and nothing "
                "else, with 133,138 of 164,097 readings at 0. The header says "
                "'load' and 0.8 stored it as load_v banded 0-60 V, where it "
                "could never be out of range."
            ),
            unit="",
            raw_unit=None,
            band=(0.0, 1.0),
            band_note="0-1 is the channel's whole domain, so nothing is flagged.",
        ),
        Channel(
            name="boot_count",
            label="Boot counter",
            kind="count",
            description="The logger's own monotonic submission counter, reset by a reboot.",
            unit="count",
            band_note=COUNTER_BAND_NOTE,
            stats=("min", "max"),
            counter=True,
        ),
    ),
    open_questions=(
        "current_a_chA and current_a_chB step by roughly 200x between 2021-04 and "
        "2021-10, with no recompile recorded at the boundary. Not a clean factor, "
        "so no scale is applied and no band is asserted.",
        "lipo2_v is a 2S pack and pins at 7.097 V. Whether the pin is the pack's "
        "own plateau or a saturated input is not recorded.",
    ),
)


# ---------------------------------------------------------------------------
# aisvn-solar -- the archived applet.  Superseded by aisvn three weeks in.
# ---------------------------------------------------------------------------

AISVN_SOLAR = Station(
    station_id="aisvn-solar",
    display_name="AISVN Solar (archived applet)",
    location="Nha Be, Ho Chi City, Vietnam",
    tz="Asia/Ho_Chi_Minh",
    source_dirs=("AISVN_Solar",),
    applet="IFTTT_AISVN_Solar",
    production=True,
    notes=(
        "Superseded by aisvn. May 2020 only. 9 columns, of which two load rails "
        "have no established unit and two inputs never move."
    ),
    layouts=(
        Layout(
            n_columns=9,
            header=("solar", "battery", "load_1", "load_2", "LiPo", "wind", "dump", "boot"),
            channels=(
                "solar_v",
                "battery_v",
                "load1_v",
                "load2_v",
                "lipo_v",
                "wind_v",
                "dump_adc",
                "boot_count",
            ),
            n_files=7,
            note=(
                "One layout for all 7 files, all of which have a header. Carries "
                "a second column block to the right of the primary one."
            ),
        ),
    ),
    channels=(
        Channel(
            name="solar_v",
            label="Solar",
            kind="voltage",
            description=(
                "Collector input, in millivolts. Maxes at 3,532 mV where a "
                "photovoltaic panel should reach 15-20 V open circuit."
            ),
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(0.0, 60.0),
            band_note=(
                "Nothing is flagged. 3.5 V is inside a 0-60 V band and the "
                "station's own ceiling is not ours to tighten: either the input "
                "is not a panel or the station never saw a real panel voltage. "
                "The collector is asked. Open question 2."
            ),
        ),
        Channel(
            name="battery_v",
            label="Battery",
            kind="voltage",
            description=(
                "Bank behind a 50/50 divider, so the raw cell is multiplied by 2 "
                "on the way in and the result is still millivolts."
            ),
            unit="V",
            raw_unit="mV",
            scale=0.002,
            band=(0.0, 5.1),
            band_note=(
                "0-5.1 V, the documented 0-5,100 mV ceiling. Fires on 8 of 13,788 "
                "readings, all of them between 5.1 and 5.148 V -- over the "
                "divider's documented rail by up to 48 mV. A real boundary "
                "finding, which is what a band is for."
            ),
        ),
        Channel(
            name="load1_v",
            label="Load 1",
            kind="voltage",
            description=(
                "First load rail. Recorded 0-1,598, which cannot be volts: at "
                "that magnitude a load rail is implausible by three orders, and "
                "millivolts would put it at a plausible 0-1.6 V. No confirmation "
                "exists, so none is applied."
            ),
            unit="",
            raw_unit=None,
            band_note=(
                "No band, because there is no unit to band it in. A band is a "
                "range on a number with a dimension; this channel's dimension is "
                "the thing that is unknown, so any number written here would be "
                "a guess dressed as a criterion."
            ),
            stats=(),
            publish=False,
            exclude="unresolved_unit",
            exclude_note=(
                "Excluded from the site and the CSVs. The header says load_1 and "
                "0.8 stored it as volts, which would have published 1,598 V. The "
                "millivolt reading is likely but unconfirmed, and publishing a "
                "guessed unit is the same error as publishing a guessed scale. "
                "The values are in the database. If the collector confirms "
                "millivolts this becomes scale 0.001 and unit V."
            ),
        ),
        Channel(
            name="load2_v",
            label="Load 2",
            kind="voltage",
            description="Second load rail. Recorded 0-3,026. Same unresolved unit as load 1.",
            unit="",
            raw_unit=None,
            band_note=(
                "No band, because there is no unit to band it in. A band is a "
                "range on a number with a dimension; this channel's dimension is "
                "the thing that is unknown, so any number written here would be "
                "a guess dressed as a criterion."
            ),
            stats=(),
            publish=False,
            exclude="unresolved_unit",
            exclude_note=(
                "Excluded from the site and the CSVs, for the same reason as "
                "load 1: 3,026 V is not a rail and 3.026 V is a guess."
            ),
        ),
        Channel(
            name="lipo_v",
            label="LiPo",
            kind="voltage",
            description=(
                "Single-cell pack, pinned at 3,532 mV for 13,784 of 13,788 "
                "readings. 3,532 is this applet's ADC rail and was 0.8's "
                "CLIP_CANDIDATES entry."
            ),
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(0.0, 5.0),
            band_note=(
                "0-5 V, the collector's figure, which replaces the 2.5-4.35 V of "
                "a 1S cell. Nothing is flagged: 13,784 of the 13,788 readings sit "
                "at exactly 3.532 V, this applet's ADC rail, and the four "
                "0.9.0 flagged were all below 2.5 V. Whether the plateau is a "
                "full pack or a saturated input is not recorded."
            ),
        ),
        Channel(
            name="wind_v",
            label="Wind",
            kind="voltage",
            description="A wired input that is exactly 0 for all 13,788 readings.",
            # No unit and no scale, because the input never varies: 0 is 0 in
            # millivolts and in volts, so there is nothing to convert and nothing
            # to convert *to*. Declaring mV here would be a claim about a channel
            # that recorded one value.
            unit="",
            band=None,
            band_note=(
                "No band. The channel records exactly one value, so every "
                "plausibility question about it is settled by the value itself; "
                "a band would add a number next to a constant and invite the "
                "reader to compare them."
            ),
            stats=(),
            publish=False,
            exclude="constant",
            exclude_note=(
                "Excluded because it never varies, not because of what it "
                "measures. etl.audit re-checks the constancy on every build, so "
                "this exclusion cannot go stale."
            ),
        ),
        Channel(
            name="dump_adc",
            label="Dump ADC",
            kind="raw",
            description="Uncalibrated dump-channel ADC reading, exactly 0 for all 13,788 readings.",
            unit="count",
            band=None,
            band_note=(
                "No band. An uncalibrated ADC count is not a physical quantity, "
                "and it is constant here anyway -- see the exclusion note."
            ),
            stats=(),
            publish=False,
            exclude="constant",
            exclude_note=(
                "Excluded because it never varies. etl.audit re-checks the "
                "constancy on every build."
            ),
        ),
        Channel(
            name="boot_count",
            label="Boot counter",
            kind="count",
            description="The logger's own monotonic submission counter, reset by a reboot.",
            unit="count",
            band_note=COUNTER_BAND_NOTE,
            stats=("min", "max"),
            counter=True,
        ),
    ),
    open_questions=(
        "solar_v maxes at 3,532 mV. A photovoltaic panel should reach 15-20 V "
        "open circuit, so either the input is not a panel or the station never "
        "saw a real panel voltage.",
        "load1_v and load2_v are recorded in an unestablished unit. Millivolts "
        "would make them plausible; nothing confirms it.",
        "lipo_v and solar_v both pin at 3,532, which is this applet's ADC rail.",
    ),
)


# ---------------------------------------------------------------------------
# maker-webhooks -- the other archived applet.  June 2020, heavy sentinel use,
# and a submission counter that resets every 16 readings.
# ---------------------------------------------------------------------------

MAKER_WEBHOOKS = Station(
    station_id="maker-webhooks",
    display_name="Maker Webhooks (archived applet)",
    location="Nha Be, Ho Chi City, Vietnam",
    tz="Asia/Ho_Chi_Minh",
    source_dirs=("Maker_Webhooks_Events",),
    applet="IFTTT_Maker_Webhooks_Events",
    production=True,
    notes=(
        "Superseded by aisvn. June 2020 only. Two sheet widths, 10 and 11 "
        "columns, and the 11-column files carry a second solar input."
    ),
    layouts=(
        Layout(
            n_columns=10,
            header=("solar", "battery", "curA", "curB", "load", "wind", "dump", "LiPo", "boot"),
            channels=(
                "solar_v",
                "battery_v",
                "current_a_chA",
                "current_a_chB",
                "load_v",
                "wind_v",
                "dump_adc",
                "lipo_v",
                "boot_count",
            ),
            n_files=3,
            note="The earlier width. No solar2 column, so solar2_v is absent from these files.",
        ),
        Layout(
            n_columns=11,
            header=(
                "solar",
                "battery",
                "curA",
                "curB",
                "load",
                "wind",
                "dump",
                "solar2",
                "LiPo",
                "boot",
            ),
            channels=(
                "solar_v",
                "battery_v",
                "current_a_chA",
                "current_a_chB",
                "load_v",
                "wind_v",
                "dump_adc",
                "solar2_v",
                "lipo_v",
                "boot_count",
            ),
            n_files=2,
            note=(
                "The later width, which added solar2 between dump and LiPo. This "
                "is the only station in the archive with two layouts of different "
                "widths, and width alone tells them apart."
            ),
        ),
    ),
    channels=(
        Channel(
            name="solar_v",
            label="Solar",
            kind="voltage",
            description="Collector input, in millivolts. Absent from 1,902 of 8,535 readings.",
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(0.0, 30.0),
            band_note=(
                "Fires on 17 of 6,649 readings: a -984 mV reading and readings up "
                "to 34.0 V. Both are outside what a panel input can do and are "
                "kept, flagged."
            ),
        ),
        Channel(
            name="solar2_v",
            label="Solar 2",
            kind="voltage",
            description="Second collector input. Only in the two 11-column files, so 2,583 of 8,535 readings.",
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(0.0, 15.0),
            band_note=(
                "0-15 V, the collector's figure. Recorded 0.735-12.944 V, so "
                "nothing is flagged -- but only just, and the 0-30 V of 0.9.0 "
                "was doing no work here."
            ),
        ),
        Channel(
            name="battery_v",
            label="Battery",
            kind="voltage",
            description="Bank in millivolts, resting around 11.9 V.",
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(9.0, 16.0),
            band_note=(
                "Fires on 138 of 8,529 readings (1.6%), all of them below 9 V, "
                "down to 0.757 V. That is the same lead-acid bank reading as "
                "aisvn's and the same question: what is installed here. 0.8's "
                "0-60 V band flagged every millivolt reading instead, which is "
                "2,041 false positives on this channel alone."
            ),
        ),
        Channel(
            name="current_a_chA",
            label="Current A",
            kind="current",
            description=(
                "Channel A, recorded 784-4,095 with a median of 998. Milliamps "
                "would put the median at 1.0 A, which is plausible; nothing "
                "confirms it."
            ),
            unit="",
            raw_unit=None,
            band=None,
            band_note=(
                "No scale and no band. This applet's confirmed scales cover "
                "solar, battery, load and LiPo, and the current channels were "
                "never among them, so applying 0.001 here would be inventing a "
                "conversion. 0.8 tested 784-4,095 against a +/-50 A band, which "
                "flagged all 8,535 readings -- the band was asserting an "
                "amplitude the archive cannot support."
            ),
        ),
        Channel(
            name="current_a_chB",
            label="Current B",
            kind="current",
            description="Channel B, recorded 0-3,967 with a median of 1,009. Same unresolved unit as channel A.",
            unit="",
            raw_unit=None,
            band=None,
            band_note="No scale and no band, for the same reason as channel A.",
        ),
        Channel(
            name="load_v",
            label="Load",
            kind="voltage",
            description="Load / dump rail in millivolts. Absent from 2,849 of 8,535 readings.",
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(0.0, 30.0),
            band_note="Recorded 0.067-12.242 V. Nothing is flagged.",
        ),
        Channel(
            name="wind_v",
            label="Wind",
            kind="power",
            description=(
                "The generator's output, logged as the single voltage reading of "
                "the three phases combined. Confirmed by the collector as a power "
                "measurement in watts."
            ),
            unit="W",
            raw_unit="mV",
            scale=0.001,
            band=(0.0, 50.0),
            band_note=(
                "0-50 W, the collector's estimate for this generator. The cell is "
                "in millivolts like every other channel this applet writes, so the "
                "stored range is -0.984 to 14.686 W. It fires on 22 of 4,935 "
                "readings, all of them marginally negative, which is a generator "
                "that was not turning rather than a fault. 0.9.0 excluded this "
                "channel as a not-a-measurement; that was the same mistake 0.8 "
                "made on a per-column-name band, in the other direction -- the "
                "reading was right and the unit was wrong, and the unit is the "
                "collector's to confirm, not the reader's to assume."
            ),
        ),
        Channel(
            name="dump_adc",
            label="Dump ADC",
            kind="raw",
            description="Uncalibrated dump-channel ADC reading, 0-5,075.",
            unit="count",
            band=None,
            band_note=(
                "No band, and none is possible: an uncalibrated ADC count is not "
                "a physical quantity. Published, because the station does log it "
                "and it varies."
            ),
        ),
        Channel(
            name="lipo_v",
            label="LiPo",
            kind="voltage",
            description=(
                "Single-cell pack in millivolts, bimodal: a 0.735 V plateau for "
                "about a fifth of the record and 4.04-4.13 V for the rest."
            ),
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(0.0, 4.35),
            band_note=(
                "Upper bound at the 1S cell ceiling of 4.35 V, lower bound at 0 "
                "rather than 2.5 V. Banded 2.5-4.35 V it fires on 1,656 of 8,535 "
                "readings (19%) because of the 0.735 V plateau, and a flag that "
                "fires on one sample in five cannot mark a contaminated "
                "aggregate. The plateau is a finding, and it stays in the "
                "database and in the report; it is simply not a per-reading "
                "out-of-range."
            ),
        ),
        Channel(
            name="boot_count",
            label="Boot counter",
            kind="count",
            description=(
                "The logger's own monotonic submission counter. It resets every "
                "16 readings, 526 times in 8,535, and 523 of those resets have no "
                "gap in sampling."
            ),
            unit="count",
            band_note=COUNTER_BAND_NOTE,
            stats=("min", "max"),
            counter=True,
        ),
    ),
    open_questions=(
        "The boot counter resets every 16 readings -- 526 times in 8,535 "
        "readings, 523 of them with no gap in sampling. A genuine reboot looks "
        "like that when the station keeps sampling, but a counter that moves "
        "that fast may be something else. Unexplained.",
        "current_a_chA and current_a_chB have no established unit.",
    ),
)


# ---------------------------------------------------------------------------
# phumy2 -- the long record.  416,088 readings, six years, and the station that
# 0.8 mis-flagged in its entirety.
# ---------------------------------------------------------------------------

PHUMY2 = Station(
    station_id="phumy2",
    display_name="Phu My Hung #2",
    location="Phu My Hung, Ho Chi City, Vietnam",
    tz="Asia/Ho_Chi_Minh",
    source_dirs=("phumy2", "phumy2a", "phumy2b"),
    applet="IFTTT_phumy2",
    production=True,
    notes=(
        "7-channel logger and by far the longest record: 416,088 readings from "
        "June 2020 to September 2026. Split across three archive folders because "
        "Google Sheets split the sheet at 2000 rows; they are one instrument."
    ),
    layouts=(
        Layout(
            n_columns=7,
            header=("solar2", "current2", "power", "temp", "LiPo2", "boot"),
            channels=("solar2_v", "current2_a", "power_w", "temp_c", "lipo2_v", "boot_count"),
            n_files=206,
            note=(
                "One layout for all 206 files across all three folders, 180 of "
                "them headerless. This is the layout that 0.8's donor search "
                "had to get right for 46% of the archive's readings."
            ),
        ),
    ),
    channels=(
        Channel(
            name="solar2_v",
            label="Solar",
            kind="voltage",
            description=(
                "The station's only collector input, in millivolts. The level "
                "steps from about 5,000 mV to about 1,200 mV when a bridge and a "
                "load were fitted, so after that date it is a divider output "
                "rather than a panel voltage."
            ),
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(0.0, 30.0),
            band_note=(
                "Nothing is flagged, and nothing should be: 0.8 nulled 220,069 "
                "readings over 2022-10 to 2024-01 on the grounds that the input "
                "was disconnected, and then banded the survivors against a "
                "0-60 V panel ceiling while they were millivolts. The value is "
                "real either way. Open question 5."
            ),
        ),
        Channel(
            name="current2_a",
            label="Current",
            kind="current",
            description=(
                "Secondary current channel, in milliamps. Median 0.231 A, and it "
                "is the only channel at this station that measures a current."
            ),
            unit="A",
            raw_unit="mA",
            scale=0.001,
            band=(-5.0, 5.0),
            band_note=(
                "This is the single largest correction in 0.9. 0.8 stored 232 mA "
                "and banded the column at +/-50 A, so all 416,088 of this "
                "station's samples carried out_of_range, and the site used that "
                "count to decide whether an aggregate was contaminated -- so the "
                "whole station read as broken on a sensor measuring a quarter of "
                "an amp. With the confirmed millivolt-equivalent scale applied at "
                "ingest the range is 0.155-1.997 A and nothing is flagged. The "
                "band is +/-5 A rather than the generic +/-50 A because 50 A is "
                "four orders of magnitude above this sensor's full scale and "
                "could never catch anything."
            ),
        ),
        Channel(
            name="power_w",
            label="Power",
            kind="power",
            description=(
                "Not a power measurement. The hardware was never implemented and "
                "the ESP32 pin reads what the collector describes as phantasy "
                "values: 416,083 of 416,088 readings are exactly 0 and the "
                "remaining five are 13,810-19,877 W. Six distinct values in six "
                "years."
            ),
            unit="W",
            band=None,
            band_note=(
                "No band, and this is the load-bearing one. 0.8 banded this at "
                "+/-2,000 W, where five readings were flagged and 415,112 zeros "
                "were presented to a reader as a power curve. A band asserts that "
                "the input carries a quantity, and this one does not: the "
                "hardware was never implemented. Declaring no band is the honest "
                "answer; declaring a wide one is the same claim in a louder voice."
            ),
            stats=(),
            publish=False,
            exclude="not_measurement",
            exclude_note=(
                "Excluded from the site and the CSVs, and the reason is that it "
                "is not a measurement rather than that it is implausible. 0.8 "
                "published it banded at +/-2,000 W, where five readings were "
                "flagged and 415,112 zeros were presented as a power curve. The "
                "values are in the database, with no band, because a band would "
                "assert a quantity the pin does not carry. Open question 3."
            ),
        ),
        Channel(
            name="temp_c",
            label="Temperature",
            kind="temperature",
            description="Ambient temperature probe.",
            unit="degC",
            scale=1.0,
            band=(0.0, 60.0),
            band_note=(
                "Fires on the readings above 60 degC, reaching 80.6. That is not "
                "plausible ambient in Ho Chi City. 0.8 multiplied this by 10 at "
                "ingest and banded it 50-900 as tenths, so 29.1 degC was stored as "
                "291 inside a band that was wrong by the same factor; the scale "
                "and the band were each measured against the other. The sheet "
                "writes plain degrees -- 24.2, 24.1, 24.2."
            ),
        ),
        Channel(
            name="lipo2_v",
            label="LiPo 2",
            kind="voltage",
            description="Single-cell pack in millivolts, resting around 3.9 V.",
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(2.5, 4.35),
            band_note="Fires on 5 of 416,083 readings, all of them below 2.5 V. A flat or absent pack.",
        ),
        Channel(
            name="boot_count",
            label="Boot counter",
            kind="count",
            description=(
                "The logger's own monotonic submission counter, reset by a "
                "reboot, reaching 71,854 by the end of the record."
            ),
            unit="count",
            band_note=COUNTER_BAND_NOTE,
            stats=("min", "max"),
            counter=True,
        ),
    ),
    open_questions=(
        "solar2_v is a divider output after a bridge and load were fitted, so it "
        "is not a panel voltage and should not be charted as one. The bridge "
        "ratio is unknown.",
        "power_w is not a power measurement. The hardware was never implemented.",
    ),
)


# ---------------------------------------------------------------------------
# solar-2020-05 -- a bench sheet, not a station.  Carries an event label and
# two uncalibrated ADC channels.
# ---------------------------------------------------------------------------

SOLAR_2020_05 = Station(
    station_id="solar-2020-05",
    display_name="Solar bench (2020-05-16 sheet)",
    location="Nha Be, Ho Chi City, Vietnam",
    tz="Asia/Ho_Chi_Minh",
    source_dirs=("Solar_2020-05-16",),
    applet="IFTTT_Solar_2020-05-16",
    production=True,
    notes=(
        "Bench sheet rather than an instrument, but it logged a real panel for a "
        "month alongside aisvn's first weeks, so it is kept as a station. The "
        "only source of the 'event' label in the archive."
    ),
    layouts=(
        Layout(
            n_columns=5,
            header=("event", "digital", "voltage", "LiPo"),
            channels=("event", "digital_adc", "voltage_adc", "lipo_v"),
            n_files=7,
            note="One layout for all 7 files, all with a header.",
        ),
    ),
    channels=(
        Channel(
            name="event",
            label="Event",
            kind="text",
            description="The IFTTT event name, 'solar_reading'. The only text column in the archive.",
            unit="",
            stats=(),
            band_note=(
                "No band, because it is a label and not a number. It is not "
                "aggregated into the rollups for the same reason: a per-hour "
                "count of the string 'solar_reading' is a constant, and a "
                "constant in a chart is a flat line at the floor."
            ),
            publish=False,
            exclude="text_label",
            exclude_note=(
                "The only text channel in the archive, and the only channel with "
                "no statistics. It is stored on every row so a reader can see "
                "which applet produced a reading, and it is not charted because "
                "there is nothing to plot."
            ),
        ),
        Channel(
            name="digital_adc",
            label="Digital ADC",
            kind="raw",
            description="Uncalibrated digital-channel ADC count, 0-4,962, at 0 for 65% of the record.",
            unit="count",
            band=None,
            band_note="No band: an uncalibrated ADC count is not a physical quantity.",
        ),
        Channel(
            name="voltage_adc",
            label="Voltage ADC",
            kind="raw",
            description="Uncalibrated voltage-channel ADC count, 0-9,828.",
            unit="count",
            band=None,
            band_note="No band, for the same reason as digital_adc.",
        ),
        Channel(
            name="lipo_v",
            label="LiPo",
            kind="voltage",
            description=(
                "Single-cell pack in millivolts, median 3.640 V, with a 13.637 V "
                "spike that no single cell can produce."
            ),
            unit="V",
            raw_unit="mV",
            scale=0.001,
            band=(2.5, 4.35),
            band_note=(
                "Fires on 148 of 12,920 readings (1.15%): 139 below 2.5 V and 9 "
                "above 4.35 V, the largest of which is 13.637 V. Both directions "
                "are the finding."
            ),
        ),
    ),
    open_questions=("lipo_v reaches 13.637 V, which is three times a 1S cell's ceiling.",),
)


# ---------------------------------------------------------------------------
# test -- a bench probe, not a solar station.  The archive's only hundredths.
# ---------------------------------------------------------------------------

TEST = Station(
    station_id="test",
    display_name="Test bench",
    location="Nha Be, Ho Chi City, Vietnam",
    tz="Asia/Ho_Chi_Minh",
    source_dirs=("test",),
    applet="IFTTT_test",
    production=False,
    notes=(
        "Not a solar station. What remains after the collector's exclusion is a "
        "4-column nix/temp/wifi probe, July to August 2020. Published under 'Not "
        "solar production' and labelled with this note wherever it appears."
    ),
    layouts=(
        Layout(
            n_columns=4,
            header=("nix", "temp_c", "wifi_tx_ms"),
            channels=("nix_raw", "temp_c", "wifi_raw"),
            n_files=18,
            note=(
                "One layout for all 18 files in the folder, 16 of them headerless. "
                "0.8 described the folder as holding two unrelated schemas and "
                "counted 19 files; the 0.8.0 raw repair deleted IFTTT_test.xlsx "
                "and removed the 11-column solar stretch from IFTTT_test (1).xlsx, "
                "so what is left is one schema. IFTTT_test (1).xlsx is excluded "
                "whole on the collector's word -- see config.FILE_EXCLUSIONS."
            ),
        ),
    ),
    channels=(
        Channel(
            name="nix_raw",
            label="Nix probe",
            kind="raw",
            description="Uncalibrated probe channel, 70-102. The only other thing this station logs.",
            unit="count",
            band=None,
            band_note="No band: an uncalibrated probe count is not a physical quantity.",
        ),
        Channel(
            name="temp_c",
            label="Temperature",
            kind="temperature",
            description=(
                "Ambient temperature, to two decimal places. The collector asked "
                "for that resolution on the probe; the sheet writes 29.47."
            ),
            unit="degC",
            scale=1.0,
            band=(0.0, 60.0),
            band_note=(
                "Recorded 21.49-31.31 degC, median 27.70. Nothing is flagged. "
                "0.8 multiplied this by 100 at ingest and banded it 2149-3131 as "
                "hundredths, which agreed with each other and with nothing else: "
                "the sheet writes 29.47, so 0.8 stored 2947. The `test` station "
                "was the archive's only 'hundredths' channel only because of that "
                "multiply, and a per-station unit override in config.py existed "
                "solely to keep the band and the scale back in step -- read in "
                "three places, of which one was the frontend. All three "
                "temperature channels in the archive are plain degrees."
            ),
        ),
        Channel(
            name="wifi_raw",
            label="WiFi",
            kind="raw",
            description="WiFi probe counter, 1,605-56,708.",
            unit="count",
            band=None,
            band_note="No band, for the same reason as nix_raw.",
        ),
    ),
    open_questions=(
        "IFTTT_test (1).xlsx is excluded whole on the collector's word, because "
        "its 4,120 unique readings are dated 2020-07-01 to 07-08 and duplicate "
        "the probe data IFTTT_test (2).xlsx carries. The collector described it "
        "as an 11-column solar layout; the 0.8.0 raw repair removed that stretch "
        "and the file is now the same probe as its neighbours. The exclusion is "
        "still defensible on the overlap, but the reason it was given no longer "
        "describes the file, and that is a question for the collector.",
    ),
)


# ---------------------------------------------------------------------------
# voltage-phumy -- an ADC calibration sheet.  Not a station at all.
# ---------------------------------------------------------------------------

VOLTAGE_PHUMY = Station(
    station_id="voltage-phumy",
    display_name="Phu My Hung voltage calibration",
    location="Phu My Hung, Ho Chi City, Vietnam",
    tz="Asia/Ho_Chi_Minh",
    source_dirs=("Voltage_phumy",),
    applet="Voltage_phumy",
    production=False,
    notes=(
        "A bench calibration of the ADC-to-voltage conversion, not a station. It "
        "carries the hand-made discharge summary in its second column block and "
        "the lab's own annotations, which are recovered into notes."
    ),
    layouts=(
        Layout(
            n_columns=4,
            header=("raw", "voltage", "millis()"),
            channels=("adc_raw", "voltage_adc", "millis_ms"),
            n_files=3,
            note=(
                "One layout for all 3 files. The second column block, to the "
                "right of this one, is a coarser hand-made summary and the lab "
                "annotations; it is read for prose only."
            ),
        ),
    ),
    channels=(
        Channel(
            name="adc_raw",
            label="ADC raw",
            kind="raw",
            description="Raw calibration-channel ADC count, 2,107-3,127. Deliberately mid-scale.",
            unit="count",
            band=None,
            band_note="No band: this is the channel being calibrated, not a measurement.",
        ),
        Channel(
            name="voltage_adc",
            label="Voltage ADC",
            kind="raw",
            description="Raw voltage-channel ADC count, 1,862-2,511.",
            unit="count",
            band=None,
            band_note="No band, for the same reason as adc_raw.",
        ),
        Channel(
            name="millis_ms",
            label="Uptime",
            kind="count",
            description="millis() since boot, 2.2 s to 7.9 days. The hardware's own view of its uptime.",
            unit="ms",
            band_note=COUNTER_BAND_NOTE,
            stats=("min", "max"),
            counter=True,
        ),
    ),
    open_questions=(
        "The ADC-to-voltage conversion this sheet calibrates is not recorded here, "
        "so neither channel can be published as a voltage.",
    ),
)


STATIONS: tuple[Station, ...] = (
    AISVN,
    AISVN2,
    AISVN_SOLAR,
    MAKER_WEBHOOKS,
    PHUMY2,
    SOLAR_2020_05,
    TEST,
    VOLTAGE_PHUMY,
)

BY_ID: dict[str, Station] = {s.station_id: s for s in STATIONS}
STATION_IDS: tuple[str, ...] = tuple(s.station_id for s in STATIONS)
BY_SOURCE_DIR: dict[str, Station] = {d: s for s in STATIONS for d in s.source_dirs}
NON_PRODUCTION: frozenset[str] = frozenset(s.station_id for s in STATIONS if not s.production)


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

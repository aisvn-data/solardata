"""Which channels each rollup carries, and how.

One definition, imported by both the aggregate stage and the export stage,
because those two are the same decision written twice and have already drifted
once: ``readings_daily`` has no ``battery_v_avg`` (a mean of minima is not a
useful number) while ``readings_hourly`` does, and every consumer of the rollups
has to know which statistic it is looking at.

The statistics are chosen per channel rather than uniformly:

``avg``
    the default. Right for anything that is a level rather than an event.
``min`` / ``max``
    for the battery and LiPo voltages, where the interesting number is the worst
    moment of the bucket. A LiPo pack's health is its lowest reading, not its
    mean, and a daily *mean* of minima is not a useful number at all -- which is
    why the daily rollup has no ``battery_v_avg``.
``peak`` (``max``)
    for the power and solar channels, so a bucket containing a spike is visible
    next to one that does not.

Per-metric out-of-range counts
------------------------------
``<channel>_n_oor`` counts the samples in the bucket whose value fell outside the
band ``etl.normalize.metrics`` records for that channel. It exists because the
row-level ``n_out_of_range`` cannot answer the question the site needs to ask.

``phumy2`` 2020-11-27 16:00 UTC is the case. One raw sample reads ``power_w =
19877`` and ``solar2_v = 160`` where its neighbours are 0; averaged with the 29
good zeros in that hour it becomes ``power_w_avg = 662.57``, which is *inside*
the +/-2000 W band, so the aggregate carries no flag at all. The row-level count
is no help either: every sample in that hour is flagged, because
``current2_a`` reads ~232 against a +/-50 A band (it is milliamps and the scale
is unconfirmed -- see the open questions). One flag for the row, 30 of 30, and
the one sample that actually broke something is invisible.

Counting per metric localises it: that hour has 1 of 30 samples out of band on
``power_w``, and 30 of 30 on ``current2_a``. A mean built from 29 zeros and one
corrupt cell is then a number the site can mark as contaminated and say how
contaminated, rather than a plausible-looking value with nothing attached.
"""

from __future__ import annotations

from etl.normalize.metrics import METRIC_BY_COLUMN

#: Canonical channels that get a value column in both rollups, with the
#: statistics each one is aggregated by.  Order is the CSV column order.
CHANNELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("solar_v", ("avg", "max")),
    ("solar2_v", ("avg", "max")),
    ("solar3_v", ("avg", "max")),
    ("battery_v", ("avg", "min", "max")),
    ("battery2_v", ("min", "max")),
    ("lipo_v", ("avg", "min", "max")),
    ("lipo2_v", ("avg", "min", "max")),
    ("current_a", ("avg",)),
    ("current_a_chA", ("avg",)),
    ("current_a_chB", ("avg",)),
    ("current2_a", ("avg",)),
    ("power_w", ("avg", "max")),
    ("load_v", ("avg",)),
    ("load1_v", ("avg",)),
    ("load2_v", ("avg",)),
    ("wind_v", ("avg",)),
    ("temp_c", ("avg", "min", "max")),
    # The two bench ADC channels. `solar-2020-05` is a bench sheet whose only
    # measurements are these and a LiPo pack, and omitting them left that station
    # offering a single channel, which reads as "broken" rather than "small".
    # Neither has a plausibility band, so neither gets an out-of-range count.
    ("voltage_adc", ("avg",)),
    ("digital_adc", ("avg",)),
)

#: Which of the above also get a per-metric out-of-range count.  A channel with no
#: plausibility band (``wind_v`` and the raw ADC channels are set from the
#: hardware, not from physics) would produce an all-zero column, so it is left
#: out rather than shipped as a column that is never anything but 0.
COUNTED: tuple[str, ...] = tuple(
    channel for channel, _ in CHANNELS if METRIC_BY_COLUMN[channel].lo is not None
)


def channel_column(channel: str, stat: str) -> str:
    """``('battery_v', 'min')`` -> ``'battery_v_min'``."""
    return f"{channel}_{stat}"


def value_columns() -> tuple[str, ...]:
    """Every value column, in order. Identical for the two rollups."""
    return tuple(channel_column(channel, stat) for channel, stats in CHANNELS for stat in stats)


def oor_columns() -> tuple[str, ...]:
    return tuple(f"{channel}_n_oor" for channel in COUNTED)


#: Human label per statistic, for the readout.  The site shows these because the
#: same channel carries a different statistic at each resolution, and a reader
#: comparing the two views needs to be told which they are looking at.
STAT_LABELS: dict[str, str] = {
    "avg": "mean",
    "min": "minimum",
    "max": "peak",
    "total": "total",
}

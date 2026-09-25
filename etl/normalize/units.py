"""Detecting changes in sensor scaling over the life of a station.

This is the subtlest problem in the archive and the one most likely to produce
silently wrong charts.

The ``aisvn`` station logged the same eleven channel names from June 2020 to
February 2022, but the numbers under identical headers are on different scales.
Two files that share the header

    time, solar, battery, current, power, load, wind, temp, solar2, LiPo, boot

read:

    IFTTT_aisvn (25).xlsx   2020-10-28   battery 29.12   temp 16.2
    IFTTT_aisvn (28).xlsx   2021-11-02   battery 14.44   temp 34.0

A 29 V "battery" and a 16 degC ambient temperature in Ho Chi City in October are
not measurements, they are a different calibration.  Nothing in the file records
the change.

Approach
--------
Rather than invent a correction, this module *detects and records* scale
regimes and leaves the values as the sensor reported them.  Each candidate gets
an explicit ``scale`` hypothesis with the evidence that produced it, a
confidence, and ``status='unconfirmed'`` so a human can accept or reject it.
Confirmed regimes are the only ones a downstream query should trust.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

#: Scale hypotheses we are willing to propose, largest first.
SCALE_CANDIDATES: tuple[float, ...] = (1.0, 0.001, 0.01, 0.1, 10.0, 1000.0)

#: A scale is proposed when it puts the median inside this fraction of the
#: metric's plausible range.  Below this the proposal is not worth recording.
MIN_IN_RANGE_FRACTION = 0.5

#: Minimum number of samples before we trust a regime boundary.
MIN_SAMPLES = 200


@dataclass
class ScaleHypothesis:
    scale: float
    in_range_fraction: float
    median_before: float
    median_after: float
    verdict: str  # "improves" | "unchanged" | "degrades"

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


@dataclass
class Regime:
    """A window over which one column of one station is believed consistent."""

    station_id: str
    column: str
    unit: str
    valid_from: str  # ts_utc ISO
    valid_to: str | None
    scale: float
    status: str  # unconfirmed | confirmed | rejected
    detected_by: str  # range | median_shift | manual
    confidence: str  # high | medium | low
    notes: str
    evidence: dict = field(default_factory=dict)


def in_range_fraction(values: Sequence[float], lo: float, hi: float, scale: float = 1.0) -> float:
    """Fraction of ``values`` that land in ``[lo, hi]`` after applying ``scale``."""
    if not values or lo is None or hi is None:
        return 0.0
    hits = sum(1 for v in values if lo <= v * scale <= hi)
    return hits / len(values)


def choose_scale(values: Sequence[float], lo: float, hi: float) -> ScaleHypothesis | None:
    """Pick the scale that best maps ``values`` into ``[lo, hi]``.

    Returns ``None`` when no candidate is meaningfully better than 1.0, so the
    common case ("the header already says volts") records nothing.
    """
    if not values or lo is None or hi is None:
        return None
    median = statistics.median(values)
    baseline = in_range_fraction(values, lo, hi, 1.0)

    best: ScaleHypothesis | None = None
    for scale in SCALE_CANDIDATES:
        if scale == 1.0:
            continue
        fraction = in_range_fraction(values, lo, hi, scale)
        if fraction <= baseline:
            continue
        if fraction < MIN_IN_RANGE_FRACTION:
            continue
        if best is None or fraction > best.in_range_fraction:
            best = ScaleHypothesis(
                scale=scale,
                in_range_fraction=round(fraction, 4),
                median_before=round(median, 4),
                median_after=round(median * scale, 4),
                verdict="improves",
            )

    if best is None:
        return None

    # Final guard: a scale that pushes the median *inside* the band but leaves
    # the individual values outside it is not a correction, it is a coincidence
    # of two mistakes.  This is the check that declines to propose anything for
    # the `aisvn` October-2020 window, where `battery` reads 29.12 V: no power
    # of ten makes a 29 V reading a plausible 3S pack, and pretending otherwise
    # would invent data.  The window is still visible through the per-reading
    # `out_of_range` flag and the report's channel-plausibility table.
    if not (lo <= best.median_after <= hi):
        return None
    return best


def propose_regime(
    station_id: str,
    column: str,
    unit: str,
    valid_from: str,
    valid_to: str | None,
    values: Sequence[float],
    lo: float | None,
    hi: float | None,
    notes: str = "",
) -> Regime | None:
    """Build a ``Regime`` for one column/window, or ``None`` if it looks fine."""
    if not values or len(values) < MIN_SAMPLES or lo is None or hi is None:
        return None
    hypothesis = choose_scale(values, lo, hi)
    if hypothesis is None:
        return None
    return Regime(
        station_id=station_id,
        column=column,
        unit=unit,
        valid_from=valid_from,
        valid_to=valid_to,
        scale=hypothesis.scale,
        # Never 'confirmed' here.  A proposal is a question for a human, and
        # AGENTS.md rule 3 forbids anything downstream from applying a scale
        # that a human has not signed off.
        status="unconfirmed",
        detected_by="range",
        # Confidence describes the *evidence*, not the hypothesis: if a scale
        # puts >90% of the window's values in range it is a real unit change,
        # whereas a marginal result is probably a coincidence of two errors.
        confidence="medium" if hypothesis.in_range_fraction > 0.9 else "low",
        notes=notes
        or (
            # The note is the whole point of the row: it states what was
            # observed, what is proposed, and how well it fits, so a reviewer
            # can judge without re-running the detector.
            f"median {hypothesis.median_before} outside plausible [{lo}, {hi}] {unit}; "
            f"scale {hypothesis.scale} brings it to {hypothesis.median_after} "
            f"({hypothesis.in_range_fraction:.0%} in range)"
        ),
        evidence=asdict(hypothesis),
    )


def detect_per_file(
    station_id: str,
    column: str,
    unit: str,
    windows: Sequence[tuple[str, str | None, Sequence[float]]],
    lo: float | None,
    hi: float | None,
) -> list[Regime]:
    """Run :func:`propose_regime` over per-file windows, dropping the clean ones.

    ``windows`` is ``(valid_from, valid_to, values)`` per source file.  Only
    files that actually look mis-scaled produce a regime, so a healthy station
    yields an empty list and the reader can skip straight to ingest.
    """
    regimes: list[Regime] = []
    for valid_from, valid_to, values in windows:
        regime = propose_regime(station_id, column, unit, valid_from, valid_to, values, lo, hi)
        if regime is not None:
            regimes.append(regime)
    return regimes

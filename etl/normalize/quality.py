"""Value-level quality handling: sentinels, clips, range checks, free text.

The rule this module exists to enforce: **nothing is dropped silently**.  Every
cell either becomes a typed value, becomes NULL with a recorded reason, or is
written to the ``rejects`` table with a reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from etl.config import (
    CLIP_CANDIDATES,
    CLIP_RUN_LENGTH,
    FLAG_CLIP,
    FLAG_DUPLICATE_TS,
    FLAG_FREE_TEXT,
    FLAG_NON_MONOTONIC,
    FLAG_OUT_OF_RANGE,
    FLAG_SENTINEL,
    SENTINEL_NEGATIVE,
)
from etl.normalize.metrics import Metric

#: Free-text strings in a *mapped* numeric column are lab notes rather than bad
#: numbers, but the rule only applies to prose: below this length a stray cell is
#: more likely junk than a note.  Side-block columns are judged structurally by
#: :func:`looks_like_note` instead, which is what catches short human notes like
#: ``STROMAUSFALL!!`` and ``leave home``.
FREE_TEXT_MIN_LENGTH = 20

#: Side-block columns often hold a hand-made summary table whose cells are bare
#: clock times.  These patterns are infrastructure, not annotation.
_CLOCK_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\s*[AaPp][Mm])?$")
_DATEISH_RE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$")


def looks_like_note(value: str) -> bool:
    """Is this side-block cell a human annotation rather than sheet plumbing?

    Structural rather than length-based, so that short but meaningful notes
    survive while the surrounding infrastructure does not.  The archive contains
    all of the following in side-block columns and none of them are notes:

    * header words, including headers repeated mid-file when a block of readings
      was appended (``time``, ``date``)
    * clock and date cells from a hand-made summary table (``04:08AM``)
    * the summary table's numbers
    """
    from etl.normalize.metrics import map_header
    from etl.readers.times import looks_like_header, looks_like_timestamp

    text = (value or "").strip()
    if not text:
        return False
    if looks_like_header(text) or looks_like_timestamp(text):
        return False
    if _CLOCK_RE.match(text) or _DATEISH_RE.match(text):
        return False
    try:
        float(text)
        return False  # a number, however oddly formatted
    except ValueError:
        pass
    return map_header(0, text).column is None


@dataclass
class CellResult:
    value: float | str | None
    flags: tuple[str, ...] = ()


def coerce_number(
    raw: str,
    metric: Metric | None,
    *,
    free_text_out: list[str] | None = None,
) -> CellResult:
    """Turn one raw cell string into a typed value plus quality flags.

    Args:
        raw: the cell as read from the sheet.
        metric: the canonical metric, used for the plausibility range check.
            ``None`` means the column was never identified.
        free_text_out: if given, long non-numeric strings are appended here so
            the caller can route them to the ``notes`` table instead of losing
            them.
    """
    text = (raw or "").strip()
    if not text:
        return CellResult(None)

    # 1. Exact sentinel match, checked before float parsing so that "-992.0"
    #    written by Sheets is caught as well as "-992".
    for sentinel, reason in SENTINEL_NEGATIVE.items():
        if text == sentinel or text == f"{sentinel}.0":
            del reason
            return CellResult(None, (FLAG_SENTINEL,))

    # 2. Numeric?
    try:
        number = float(text)
    except ValueError:
        if free_text_out is not None and len(text) >= FREE_TEXT_MIN_LENGTH:
            free_text_out.append(text)
        return CellResult(None, (FLAG_FREE_TEXT,))

    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return CellResult(None, (FLAG_SENTINEL,))

    # 3. Plausibility.  Only a flag -- the value is kept, because during a
    #    firmware change the "implausible" values are exactly the interesting
    #    ones and a human still has to adjudicate them.
    if (
        metric is not None
        and metric.lo is not None
        and metric.hi is not None
        and not (metric.lo <= number <= metric.hi)
    ):
        return CellResult(number, (FLAG_OUT_OF_RANGE,))

    return CellResult(number)


def coerce_cell(
    raw: str,
    metric: Metric | None,
    *,
    free_text_out: list[str] | None = None,
) -> CellResult:
    """Coerce one raw cell according to its metric's declared kind.

    Text-kind metrics such as ``event`` hold words, not numbers.  Routing them
    through :func:`coerce_number` would null every value and flag the whole
    column, which is how ``solar-2020-05``'s 12,920 ``solar_reading`` labels
    were being lost.
    """
    text = (raw or "").strip()
    if not text:
        return CellResult(None)
    if metric is not None and metric.kind == "text":
        return CellResult(text)
    return coerce_number(text, metric, free_text_out=free_text_out)


def is_clip_candidate(value: float | None) -> bool:
    return value is not None and any(abs(value - c) < 1e-9 for c in CLIP_CANDIDATES)


def long_runs(values: list[float | None], minimum: int = CLIP_RUN_LENGTH) -> set[int]:
    """Indices inside runs of >= ``minimum`` identical consecutive values.

    Used to spot rail/clip artefacts such as the LiPo channel sitting at 3532
    for thousands of rows.  These are flagged, never nulled.
    """
    flagged: set[int] = set()
    start = 0
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[start]:
            length = i - start
            if length >= minimum and values[start] is not None:
                flagged.update(range(start, i))
            start = i
    return flagged


def clip_run_indices(values: list[float | None]) -> set[int]:
    """Indices belonging to a repeated *known clip candidate* value."""
    flagged: set[int] = set()
    start = 0
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[start]:
            length = i - start
            if length >= CLIP_RUN_LENGTH and is_clip_candidate(values[start]):
                flagged.update(range(start, i))
            start = i
    return flagged


def merge_flags(*groups) -> str:
    """Combine flag tuples into the stored comma-separated form."""
    seen: list[str] = []
    for group in groups:
        for flag in group:
            if flag and flag not in seen:
                seen.append(flag)
    return ",".join(seen)


__all__ = [
    "FLAG_CLIP",
    "FLAG_DUPLICATE_TS",
    "FLAG_FREE_TEXT",
    "FLAG_NON_MONOTONIC",
    "FLAG_OUT_OF_RANGE",
    "FLAG_SENTINEL",
    "CellResult",
    "clip_run_indices",
    "coerce_cell",
    "coerce_number",
    "is_clip_candidate",
    "long_runs",
    "looks_like_note",
    "merge_flags",
]

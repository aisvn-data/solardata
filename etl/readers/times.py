"""Parsing the raw timestamp strings.

Every timestamp in the archive is US-locale free text in column A, e.g.
``"July 14, 2020 at 10:12AM"``.  Consequences that matter downstream:

* Month names are English, so lexicographic sorting is wrong
  (``April < August < December < February ...``).
* There is no seconds field and no timezone.  The stations are UTC+07:00 with no
  DST, so the offset is fixed, but it has to be attached explicitly or every
  downstream join is ambiguous.
* A handful of sheets carry secondary time columns in other formats
  (``04:08AM``, ``04:08:00``) as part of a side block.  Those are deliberately
  *not* parsed here -- only column A is treated as the observation clock.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

#: The one and only observation timestamp format in column A.
TIMESTAMP_RE = re.compile(
    r"^(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})"
    r"\s+at\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?P<ampm>AM|PM)$"
)

#: Strings that are a repeated header row rather than data.
HEADER_LIKE = frozenset({"time", "date", "timestamp", "datetime"})


class TimestampError(ValueError):
    """Raised when a column-A cell cannot be read as an observation time."""


def looks_like_timestamp(value: str) -> bool:
    return bool(TIMESTAMP_RE.match(value.strip()))


def looks_like_header(value: str) -> bool:
    return value.strip().lower() in HEADER_LIKE


def parse_local(value: str) -> datetime:
    """Parse a raw column-A timestamp into a naive local datetime.

    Raises:
        TimestampError: if the cell is not in the expected format.
    """
    match = TIMESTAMP_RE.match(value.strip())
    if match is None:
        raise TimestampError(f"unparseable timestamp: {value!r}")
    parts = match.groupdict()
    try:
        return datetime.strptime(
            "{month} {day} {year} {hour}:{minute}{ampm}".format(**parts),
            "%B %d %Y %I:%M%p",
        )
    except ValueError as exc:  # e.g. "February 30, 2020 at 10:00AM"
        raise TimestampError(f"invalid date {value!r}: {exc}") from exc


def to_utc(local: datetime, tzinfo) -> datetime:
    """Attach a timezone and convert to UTC.

    ``fold=0`` resolves any DST ambiguity deterministically.  Neither Vietnam
    nor the archived European collectors observe DST during these records, so
    this is a formality, but it keeps the function safe if a station is added
    later.
    """
    return local.replace(tzinfo=tzinfo, fold=0).astimezone(UTC)


def iso_utc(dt: datetime) -> str:
    """Canonical on-disk representation: RFC 3339 / ISO 8601, UTC, 'Z' suffix."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_local(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def parse_to_pair(value: str, tzinfo) -> tuple[str, str]:
    """Convenience: raw text -> ``(ts_utc_iso, ts_local_iso)``."""
    local = parse_local(value)
    return iso_utc(to_utc(local, tzinfo)), iso_local(local)


def year_of(ts_utc_iso: str) -> int:
    return int(ts_utc_iso[:4])

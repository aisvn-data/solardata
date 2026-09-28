"""The one timestamp format in the archive, and its conversion to UTC.

Column A of every sheet is US-locale free text with no offset, for example
``July 14, 2020 at 10:12AM``.  It has to be parsed, and it has to be parsed the
same way in the ingest, in a donor search, in a GROUP BY and in a rollup:
``April`` sorts before ``August`` as a string, so any lexicographic comparison
of these timestamps is wrong.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final
from zoneinfo import ZoneInfo

__all__ = [
    "MONTHS",
    "TIMESTAMP_FORMAT",
    "TimestampError",
    "day_key",
    "hour_key",
    "iso_local",
    "iso_utc",
    "looks_like_header",
    "looks_like_timestamp",
    "parse_local",
    "parse_to_pair",
    "to_utc",
    "year_of",
]

TIMESTAMP_RE: Final = re.compile(
    r"^(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})"
    r"\s+at\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?P<ampm>[AaPp][Mm])$"
)

#: Values that mark a row as a repeated header rather than a reading. A header
#: row inside a headerless chunk is a real thing in this archive -- one file in
#: Maker_Webhooks_Events has two.
HEADER_LIKE: Final = frozenset({"time", "date", "timestamp", "datetime"})

#: Retained for documentation and for anything that wants to show the archive's
#: format literally.  Nothing parses with it -- see ``parse_local``.
TIMESTAMP_FORMAT: Final = "%B %d, %Y at %I:%M%p"


class TimestampError(ValueError):
    """A cell in column A that is not a timestamp this pipeline can read."""


def looks_like_timestamp(value: str) -> bool:
    return bool(TIMESTAMP_RE.match(value.strip()))


def looks_like_header(value: str) -> bool:
    return value.strip().lower() in HEADER_LIKE


#: The twelve month names, spelled out rather than looked up in ``calendar``.
#: ``calendar.month_name`` consults the C library's locale tables and is
#: affected by the same uninitialised-locale problem as ``%B`` and ``%p``; a
#: fixed tuple cannot be.  An unrecognised name is rejected rather than guessed
#: at, so a sheet written in another language fails loudly instead of landing in
#: January.
MONTHS: Final = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)

MONTH_NUMBERS: Final = {name: i for i, name in enumerate(MONTHS, start=1)}


def parse_local(value: str) -> datetime:
    """The naive local wall clock the sheet wrote.

    Built from the regex's own groups rather than handed to
    ``datetime.strptime``, and that is not a style preference.  ``%B`` and ``%p``
    both resolve out of the C library's ``LC_TIME`` data, which is not loaded
    until something calls ``locale.setlocale``; on Windows it usually is not, so
    ``strptime`` raises ``ValueError: does not match format`` on every cell in
    the archive while ``TIMESTAMP_RE`` still matches all of them.  The build then
    reports zero readings and 738,000 rejects, which reads as a data problem and
    is a locale one.  The regex already validated the shape, so reading the
    fields out of it is both deterministic and faster.
    """
    text = value.strip()
    match = TIMESTAMP_RE.match(text)
    if match is None:
        raise TimestampError(f"not a timestamp cell: {value!r}")

    month = MONTH_NUMBERS.get(match["month"].lower())
    if month is None:
        raise TimestampError(f"{value!r}: unknown month {match['month']!r}")

    hour = int(match["hour"])
    if not 1 <= hour <= 12:
        raise TimestampError(f"{value!r}: hour {hour} is not a 12-hour hour")
    meridiem = match["ampm"].upper()
    if meridiem == "PM" and hour != 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0

    try:
        return datetime(int(match["year"]), month, int(match["day"]), hour, int(match["minute"]))
    except ValueError as exc:  # e.g. "February 30, 2020 at 10:12AM"
        raise TimestampError(f"{value!r}: {exc}") from exc


def to_utc(local: datetime, tzinfo: ZoneInfo) -> datetime:
    """The same instant in UTC.

    Vietnam is UTC+07:00 with no DST, which is correct, and it is an assumption
    about every reading in the archive. ``fold=0`` is explicit because a bare
    ``replace(tzinfo=...)`` on an ambiguous or imaginary local time is exactly
    the kind of thing that moves a reading by an hour without a word.
    """
    return local.replace(tzinfo=tzinfo, fold=0).astimezone(UTC)


def iso_utc(dt: datetime) -> str:
    """RFC 3339, UTC, Z suffix: ``2020-07-14T03:12:00Z``. The primary key clock."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_local(dt: datetime) -> str:
    """Naive local wall clock, ``2020-07-14T10:12:00``. Display only."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def parse_to_pair(value: str, tzinfo: ZoneInfo) -> tuple[str, str]:
    """Both clocks in one call, which is how every caller wants it."""
    local = parse_local(value)
    return iso_utc(to_utc(local, tzinfo)), iso_local(local)


def year_of(ts_utc: str) -> int:
    return int(ts_utc[:4])


def hour_key(ts_utc: str) -> str:
    """The UTC hour a reading falls in: ``2020-07-14T03:12:00Z`` -> ``2020-07-14T03:00:00Z``."""
    return ts_utc[:13] + ":00:00Z"


def day_key(day: str) -> str:
    """The UTC midnight of a local day, from the local day itself."""
    return day[:10] + "T00:00:00Z"

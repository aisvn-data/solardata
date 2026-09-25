"""Station registry: which raw folder belongs to which physical station.

The folder names under ``data/raw`` are *archive chunks*, not stations.  Three
of them (``phumy2``, ``phumy2a``, ``phumy2b``) are consecutive 2000-row splits
of the same IFTTT applet and must be merged into one station, otherwise every
downstream query has to know about the chunk boundaries.

Timezone is an explicit, reviewable assumption.  The stations are in Nha Be and
Phu My Hung, Ho Chi City, Vietnam, which is UTC+07:00 all year (no DST), so the
conversion is unambiguous -- but it is still an assumption and lives here so it
can be corrected in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Station:
    station_id: str
    display_name: str
    location: str
    tz: str
    source_dirs: tuple[str, ...]
    applet: str
    notes: str = ""

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.tz)


STATIONS: tuple[Station, ...] = (
    Station(
        station_id="phumy2",
        display_name="Phu My Hung #2",
        location="Phu My Hung, Ho Chi Minh City, Vietnam",
        tz="Asia/Ho_Chi_Minh",
        source_dirs=("phumy2", "phumy2a", "phumy2b"),
        applet="IFTTT_phumy2",
        notes=(
            "Split across three archive folders because Google Sheets split the "
            "sheet at 2000 rows. Continuous June 2020 - February 2024."
        ),
    ),
    Station(
        station_id="aisvn",
        display_name="AISVN #1",
        location="Nha Be, Ho Chi Minh City, Vietnam",
        tz="Asia/Ho_Chi_Minh",
        source_dirs=("aisvn",),
        applet="IFTTT_aisvn",
        notes=(
            "11-channel logger. Column meaning and sensor scaling both change "
            "during the record -- see etl.normalize.units for the regime table."
        ),
    ),
    Station(
        station_id="aisvn2",
        display_name="AISVN #2",
        location="Nha Be, Ho Chi Minh City, Vietnam",
        tz="Asia/Ho_Chi_Minh",
        source_dirs=("aisvn2",),
        applet="IFTTT_aisvn2",
        notes="8-channel logger, values in millivolts/milliamps until normalised.",
    ),
    Station(
        station_id="aisvn-solar",
        display_name="AISVN Solar (archived applet)",
        location="Nha Be, Ho Chi Minh City, Vietnam",
        tz="Asia/Ho_Chi_Minh",
        source_dirs=("AISVN_Solar",),
        applet="IFTTT_AISVN_Solar",
        notes="Superseded by aisvn. May 2020 only. Carries a second column block.",
    ),
    Station(
        station_id="maker-webhooks",
        display_name="Maker Webhooks (archived applet)",
        location="Ho Chi Minh City, Vietnam",
        tz="Asia/Ho_Chi_Minh",
        source_dirs=("Maker_Webhooks_Events",),
        applet="IFTTT_Maker_Webhooks_Events",
        notes="Superseded by aisvn. June 2020 only. Heavy -992 sentinel usage.",
    ),
    Station(
        station_id="solar-2020-05",
        display_name="Solar bench (2020-05-16 sheet)",
        location="Ho Chi Minh City, Vietnam",
        tz="Asia/Ho_Chi_Minh",
        source_dirs=("Solar_2020-05-16",),
        applet="IFTTT_Solar_2020-05-16",
        notes="Bench test sheet, May-June 2020. Has an 'event' column (solar_reading).",
    ),
    Station(
        station_id="voltage-phumy",
        display_name="Phu My Hung voltage calibration",
        location="Phu My Hung, Ho Chi Minh City, Vietnam",
        tz="Asia/Ho_Chi_Minh",
        source_dirs=("Voltage_phumy",),
        applet="Voltage_phumy",
        notes=(
            "Bench calibration of the ADC->voltage conversion, not a station. "
            "Contains free-text discharge-test notes. Unit mapping unconfirmed."
        ),
    ),
    Station(
        station_id="test",
        display_name="Test bench",
        location="unknown",
        tz="Asia/Ho_Chi_Minh",
        source_dirs=("test",),
        applet="IFTTT_test",
        notes=(
            "NOT a solar station. Mixes at least three unrelated schemas "
            "(solar channels, a nix/temp/wifi probe, and a millis counter). "
            "Treat with suspicion; excluded from published exports by default."
        ),
    ),
)

BY_SOURCE_DIR: dict[str, Station] = {d: s for s in STATIONS for d in s.source_dirs}
BY_ID: dict[str, Station] = {s.station_id: s for s in STATIONS}

#: Stations that should not be published as solar production data.
NON_PRODUCTION = frozenset({"test", "voltage-phumy"})


def station_for_dir(name: str) -> Station | None:
    return BY_SOURCE_DIR.get(name)

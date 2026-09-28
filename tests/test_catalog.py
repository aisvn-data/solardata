"""The catalog: the one declaration per (station, channel) everything else reads.

These are the checks 0.8 could not make, because they are about the shape of the
declaration rather than about any single value.  A band that fires on most of a
record, a channel published in a unit the site then divides again, a layout two
files of one width disagree about -- each of those was possible in 0.8 and each
of them produced a chart that looked fine.
"""

from __future__ import annotations

import re
import unittest
from typing import ClassVar

from etl import catalog
from etl.catalog import BAND_FIRE_FRACTION, BY_ID, STATIONS, table_name


class TestStationRegistry(unittest.TestCase):
    def test_eight_stations(self) -> None:
        self.assertEqual(len(STATIONS), 8)
        self.assertEqual(
            sorted(BY_ID),
            [
                "aisvn",
                "aisvn-solar",
                "aisvn2",
                "maker-webhooks",
                "phumy2",
                "solar-2020-05",
                "test",
                "voltage-phumy",
            ],
        )

    def test_ids_and_tables_are_unique(self) -> None:
        ids = [s.station_id for s in STATIONS]
        self.assertEqual(len(ids), len(set(ids)))
        tables = [table_name(s.station_id) for s in STATIONS]
        self.assertEqual(len(tables), len(set(tables)))

    def test_table_names_are_legal_sql_identifiers(self) -> None:
        # Hyphens are not legal unquoted in SQL, and every generated DDL
        # interpolates the name without quoting it.
        for station in STATIONS:
            name = station.table
            self.assertRegex(name, r"^s_[a-z0-9_]+$", f"{name} is not a bare identifier")

    def test_every_station_is_ho_chi_minh(self) -> None:
        for station in STATIONS:
            self.assertEqual(station.tz, "Asia/Ho_Chi_Minh", station.station_id)

    def test_two_stations_are_not_solar_production(self) -> None:
        bench = sorted(s.station_id for s in STATIONS if not s.production)
        self.assertEqual(bench, ["test", "voltage-phumy"])


class TestLayouts(unittest.TestCase):
    def test_no_station_has_two_layouts_of_the_same_width(self) -> None:
        # This is what makes `(station, width)` a total lookup. If it ever stops
        # holding, a headerless file's column meanings become ambiguous and the
        # ingest has to fall back to guessing -- which is the 0.8 failure that
        # could discard 90% of an archive's measurements while every timestamp
        # still ingested and nothing reported a problem.
        for station in STATIONS:
            widths = [layout.n_columns for layout in station.layouts]
            self.assertEqual(
                len(widths),
                len(set(widths)),
                f"{station.station_id} has two layouts of the same width",
            )

    def test_every_layout_names_only_channels_the_station_declares(self) -> None:
        for station in STATIONS:
            declared = station.by_name
            for layout in station.layouts:
                for name in layout.channels:
                    self.assertIn(name, declared, f"{station.station_id}/{layout.header} {name}")
                for column in layout.header:
                    self.assertTrue(column, f"{station.station_id} has an unnamed header")

    def test_every_layout_header_matches_the_archive(self) -> None:
        # Pinned because these are claims about 364 files that no test can read
        # without taking fifty seconds. `etl.audit` checks them against the real
        # archive on every build; this is the cheap tripwire in between.
        expected = {
            "aisvn": ("solar battery current power load wind temp solar2 LiPo boot", 11),
            "aisvn2": ("solar3 battery2 currentA currentB LiPo2 load boot", 8),
            "aisvn-solar": ("solar battery load_1 load_2 LiPo wind dump boot", 9),
            "phumy2": ("solar2 current2 power temp LiPo2 boot", 7),
            "solar-2020-05": ("event digital voltage LiPo", 5),
            "test": ("nix temp_c wifi_tx_ms", 4),
            "voltage-phumy": ("raw voltage millis()", 4),
        }
        for station_id, (header, width) in expected.items():
            station = BY_ID[station_id]
            self.assertEqual(len(station.layouts), 1, station_id)
            layout = station.layouts[0]
            self.assertEqual(" ".join(layout.header), header, station_id)
            self.assertEqual(layout.n_columns, width, station_id)

    def test_only_maker_webhooks_has_two_layouts(self) -> None:
        # It is the one station whose applet added a column mid-record, and the
        # two widths are what tells the two apart.
        two = sorted(s.station_id for s in STATIONS if len(s.layouts) == 2)
        self.assertEqual(two, ["maker-webhooks"])
        widths = sorted(layout.n_columns for layout in BY_ID["maker-webhooks"].layouts)
        self.assertEqual(widths, [10, 11])

    def test_the_eleven_column_maker_layout_adds_solar2(self) -> None:
        # Position matters: the added column sits between `dump` and `LiPo`, so
        # reading the 11-column files with the 10-column channel order would put
        # LiPo's value in solar2 and wind's in dump_adc. Both would then be
        # banded, in-range, wrong -- the worst kind of wrong.
        station = BY_ID["maker-webhooks"]
        wide = station.layout_for(11)
        assert wide is not None
        self.assertEqual(wide.channels[7], "solar2_v")
        self.assertEqual(wide.channels[8], "lipo_v")
        narrow = station.layout_for(10)
        assert narrow is not None
        self.assertEqual(narrow.channels[7], "lipo_v")
        self.assertNotIn("solar2_v", narrow.channels)


class TestChannels(unittest.TestCase):
    def test_channel_names_are_unique_within_a_station(self) -> None:
        for station in STATIONS:
            names = [c.name for c in station.channels]
            self.assertEqual(len(names), len(set(names)), station.station_id)

    def test_every_band_is_ordered(self) -> None:
        for station in STATIONS:
            for ch in station.channels:
                if not ch.band:
                    continue
                lo, hi = ch.band
                if lo is not None and hi is not None:
                    self.assertLess(lo, hi, f"{station.station_id}.{ch.name} has lo >= hi")

    def test_every_band_has_a_note(self) -> None:
        # A band with no note is a number nobody has defended, and 0.8 had a
        # dozen of them. The note is where the reasoning lives, so an empty one
        # means the band was typed rather than decided.
        for station in STATIONS:
            for ch in station.channels:
                if ch.band and (ch.band[0] is not None or ch.band[1] is not None):
                    self.assertTrue(
                        ch.band_note.strip(),
                        f"{station.station_id}.{ch.name} is banded and has no note",
                    )
                    self.assertGreater(
                        len(ch.band_note.strip()), 20, f"{station.station_id}.{ch.name}"
                    )

    def test_a_bandless_channel_says_why(self) -> None:
        for station in STATIONS:
            for ch in station.channels:
                if ch.band:
                    continue
                self.assertTrue(
                    ch.band_note.strip(),
                    f"{station.station_id}.{ch.name} has no band and no reason",
                )

    def test_every_hidden_channel_states_a_reason_and_a_sentence(self) -> None:
        allowed = {"not_measurement", "constant", "unresolved_unit", "text_label"}
        for station in STATIONS:
            for ch in station.channels:
                if ch.publish:
                    self.assertIsNone(ch.exclude, f"{station.station_id}.{ch.name}")
                    continue
                self.assertIn(ch.exclude, allowed, f"{station.station_id}.{ch.name}")
                self.assertGreater(len(ch.exclude_note), 40, f"{station.station_id}.{ch.name}")

    def test_a_hidden_channel_carries_no_band(self) -> None:
        # A band on a channel that is not published is a claim about nothing: it
        # can only fire in a report row a reader has already been told to ignore.
        for station in STATIONS:
            for ch in station.channels:
                if not ch.publish:
                    self.assertIsNone(
                        ch.band, f"{station.station_id}.{ch.name} is hidden but banded"
                    )

    def test_every_scale_is_a_positive_number(self) -> None:
        for station in STATIONS:
            for ch in station.channels:
                self.assertGreater(ch.scale, 0, f"{station.station_id}.{ch.name}")
                if ch.scale != 1.0:
                    self.assertTrue(
                        ch.raw_unit,
                        f"{station.station_id}.{ch.name} is scaled and must name its raw unit",
                    )

    def test_the_only_scales_are_confirmed_millivolt_and_milliamp_conversions(self) -> None:
        # Every non-1.0 scale in the archive is a divisor of 1000 or 100: a
        # collector that logged millivolts or milliamps as integers. A scale that
        # is not one of those is a guess, and the catalog is not where guesses go.
        for station in STATIONS:
            for ch in station.channels:
                if ch.scale == 1.0:
                    continue
                self.assertIn(
                    ch.scale,
                    (0.001, 0.002),
                    f"{station.station_id}.{ch.name} has scale {ch.scale}, which is not a "
                    "confirmed divisor",
                )

    def test_every_temperature_is_in_degrees_with_no_scale(self) -> None:
        # The 0.9 finding. 0.8 multiplied aisvn's and phumy2's by 10 and test's by
        # 100, and banded each in the multiplied unit, so every reading landed
        # inside a band that was wrong by the same factor as the value: two errors
        # cancelling, and the record looking banded when it was only rescaled.
        # The sheets write degrees -- 32.5, 24.2, 29.47 -- and that is the unit
        # they are stored and published in.
        #
        # The ceiling is per station, because the ceiling is a claim about where
        # the probe is. aisvn's probe stands in shadow, so the collector put it at
        # 40 degC; phumy2's and the bench probe's are left at 60. 0.9.0 asserted a
        # single 60 for all three, which meant aisvn's 40-63 degC readings were
        # inside a band for a probe that cannot reach it.
        CEILINGS = {"aisvn": 40.0, "phumy2": 60.0, "test": 60.0}
        found = 0
        for station in STATIONS:
            for ch in station.channels:
                if ch.name != "temp_c":
                    continue
                found += 1
                self.assertEqual(ch.unit, "degC", f"{station.station_id}.temp_c")
                self.assertEqual(ch.scale, 1.0, f"{station.station_id}.temp_c")
                self.assertIsNone(ch.raw_unit, f"{station.station_id}.temp_c is not converted")
                self.assertEqual(
                    ch.band,
                    (0.0, CEILINGS[station.station_id]),
                    f"{station.station_id}.temp_c",
                )
        self.assertEqual(found, 3, "three stations record a temperature")

    def test_a_unitless_channel_claims_no_unit(self) -> None:
        for station in STATIONS:
            for ch in station.channels:
                if ch.exclude == "unresolved_unit":
                    self.assertEqual(ch.unit, "", f"{station.station_id}.{ch.name}")
                    self.assertEqual(ch.scale, 1.0, f"{station.station_id}.{ch.name}")

    def test_counters_carry_min_and_max_and_never_a_mean(self) -> None:
        # A mean across a reboot averages two boot sessions into a number that
        # never happened.
        for station in STATIONS:
            for ch in station.channels:
                if ch.counter:
                    self.assertEqual(ch.stats, ("min", "max"), f"{station.station_id}.{ch.name}")
                    self.assertIsNone(ch.band, f"{station.station_id}.{ch.name} is banded")

    def test_a_text_channel_is_not_aggregated(self) -> None:
        for station in STATIONS:
            for ch in station.channels:
                if ch.kind == "text":
                    self.assertEqual(ch.stats, (), f"{station.station_id}.{ch.name}")
                    self.assertFalse(ch.publish, f"{station.station_id}.{ch.name}")

    def test_decimals_suit_the_unit(self) -> None:
        for station in STATIONS:
            for ch in station.channels:
                if ch.kind in ("count", "raw", "digital") or ch.counter:
                    self.assertEqual(ch.decimals, 0, f"{station.station_id}.{ch.name}")
                else:
                    self.assertGreaterEqual(ch.decimals, 1, f"{station.station_id}.{ch.name}")


class TestTheThreeExclusions(unittest.TestCase):
    """The three kinds of channel the site is not allowed to chart."""

    def test_wind_is_charted_where_the_collector_confirmed_it_and_hidden_where_constant(
        self,
    ) -> None:
        # Three stations record a `wind_v`. The collector confirmed it as a power
        # measurement in watts at aisvn and maker-webhooks, so it is charted there
        # and banded 0-50 W. aisvn-solar's is identically zero for all 13,788 of
        # its readings, so it is hidden as `constant` rather than charted as a
        # flat line at zero, which is a shape, not a measurement.
        #
        # 0.9.0 asserted the opposite at all three on the grounds that the values
        # were not a plausible *voltage* -- and aisvn's 29.8 and maker-webhooks'
        # 14.7 became watts once the unit was the collector's to confirm rather
        # than the reader's to assume.
        charted = {
            s.station_id for s in STATIONS if "wind_v" in s.by_name and s.channel("wind_v").publish
        }
        self.assertEqual(sorted(charted), ["aisvn", "maker-webhooks"])
        for station_id in charted:
            channel = BY_ID[station_id].channel("wind_v")
            self.assertEqual(channel.unit, "W", station_id)
            self.assertEqual(channel.band, (0.0, 50.0), station_id)
            self.assertIn("avg", channel.stats, station_id)
        hidden = BY_ID["aisvn-solar"].channel("wind_v")
        self.assertFalse(hidden.publish)
        self.assertEqual(hidden.exclude, "constant")

    def test_aisvn_solar_wind_is_excluded_for_being_constant(self) -> None:
        # The user asked for a rule rather than a list of names, so the reason has
        # to be the *kind* of exclusion: this one never varies. `etl.audit`
        # re-checks the constancy on the real archive, so it cannot go stale.
        channel = BY_ID["aisvn-solar"].channel("wind_v")
        self.assertEqual(channel.exclude, "constant")

    def test_aisvn_solar_dump_adc_is_excluded_for_being_constant(self) -> None:
        self.assertEqual(BY_ID["aisvn-solar"].channel("dump_adc").exclude, "constant")

    def test_phumy2_power_is_excluded_for_not_being_a_measurement(self) -> None:
        channel = BY_ID["phumy2"].channel("power_w")
        self.assertFalse(channel.publish)
        self.assertEqual(channel.exclude, "not_measurement")
        self.assertIsNone(channel.band, "a band would assert a quantity the pin does not carry")
        self.assertEqual(channel.stats, (), "and it is not aggregated either")

    def test_aisvn_power_is_charted(self) -> None:
        # The same column name at a station where it *is* a measurement. This is
        # why the exclusion is per (station, channel) and not per column name.
        self.assertTrue(BY_ID["aisvn"].channel("power_w").publish)

    def test_the_unresolved_load_rails_are_not_charted(self) -> None:
        for name in ("load1_v", "load2_v"):
            channel = BY_ID["aisvn-solar"].channel(name)
            self.assertFalse(channel.publish, name)
            self.assertEqual(channel.exclude, "unresolved_unit", name)

    def test_exactly_six_channels_are_hidden(self) -> None:
        # Still six, but not the same six. 0.9.0 hid `wind_v` at all three
        # stations that record it, on the reasoning that the values were not a
        # plausible generator output. The collector has since confirmed `wind_v`
        # as a power measurement in watts at aisvn and maker-webhooks, so it is
        # charted there -- and that the reading was right and the *unit* was wrong
        # is precisely the mistake 0.8 made in the other direction, a plausible
        # number banded in the wrong unit.
        #
        # aisvn-solar's `wind_v` stays hidden, and now for a reason the data
        # supports rather than one the reader has to take on trust: it is
        # identically zero for all 13,788 of its readings.
        hidden = [f"{s.station_id}.{c.name}" for s in STATIONS for c in s.channels if not c.publish]
        self.assertEqual(
            sorted(hidden),
            [
                "aisvn-solar.dump_adc",
                "aisvn-solar.load1_v",
                "aisvn-solar.load2_v",
                "aisvn-solar.wind_v",
                "phumy2.power_w",
                "solar-2020-05.event",
            ],
        )
        constant = BY_ID["aisvn-solar"].channel("wind_v")
        self.assertEqual(constant.exclude, "constant")


class TestBandsAreQuietOrExplained(unittest.TestCase):
    """`BAND_FIRE_FRACTION` is the rule the audit enforces on the real archive.

    Here it is asserted against the observed distributions, recorded so the
    catalog's reasoning is checkable without a fifty-second archive scan.  If a
    band changes, one of these moves and the test says which.
    """

    #: (station, channel) -> (n_values, n_out_of_range), measured by the 0.9
    #: build over the real archive and recorded here so the catalog's reasoning is
    #: checkable without a fifty-second archive scan. \etl.audit\ recomputes the
    #: same numbers on every build; if one of these is wrong, that check fails.
    OBSERVED: ClassVar[dict[tuple[str, str], tuple[int, int]]] = {
        ("aisvn", "solar_v"): (77526, 1612),
        ("aisvn", "solar2_v"): (77526, 14107),
        ("aisvn", "battery_v"): (77526, 1717),
        ("aisvn", "current_a"): (77526, 1412),
        ("aisvn", "power_w"): (77525, 879),
        ("aisvn", "load_v"): (77526, 1612),
        ("aisvn", "wind_v"): (77526, 0),
        ("aisvn", "temp_c"): (62060, 155),
        ("aisvn", "lipo_v"): (77526, 14107),
        ("aisvn2", "solar3_v"): (164097, 0),
        ("aisvn2", "battery2_v"): (164098, 1070),
        ("aisvn2", "current_a_chA"): (153770, 2915),
        ("aisvn2", "lipo2_v"): (164097, 0),
        ("aisvn2", "load_v"): (164097, 0),
        ("aisvn-solar", "solar_v"): (13788, 0),
        ("aisvn-solar", "battery_v"): (13788, 7),
        ("aisvn-solar", "lipo_v"): (13788, 0),
        ("maker-webhooks", "solar_v"): (6649, 17),
        ("maker-webhooks", "solar2_v"): (2583, 0),
        ("maker-webhooks", "battery_v"): (8529, 138),
        ("maker-webhooks", "load_v"): (5686, 0),
        ("maker-webhooks", "wind_v"): (4935, 22),
        ("maker-webhooks", "lipo_v"): (8535, 4),
        ("phumy2", "solar2_v"): (195954, 0),
        ("phumy2", "current2_a"): (416088, 0),
        ("phumy2", "temp_c"): (416083, 466),
        ("phumy2", "lipo2_v"): (416083, 5),
        ("solar-2020-05", "lipo_v"): (12920, 148),
        ("test", "temp_c"): (33377, 0),
    }

    def test_every_banded_published_channel_is_accounted_for(self) -> None:
        declared = {
            (s.station_id, c.name) for s in STATIONS for c in s.channels if c.band and c.publish
        }
        self.assertEqual(declared, set(self.OBSERVED))

    def test_no_band_fires_without_a_note(self) -> None:
        for (station_id, channel), (n, out_of_range) in self.OBSERVED.items():
            fraction = out_of_range / n
            if fraction <= BAND_FIRE_FRACTION:
                continue
            ch = BY_ID[station_id].channel(channel)
            self.assertTrue(
                ch.band_note.strip(),
                f"{station_id}.{channel} fires on {fraction:.1%} of its record and "
                "has no note explaining why the fire is the finding",
            )

    def test_a_stated_count_in_a_band_note_is_the_current_one(self) -> None:
        # A `band_note` is documentation a reader will believe. When it states a
        # count in the form "N of M", those two numbers are a claim about this
        # build, and a claim that is quietly stale is worse than no claim: it is
        # the one thing in the catalog that looks measured and is not.
        #
        # The rule this asserts is therefore narrow and easy to hold to: *every*
        # "N of M" in a band note is the current count. A historical figure is
        # written differently -- "1,656 readings -- 19% of the channel -- would
        # have fired" -- so it cannot be mistaken for the present. The only way
        # to state a past count unambiguously is not to use this shape.
        #
        # `aisvn-solar.battery_v` was the one that caught it: its note said 8
        # while the build counted 7, left over from before the rollup was fixed
        # and started reporting this channel at all. `maker-webhooks.lipo_v`
        # said 1,656, which was true of a band it no longer has.
        mismatches: list[str] = []
        for (station_id, channel), (n_values, n_out_of_range) in self.OBSERVED.items():
            note = BY_ID[station_id].channel(channel).band_note or ""
            for stated, total in re.findall(r"(\d[\d,]*)\s+of\s+(\d[\d,]*)", note):
                stated_n = int(stated.replace(",", ""))
                total_n = int(total.replace(",", ""))
                if (stated_n, total_n) != (n_out_of_range, n_values):
                    mismatches.append(
                        f"{station_id}.{channel}: note says {stated_n} of {total_n}, "
                        f"the build says {n_out_of_range} of {n_values}"
                    )
        self.assertEqual(mismatches, [], "band notes whose stated count has gone stale")

    def test_the_whole_archive_is_not_mostly_flags(self) -> None:
        # The number this rewrite exists for. 0.8 flagged 631,252 readings out of
        # range, 416,088 of them because a quarter-of-an-amp current sensor was
        # tested against a +/-50 A band.
        #
        # The total is now 40,393, and it moved *up* from 3,695 -- deliberately,
        # and entirely because the collector tightened two bands that 0.9.0 had
        # widened to stop them firing. `aisvn.solar2_v` at 0-15 V and `aisvn.lipo_v`
        # at 0-5 V each flag 14,107 readings: exactly 19.5 V and exactly 6.84 V,
        # repeated. Those are plateaus on a rail, not a unit error, and a band
        # that does not ring them is a band that has been widened to be quiet.
        #
        # So the invariant is not "few" but "the flags are accounted for": every
        # channel firing above BAND_FIRE_FRACTION carries a note saying why the
        # fire is the finding (`test_no_band_fires_without_a_note`), and setting the
        # two declared plateaus aside, the archive flags well under 1% of its
        # readings. That is the property that 0.8 violated, and it still holds.
        total_out = sum(o for _, o in self.OBSERVED.values())
        self.assertEqual(total_out, 40393)
        plateaus = {("aisvn", "solar2_v"), ("aisvn", "lipo_v")}
        remainder = sum(
            o
            for (station_id, channel), (_n, o) in self.OBSERVED.items()
            if (station_id, channel) not in plateaus
        )
        self.assertEqual(remainder, 12179)
        # 1.7%, against 0.8's 86% (631,252 of 731,885). The two that carry the
        # rest are `aisvn.solar2_v` and `aisvn.lipo_v`, the declared plateaus.
        self.assertLess(
            remainder / 731885,
            0.02,
            "under 2% once the two declared plateaus are set aside; 0.8 flagged 86%",
        )

    def test_the_two_large_fires_are_the_declared_plateaus(self) -> None:
        # Naming them, so a third band firing at 18% cannot be added without this
        # test moving and someone having to say why.
        for key in (("aisvn", "solar2_v"), ("aisvn", "lipo_v")):
            n, out = self.OBSERVED[key]
            self.assertGreater(out / n, BAND_FIRE_FRACTION, key)
            self.assertIn(key, {("aisvn", "solar2_v"), ("aisvn", "lipo_v")})

    def test_the_worst_station_is_not_the_worst_offender(self) -> None:
        # phumy2 is 57% of the archive and contributes 0.9% of its own readings to
        # the out-of-range total. In 0.8 it contributed all 416,088 of them.
        phumy2_out = sum(
            out
            for (station_id, _channel), (_n, out) in self.OBSERVED.items()
            if station_id == "phumy2"
        )
        phumy2_n = sum(
            n
            for (station_id, _channel), (n, _out) in self.OBSERVED.items()
            if station_id == "phumy2"
        )
        self.assertLess(phumy2_out / phumy2_n, 0.003)


class TestOpenQuestions(unittest.TestCase):
    def test_every_station_records_its_unresolved_questions(self) -> None:
        # The list a human still has to answer. It is not allowed to be empty for
        # a station, because 0.8 kept the open questions in AGENTS.md where a
        # reader of the database would never find them.
        for station in STATIONS:
            self.assertTrue(
                station.open_questions,
                f"{station.station_id} records no open question, which cannot be right",
            )
            for question in station.open_questions:
                self.assertGreater(len(question), 40, f"{station.station_id}: {question!r}")

    def test_the_notes_are_worth_reading(self) -> None:
        for station in STATIONS:
            self.assertGreater(len(station.notes), 30, station.station_id)


class TestBandArithmetic(unittest.TestCase):
    def test_in_band_handles_an_open_ended_band(self) -> None:
        channel = catalog.Channel(name="x", label="X", kind="raw", description="d", unit="")
        self.assertTrue(channel.in_band(1.0), "no band means never out of range")
        channel = catalog.Channel(
            name="x", label="X", kind="voltage", description="d", unit="V", band=(0.0, None)
        )
        self.assertTrue(channel.in_band(1000.0))
        self.assertFalse(channel.in_band(-0.1))
        channel = catalog.Channel(
            name="x", label="X", kind="voltage", description="d", unit="V", band=(None, 60.0)
        )
        self.assertTrue(channel.in_band(-100.0))
        self.assertFalse(channel.in_band(60.1))

    def test_a_band_boundary_is_inside(self) -> None:
        channel = catalog.Channel(
            name="x", label="X", kind="voltage", description="d", unit="V", band=(9.0, 16.0)
        )
        self.assertTrue(channel.in_band(9.0), "the lower bound is inside, not out")
        self.assertTrue(channel.in_band(16.0), "and so is the upper")
        self.assertFalse(channel.in_band(16.000001))

    def test_the_fire_fraction_is_one_percent(self) -> None:
        self.assertEqual(BAND_FIRE_FRACTION, 0.01)


if __name__ == "__main__":
    unittest.main()

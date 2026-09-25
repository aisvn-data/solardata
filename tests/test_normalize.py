"""Header mapping, sentinel handling, and plausibility flagging."""

from __future__ import annotations

import unittest

from etl.normalize.metrics import (
    METRIC_BY_COLUMN,
    build_row_mapping,
    map_header,
    map_headers,
)
from etl.normalize.quality import coerce_cell, coerce_number, looks_like_note, merge_flags
from etl.normalize.units import choose_scale, in_range_fraction, propose_regime


class TestHeaderMapping(unittest.TestCase):
    def test_every_archive_header_spelling_maps(self):
        # Collected from the 36 files that actually carry a header row.
        expected = {
            "time": None,  # handled by the caller
            "solar": "solar_v",
            "solar2": "solar2_v",
            "solar3": "solar3_v",
            "battery": "battery_v",
            "battery2": "battery2_v",
            "current": "current_a",
            "current2": "current2_a",
            "currentA": "current_a_chA",
            "currentB": "current_a_chB",
            "curA": "current_a_chA",
            "curB": "current_a_chB",
            "power": "power_w",
            "load": "load_v",
            "load_1": "load1_v",
            "load_2": "load2_v",
            "wind": "wind_v",
            "temp": "temp_c",
            "LiPo": "lipo_v",
            "LiPo2": "lipo2_v",
            "boot": "boot_count",
            "dump": "dump_adc",
            "digital": "digital_adc",
            "voltage": "voltage_adc",
            "raw": "adc_raw",
            "millis()": "millis_ms",
            "event": "event",
            "nix": "nix_raw",
            "wifi": "wifi_raw",
        }
        for raw, want in expected.items():
            got = map_header(1, raw)
            self.assertEqual(got.column, want, f"{raw!r} -> {got.column!r}")
            if want is not None:
                self.assertEqual(got.confidence, "high", raw)

    def test_case_insensitive(self):
        self.assertEqual(map_header(1, "LIPO").column, "lipo_v")
        self.assertEqual(map_header(1, "Solar").column, "solar_v")

    def test_unmapped_headers_are_refused_with_a_reason(self):
        mapping = map_header(1, "mystery_channel")
        self.assertIsNone(mapping.column)
        self.assertIn("unrecognised", mapping.reason)
        self.assertEqual(mapping.confidence, "low")

    def test_ambiguous_header_is_refused(self):
        mapping = map_header(1, "millis")
        self.assertIsNone(mapping.column)
        self.assertIn("ambiguous", mapping.reason)

    def test_every_mapped_column_exists_in_the_registry(self):
        header = (
            "time",
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
        )
        for mapping in map_headers(header):
            if mapping.column:
                self.assertIn(mapping.column, METRIC_BY_COLUMN, mapping.column)

    def test_headerless_file_yields_placeholders_not_guesses(self):
        mappings = build_row_mapping(None, n_columns=8)
        self.assertEqual(len(mappings), 7)  # minus the time column
        self.assertTrue(all(m.column is None for m in mappings))
        self.assertTrue(all("no header" in m.reason for m in mappings))

    def test_empty_and_trailing_header_cells_are_handled(self):
        mappings = map_headers(("time", "solar", "battery"))
        self.assertEqual([m.column for m in mappings], ["solar_v", "battery_v"])


class TestCoerceNumber(unittest.TestCase):
    def test_plain_numbers(self):
        self.assertEqual(coerce_number("12.72", METRIC_BY_COLUMN["battery_v"]).value, 12.72)
        self.assertEqual(coerce_number("0", METRIC_BY_COLUMN["power_w"]).value, 0.0)
        self.assertEqual(coerce_number("-6.43", METRIC_BY_COLUMN["current_a"]).value, -6.43)

    def test_empty_is_null_not_zero(self):
        self.assertIsNone(coerce_number("", METRIC_BY_COLUMN["solar_v"]).value)
        self.assertIsNone(coerce_number("   ", METRIC_BY_COLUMN["solar_v"]).value)

    def test_ifttt_sentinels_become_null(self):
        # -992 and -1 are "input floating" markers, not measurements.
        for raw in ("-992", "-992.0", "-1", "-1.0"):
            result = coerce_number(raw, METRIC_BY_COLUMN["wind_v"])
            self.assertIsNone(result.value, raw)
            self.assertIn("sentinel", result.flags, raw)

    def test_adc_rail_sentinel_becomes_null_in_any_spelling(self):
        # aisvn.temp_c logs 342.1 fourteen thousand times: a saturating float32
        # conversion, not a temperature. The reader normalises integral floats
        # to their integer spelling, so a cell holding 342.0 arrives as "342" --
        # matching on text alone would miss it.
        for raw in ("342.1", "342.0", "342"):
            result = coerce_number(raw, METRIC_BY_COLUMN["temp_c"])
            self.assertIsNone(result.value, raw)
            self.assertIn("sentinel", result.flags, raw)

    def test_zero_is_preserved_not_treated_as_sentinel(self):
        # 0 W at night is a real measurement and must survive.
        result = coerce_number("0.0", METRIC_BY_COLUMN["power_w"])
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.flags, ())

    def test_out_of_range_is_flagged_but_kept(self):
        # A 29 V "battery" is the calibration-drift signal we must not lose.
        result = coerce_number("29.12", METRIC_BY_COLUMN["battery_v"])
        self.assertEqual(result.value, 29.12)
        self.assertIn("out_of_range", result.flags)

    def test_long_free_text_is_routed_to_notes(self):
        collected: list[str] = []
        note = "discharge 7.5 Ah with 0.3A - 25 hours, starting at 18:00 on July 11th"
        result = coerce_number(note, METRIC_BY_COLUMN["voltage_adc"], free_text_out=collected)
        self.assertIsNone(result.value)
        self.assertIn("free_text", result.flags)
        self.assertEqual(collected, [note])

    def test_text_in_an_unmapped_column_is_still_null(self):
        self.assertIsNone(coerce_number("hello", None).value)

    def test_text_kind_columns_are_not_coerced_to_numbers(self):
        # The 'event' column holds the IFTTT applet name, not a number.  Routing
        # it through the numeric path nulled all 12,920 labels in solar-2020-05.
        result = coerce_cell("solar_reading", METRIC_BY_COLUMN["event"])
        self.assertEqual(result.value, "solar_reading")
        self.assertEqual(result.flags, ())

    def test_coerce_cell_falls_through_to_numeric_for_real_metrics(self):
        result = coerce_cell("-992", METRIC_BY_COLUMN["battery_v"])
        self.assertIsNone(result.value)
        self.assertIn("sentinel", result.flags)

    def test_coerce_cell_handles_empty_text_columns(self):
        self.assertIsNone(coerce_cell("", METRIC_BY_COLUMN["event"]).value)

    def test_merge_flags_is_order_stable_and_deduplicated(self):
        self.assertEqual(
            merge_flags(("sentinel",), ("out_of_range",), ("sentinel",)), "sentinel,out_of_range"
        )
        self.assertEqual(merge_flags((), ()), "")


class TestNoteDetection(unittest.TestCase):
    """Side-block columns mix human notes with sheet plumbing; separate them.

    The real cases come from data/raw/aisvn/IFTTT_aisvn (25).xlsx column L and
    data/raw/Voltage_phumy/Voltage_phumy (2).xlsx columns F-H.
    """

    def test_real_notes_are_recognised(self):
        for note in (
            "this all is just garbage",
            "STROMAUSFALL!!",  # 13 chars, short but real
            "leave home",  # 10 chars
            "arrive at school",
            "Pin 4 is temperature - calibrated ...",
            "discharge 7.5 Ah with 0.3A - 25 hours, starting at 18:00 on July 11th",
        ):
            self.assertTrue(looks_like_note(note), note)

    def test_sheet_plumbing_is_rejected(self):
        for junk in (
            "time",
            "date",
            "Time",
            "DATE",  # headers, incl. mid-file repeats
            "04:08AM",
            "10:00:00",
            "23:59",  # summary-table clocks
            "2020-05-16",
            "2020/05/16",
            "11.286",
            "-992",
            "0",  # numbers
            "",
            "   ",
            "solar",
            "battery",
            "temp",  # metric header words
            "July 14, 2020 at 10:12AM",  # the observation clock
        ):
            self.assertFalse(looks_like_note(junk), repr(junk))


class TestScaleDetection(unittest.TestCase):
    def test_in_range_fraction(self):
        # 2.0 is inside [0, 60]; 200.0 is not.
        self.assertEqual(in_range_fraction([10.0, 14.0, 200.0], 0.0, 60.0), 2 / 3)
        self.assertEqual(in_range_fraction([], 0.0, 1.0), 0.0)

    def test_millivolts_scale_is_proposed(self):
        # aisvn2 logs battery as 12597, which is 12.597 V.
        values = [12597.0, 12602.0, 12606.0, 12602.0] * 100
        hypothesis = choose_scale(values, 9.0, 16.0)
        self.assertIsNotNone(hypothesis)
        self.assertEqual(hypothesis.scale, 0.001)
        self.assertAlmostEqual(hypothesis.median_after, 12.6, places=2)

    def test_already_scaled_values_propose_nothing(self):
        values = [12.7, 13.1, 12.9, 0.0, 14.2] * 100
        self.assertIsNone(choose_scale(values, 9.0, 16.0))

    def test_no_scale_is_proposed_when_none_reaches_the_band(self):
        # The aisvn October-2020 window: "battery" reads 29.12 V.  No power of
        # ten lands a 29 V reading inside a 3S band, so proposing one would be
        # a guess.  The per-reading out_of_range flag carries the signal instead.
        values = [29.01, 29.06, 29.12, 0.95] * 200
        self.assertIsNone(choose_scale(values, 9.0, 16.0))

    def test_regime_requires_enough_samples(self):
        self.assertIsNone(
            propose_regime(
                "aisvn2",
                "battery_v",
                "V",
                "2020-01-01T00:00:00Z",
                None,
                [12597.0, 12600.0],
                9.0,
                16.0,
            )
        )

    def test_regime_is_unconfirmed_by_default(self):
        values = [12597.0, 12600.0, 12604.0] * 200
        regime = propose_regime(
            "aisvn2",
            "battery_v",
            "V",
            "2020-06-23T00:00:00Z",
            "2020-07-01T00:00:00Z",
            values,
            9.0,
            16.0,
        )
        self.assertIsNotNone(regime)
        self.assertEqual(regime.status, "unconfirmed")
        self.assertEqual(regime.scale, 0.001)
        self.assertIn("outside plausible", regime.notes)


if __name__ == "__main__":
    unittest.main()

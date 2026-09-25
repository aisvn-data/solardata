"""Timestamp parsing: the format is load-bearing for every join downstream."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from etl.readers.times import (
    TimestampError,
    iso_local,
    iso_utc,
    looks_like_header,
    looks_like_timestamp,
    parse_local,
    parse_to_pair,
    to_utc,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")


class TestParseLocal(unittest.TestCase):
    def test_all_month_names_present_in_the_archive(self):
        months = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        for index, month in enumerate(months, start=1):
            raw = f"{month} 14, 2020 at 10:12AM"
            self.assertEqual(parse_local(raw), datetime(2020, index, 14, 10, 12), month)

    def test_single_digit_day(self):
        self.assertEqual(parse_local("May 1, 2021 at 01:32AM"), datetime(2021, 5, 1, 1, 32))

    def test_12_hour_clock_boundaries(self):
        self.assertEqual(parse_local("May 1, 2021 at 12:00AM").hour, 0)
        self.assertEqual(parse_local("May 1, 2021 at 12:59AM").hour, 0)
        self.assertEqual(parse_local("May 1, 2021 at 12:00PM").hour, 12)
        self.assertEqual(parse_local("May 1, 2021 at 11:59PM").hour, 23)

    def test_lexicographic_ordering_is_wrong_so_we_parse(self):
        # The reason timestamps are parsed instead of sorted as text.
        april = "April 1, 2021 at 10:00AM"
        august = "August 1, 2021 at 10:00AM"
        self.assertLess(april, august)  # string comparison
        self.assertLess(parse_local(april), parse_local(august))  # real comparison

    def test_rejects_non_timestamps(self):
        for bad in ("time", "date", "", "2021-05-01 10:00", "May 2021", "nix"):
            with self.assertRaises(TimestampError, msg=bad):
                parse_local(bad)

    def test_rejects_impossible_dates(self):
        with self.assertRaises(TimestampError):
            parse_local("February 30, 2020 at 10:00AM")


class TestTimezoneConversion(unittest.TestCase):
    def test_vietnam_is_fixed_utc_plus_7(self):
        # No DST in Vietnam, so the UTC result is 7 hours behind local in both
        # July and January.  Checking the *converted* wall clock rather than
        # utcoffset(), which is zero once the value is in UTC.
        summer = to_utc(datetime(2020, 7, 1, 12, 0), VN)
        winter = to_utc(datetime(2021, 1, 15, 12, 0), VN)
        self.assertEqual(summer, datetime(2020, 7, 1, 5, 0, tzinfo=UTC))
        self.assertEqual(winter, datetime(2021, 1, 15, 5, 0, tzinfo=UTC))
        self.assertEqual(summer.hour, winter.hour)

    def test_utc_string_is_z_suffixed_and_correct(self):
        ts_utc, ts_local = parse_to_pair("July 14, 2020 at 10:12AM", VN)
        self.assertEqual(ts_utc, "2020-07-14T03:12:00Z")
        self.assertEqual(ts_local, "2020-07-14T10:12:00")

    def test_midnight_local_crosses_the_utc_date_line(self):
        ts_utc, _ = parse_to_pair("January 1, 2021 at 12:30AM", VN)
        self.assertEqual(ts_utc, "2020-12-31T17:30:00Z")

    def test_iso_helpers(self):
        moment = datetime(2020, 5, 16, 22, 52, tzinfo=UTC)
        self.assertEqual(iso_utc(moment), "2020-05-16T22:52:00Z")
        self.assertEqual(iso_local(moment.replace(tzinfo=None)), "2020-05-16T22:52:00")


class TestClassification(unittest.TestCase):
    def test_looks_like_timestamp(self):
        self.assertTrue(looks_like_timestamp("May 24, 2020 at 07:52AM"))
        self.assertFalse(looks_like_timestamp("time"))
        self.assertFalse(looks_like_timestamp("solar_reading"))

    def test_header_like_catches_the_repeated_rows_we_saw(self):
        # test/IFTTT_test (1).xlsx repeats these mid-file.
        for word in ("time", "Time", "date", "TIME", " date "):
            self.assertTrue(looks_like_header(word), word)
        self.assertFalse(looks_like_header("July 8, 2020 at 07:13AM"))


if __name__ == "__main__":
    unittest.main()

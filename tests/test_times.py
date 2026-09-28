"""Timestamps: the one format in the archive, and why it is parsed by regex.

The interesting case is not the happy path.  0.8 handed these cells to
``datetime.strptime``, and ``%B`` and ``%p`` both resolve out of the C library's
``LC_TIME`` tables, which are not loaded until something calls
``locale.setlocale``.  On Windows that usually has not happened, so *every* cell
in the archive failed to parse: the build reported zero readings and 738,358
rejected cells, and the shape of that failure reads as a data problem rather than
a locale one.

So the parser reads the fields out of the regex it already matched, and the
month names are a fixed tuple rather than a locale lookup.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from etl.readers import times

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class TestParse(unittest.TestCase):
    def test_the_archive_format(self) -> None:
        self.assertEqual(
            times.parse_local("July 14, 2020 at 10:12AM"),
            datetime(2020, 7, 14, 10, 12),
        )

    def test_twelve_hour_boundaries(self) -> None:
        # The two places a 12-hour clock and a 24-hour one part company, and both
        # have to be right or a reading moves by twelve hours.
        self.assertEqual(times.parse_local("December 1, 2020 at 12:00AM").hour, 0)
        self.assertEqual(times.parse_local("December 1, 2020 at 12:00PM").hour, 12)
        self.assertEqual(times.parse_local("December 1, 2020 at 01:00AM").hour, 1)
        self.assertEqual(times.parse_local("December 1, 2020 at 01:00PM").hour, 13)
        self.assertEqual(times.parse_local("December 1, 2020 at 11:59PM").hour, 23)

    def test_every_month_name(self) -> None:
        # All twelve, because a hardcoded tuple and a locale table do not agree
        # about what a month is called, and one wrong entry is one silent gap.
        for index, name in enumerate(times.MONTHS, start=1):
            text = f"{name.capitalize()} 15, 2021 at 09:30AM"
            self.assertEqual(times.parse_local(text).month, index, name)

    def test_leap_day(self) -> None:
        self.assertEqual(
            times.parse_local("February 29, 2024 at 06:00AM"), datetime(2024, 2, 29, 6, 0)
        )

    def test_single_digit_day_and_hour(self) -> None:
        self.assertEqual(times.parse_local("April 3, 2021 at 7:05AM"), datetime(2021, 4, 3, 7, 5))

    def test_surrounding_whitespace(self) -> None:
        self.assertEqual(
            times.parse_local("  July 14, 2020 at 10:12AM  "), datetime(2020, 7, 14, 10, 12)
        )

    def test_lowercase_ampm(self) -> None:
        # A cell the collector wrote by hand rather than by applet.
        self.assertEqual(
            times.parse_local("July 14, 2020 at 10:12pm"), datetime(2020, 7, 14, 22, 12)
        )


class TestRejects(unittest.TestCase):
    def test_a_header_word_is_not_a_timestamp(self) -> None:
        for word in ("time", "Time", "date", "timestamp", "DateTime"):
            self.assertFalse(times.looks_like_timestamp(word), word)
            self.assertTrue(times.looks_like_header(word), word)
            with self.assertRaises(times.TimestampError):
                times.parse_local(word)

    def test_an_impossible_date_is_rejected_not_rounded(self) -> None:
        with self.assertRaises(times.TimestampError) as caught:
            times.parse_local("February 30, 2020 at 10:12AM")
        self.assertIn("range", str(caught.exception))

    def test_a_thirteenth_hour_is_rejected(self) -> None:
        with self.assertRaises(times.TimestampError) as caught:
            times.parse_local("June 18, 2020 at 13:52AM")
        self.assertIn("12-hour", str(caught.exception))

    def test_a_zero_hour_is_rejected(self) -> None:
        with self.assertRaises(times.TimestampError):
            times.parse_local("June 18, 2020 at 00:30AM")

    def test_another_language_is_rejected_not_guessed_at(self) -> None:
        # A fixed month tuple cannot quietly map a foreign name onto a wrong
        # month, which is the failure a locale table would have.
        with self.assertRaises(times.TimestampError) as caught:
            times.parse_local("Januar 1, 2020 at 10:00AM")
        self.assertIn("unknown month", str(caught.exception))

    def test_prose_is_rejected(self) -> None:
        for text in ("", "   ", "STROMAUSFALL!!", "2020-07-14 10:12", "July 14 2020"):
            with self.assertRaises(times.TimestampError):
                times.parse_local(text)

    def test_a_number_is_rejected(self) -> None:
        with self.assertRaises(times.TimestampError):
            times.parse_local("342")


class TestUtcConversion(unittest.TestCase):
    def test_vietnam_is_utc_plus_seven_with_no_dst(self) -> None:
        # Correct for Vietnam and an assumption about every reading in the
        # archive, which is why the offset is stated rather than inferred.
        local = times.parse_local("July 14, 2020 at 10:12AM")
        utc = times.to_utc(local, TZ)
        self.assertEqual(utc, datetime(2020, 7, 14, 3, 12, tzinfo=UTC))
        self.assertEqual(utc.utcoffset().total_seconds(), 0)

    def test_the_offset_is_seven_hours_in_every_month(self) -> None:
        # No DST means no surprise in April or October. If Vietnam ever changes,
        # this is the test that says so before a reading moves by an hour.
        for month, day in ((1, 15), (4, 15), (7, 15), (10, 15), (12, 15)):
            local = times.parse_local(
                f"Month {day}, 2021 at 12:00PM".replace("Month", _month(month))
            )
            offset = local.replace(tzinfo=TZ).utcoffset()
            assert offset is not None
            self.assertEqual(offset.total_seconds(), 7 * 3600, _month(month))

    def test_both_clocks_come_from_one_parse(self) -> None:
        utc, local = times.parse_to_pair("July 14, 2020 at 10:12AM", TZ)
        self.assertEqual(utc, "2020-07-14T03:12:00Z")
        self.assertEqual(local, "2020-07-14T10:12:00")

    def test_the_utc_string_round_trips(self) -> None:
        for text in (
            "July 14, 2020 at 10:12AM",
            "January 1, 2021 at 12:00AM",
            "December 31, 2020 at 11:59PM",
        ):
            utc, _ = times.parse_to_pair(text, TZ)
            self.assertEqual(
                datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC),
                times.to_utc(times.parse_local(text), TZ),
                text,
            )


class TestOrdering(unittest.TestCase):
    def test_string_order_is_wrong_and_utc_order_is_right(self) -> None:
        # The reason every comparison goes through the parsed clock.  The trap is
        # not the month name -- `April` does happen to sort before `August` -- it
        # is the day: the sheets write `July 4` and `July 14` unpadded, so string
        # order puts the fourteenth before the fourth. A donor search that ordered
        # on the string would pick the wrong sibling's schema and discard the
        # readings that depend on it.
        cells = [
            "July 14, 2020 at 09:00AM",
            "July 4, 2020 at 10:12AM",
            "July 4, 2020 at 11:00AM",
        ]
        by_string = sorted(cells)
        by_clock = sorted(cells, key=lambda c: times.parse_to_pair(c, TZ)[0])
        self.assertEqual(by_string[0], "July 14, 2020 at 09:00AM", "string order is the trap")
        self.assertEqual(by_clock[0], "July 4, 2020 at 10:12AM")
        self.assertNotEqual(by_string, by_clock)

    def test_a_zero_padded_day_would_have_been_fine_which_is_why_it_is_not_assumed(self) -> None:
        # Both readings of the same day, 10:12 and 11:00. String order gets this
        # one right by luck; the day case above is the one that does not.
        cells = ["July 4, 2020 at 11:00AM", "July 4, 2020 at 10:12AM"]
        self.assertEqual(sorted(cells)[0], "July 4, 2020 at 10:12AM")

    def test_month_names_sort_correctly_but_days_do_not(self) -> None:
        # Documented so the next reader does not "fix" the ordering test by
        # reaching for the month names, which are not the problem.
        months = [
            "August 1, 2020 at 10:00AM",
            "April 1, 2020 at 10:00AM",
            "July 1, 2020 at 10:00AM",
        ]
        self.assertEqual(
            sorted(months, key=lambda c: times.parse_to_pair(c, TZ)[0])[0],
            "April 1, 2020 at 10:00AM",
        )

    def test_hour_and_day_keys(self) -> None:
        self.assertEqual(times.hour_key("2020-07-14T03:12:00Z"), "2020-07-14T03:00:00Z")
        self.assertEqual(times.day_key("2020-07-14"), "2020-07-14T00:00:00Z")
        self.assertEqual(times.year_of("2020-07-14T03:12:00Z"), 2020)


def _month(index: int) -> str:
    return times.MONTHS[index - 1].capitalize()


if __name__ == "__main__":
    unittest.main()

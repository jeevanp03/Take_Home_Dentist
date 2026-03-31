"""Tests for the natural-language date parser.

Uses a fixed reference date (Wednesday, 2026-04-01) so tests are
deterministic regardless of when they run.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.agent.date_parser import parse_date_expression


# Fixed reference: Wednesday, April 1, 2026
REF = date(2026, 4, 1)


# ---------------------------------------------------------------------------
# ISO passthrough
# ---------------------------------------------------------------------------

class TestISOPassthrough:
    def test_valid_iso(self):
        result = parse_date_expression("2026-04-15", REF)
        assert result == {"start": "2026-04-15", "end": "2026-04-15"}

    def test_iso_with_whitespace(self):
        result = parse_date_expression("  2026-04-15  ", REF)
        assert result == {"start": "2026-04-15", "end": "2026-04-15"}

    def test_invalid_iso_returns_none(self):
        assert parse_date_expression("not-a-date", REF) is None

    def test_empty_string_returns_none(self):
        assert parse_date_expression("", REF) is None

    def test_iso_sunday_no_skip(self):
        """ISO passthrough should NOT skip Sundays — return verbatim."""
        result = parse_date_expression("2026-04-05", REF)  # Sunday
        assert result == {"start": "2026-04-05", "end": "2026-04-05"}


# ---------------------------------------------------------------------------
# Relative expressions
# ---------------------------------------------------------------------------

class TestToday:
    def test_today_weekday(self):
        result = parse_date_expression("today", REF)
        assert result == {"start": "2026-04-01", "end": "2026-04-01"}

    def test_today_sunday_skips_to_monday(self):
        sunday = date(2026, 4, 5)
        result = parse_date_expression("today", sunday)
        assert result == {"start": "2026-04-06", "end": "2026-04-06"}


class TestTomorrow:
    def test_tomorrow(self):
        result = parse_date_expression("tomorrow", REF)
        assert result == {"start": "2026-04-02", "end": "2026-04-02"}

    def test_tomorrow_saturday_skips_sunday(self):
        saturday = date(2026, 4, 4)
        result = parse_date_expression("tomorrow", saturday)
        assert result == {"start": "2026-04-06", "end": "2026-04-06"}


class TestASAP:
    def test_asap_weekday(self):
        result = parse_date_expression("ASAP", REF)
        assert result is not None
        assert result["start"] == "2026-04-01"
        assert result["end"] == "2026-04-03"  # Wed + 2 = Fri (no Sunday skip needed)

    def test_asap_saturday(self):
        saturday = date(2026, 4, 4)
        result = parse_date_expression("asap", saturday)
        assert result is not None
        assert result["start"] == "2026-04-04"  # Saturday (office open)
        assert result["end"] == "2026-04-06"    # Sat+2=Mon (Mon is not Sunday, no skip)

    def test_asap_sunday(self):
        sunday = date(2026, 4, 5)
        result = parse_date_expression("ASAP", sunday)
        assert result is not None
        assert result["start"] == "2026-04-06"  # Monday
        assert result["end"] == "2026-04-08"    # Mon+2=Wed


class TestThisWeek:
    def test_this_week_wednesday(self):
        result = parse_date_expression("this week", REF)
        assert result is not None
        assert result["start"] == "2026-04-01"  # Wednesday
        assert result["end"] == "2026-04-04"    # Saturday

    def test_this_week_saturday(self):
        saturday = date(2026, 4, 4)
        result = parse_date_expression("this week", saturday)
        assert result is not None
        assert result["start"] == "2026-04-06"  # next Monday
        assert result["end"] == "2026-04-11"    # next Saturday

    def test_this_week_sunday(self):
        """Sunday: 'this week' has no remaining days, so returns next Mon-Sat."""
        sunday = date(2026, 4, 5)
        result = parse_date_expression("this week", sunday)
        assert result is not None
        assert result["start"] == "2026-04-06"  # Monday
        assert result["end"] == "2026-04-11"    # Saturday


class TestNextWeek:
    def test_next_week(self):
        result = parse_date_expression("next week", REF)
        assert result == {"start": "2026-04-06", "end": "2026-04-10"}

    def test_next_week_case_insensitive(self):
        result = parse_date_expression("Next Week", REF)
        assert result == {"start": "2026-04-06", "end": "2026-04-10"}

    def test_next_week_from_friday(self):
        friday = date(2026, 4, 3)
        result = parse_date_expression("next week", friday)
        assert result == {"start": "2026-04-06", "end": "2026-04-10"}


class TestEarlyNextWeek:
    def test_early_next_week(self):
        result = parse_date_expression("early next week", REF)
        assert result == {"start": "2026-04-06", "end": "2026-04-07"}

    def test_embedded_in_sentence(self):
        result = parse_date_expression("I'm free early next week", REF)
        assert result == {"start": "2026-04-06", "end": "2026-04-07"}


class TestLaterNextWeek:
    def test_later_next_week(self):
        result = parse_date_expression("later next week", REF)
        assert result == {"start": "2026-04-09", "end": "2026-04-10"}

    def test_late_next_week(self):
        result = parse_date_expression("late next week", REF)
        assert result == {"start": "2026-04-09", "end": "2026-04-10"}

    def test_later_next_week_when_today_is_thursday(self):
        thursday = date(2026, 4, 2)
        result = parse_date_expression("later next week", thursday)
        assert result == {"start": "2026-04-09", "end": "2026-04-10"}


class TestNextMonth:
    def test_next_month(self):
        result = parse_date_expression("next month", REF)
        assert result == {"start": "2026-05-01", "end": "2026-05-31"}

    def test_early_next_month(self):
        result = parse_date_expression("early next month", REF)
        assert result == {"start": "2026-05-01", "end": "2026-05-10"}

    def test_late_next_month(self):
        result = parse_date_expression("late next month", REF)
        assert result == {"start": "2026-05-20", "end": "2026-05-31"}

    def test_december_to_january(self):
        dec = date(2026, 12, 15)
        result = parse_date_expression("next month", dec)
        assert result == {"start": "2027-01-01", "end": "2027-01-31"}

    def test_early_next_month_dec_to_jan(self):
        dec = date(2026, 12, 15)
        result = parse_date_expression("early next month", dec)
        assert result == {"start": "2027-01-01", "end": "2027-01-10"}

    def test_late_next_month_dec_to_jan(self):
        dec = date(2026, 12, 15)
        result = parse_date_expression("late next month", dec)
        assert result == {"start": "2027-01-20", "end": "2027-01-31"}


class TestThisMonth:
    def test_this_month(self):
        result = parse_date_expression("this month", REF)
        assert result is not None
        assert result["start"] == "2026-04-01"
        assert result["end"] == "2026-04-30"

    def test_this_month_sunday_ref(self):
        sunday = date(2026, 4, 5)
        result = parse_date_expression("this month", sunday)
        assert result is not None
        assert result["start"] == "2026-04-06"  # Sunday skipped
        assert result["end"] == "2026-04-30"

    def test_this_month_last_day(self):
        last_day = date(2026, 4, 30)
        result = parse_date_expression("this month", last_day)
        assert result is not None
        assert result["start"] == "2026-04-30"
        assert result["end"] == "2026-04-30"


# ---------------------------------------------------------------------------
# Named weekdays
# ---------------------------------------------------------------------------

class TestNamedWeekdays:
    def test_next_monday(self):
        result = parse_date_expression("next Monday", REF)  # REF is Wed
        assert result == {"start": "2026-04-06", "end": "2026-04-06"}

    def test_next_tuesday(self):
        result = parse_date_expression("next Tuesday", REF)
        assert result == {"start": "2026-04-07", "end": "2026-04-07"}

    def test_next_friday(self):
        result = parse_date_expression("next Friday", REF)
        assert result == {"start": "2026-04-03", "end": "2026-04-03"}

    def test_next_saturday(self):
        result = parse_date_expression("next Saturday", REF)
        assert result == {"start": "2026-04-04", "end": "2026-04-04"}

    def test_next_wednesday_from_wednesday(self):
        """'next Wednesday' on a Wednesday should return NEXT week's Wednesday."""
        result = parse_date_expression("next Wednesday", REF)
        assert result == {"start": "2026-04-08", "end": "2026-04-08"}

    def test_next_tuesday_in_sentence(self):
        result = parse_date_expression("Can I come in next Tuesday?", REF)
        assert result == {"start": "2026-04-07", "end": "2026-04-07"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_sunday_reference_asap(self):
        sunday = date(2026, 4, 5)
        result = parse_date_expression("ASAP", sunday)
        assert result["start"] == "2026-04-06"
        assert result["end"] == "2026-04-08"

    def test_february_next_month(self):
        jan = date(2026, 1, 15)
        result = parse_date_expression("late next month", jan)
        assert result == {"start": "2026-02-20", "end": "2026-02-28"}

    def test_leap_year_february(self):
        jan = date(2028, 1, 15)
        result = parse_date_expression("late next month", jan)
        assert result == {"start": "2028-02-20", "end": "2028-02-29"}

    def test_unrecognised_expression(self):
        assert parse_date_expression("the Tuesday after Easter", REF) is None

    def test_partial_match_in_sentence(self):
        result = parse_date_expression("Can I come in tomorrow afternoon?", REF)
        assert result is not None
        assert result["start"] == "2026-04-02"

    def test_none_reference_uses_today(self):
        result = parse_date_expression("tomorrow")
        assert result is not None
        assert "start" in result

    def test_next_week_from_saturday(self):
        saturday = date(2026, 4, 4)
        result = parse_date_expression("next week", saturday)
        assert result == {"start": "2026-04-06", "end": "2026-04-10"}

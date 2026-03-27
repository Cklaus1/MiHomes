"""Tests for the recurrence engine — critical test file."""

from datetime import date

import pytest

from mihomes.services.recurrence import add_months, calculate_next_due


class TestAddMonths:
    def test_basic(self):
        assert add_months(date(2026, 1, 15), 1) == date(2026, 2, 15)

    def test_end_of_month_clamping(self):
        # Jan 31 + 1 month = Feb 28 (non-leap year)
        assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)

    def test_leap_year(self):
        # Jan 31 + 1 month in leap year = Feb 29
        assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)

    def test_year_rollover(self):
        assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)

    def test_twelve_months(self):
        assert add_months(date(2026, 3, 15), 12) == date(2027, 3, 15)

    def test_feb29_to_next_year(self):
        # Feb 29 + 12 months = Feb 28 (2029 is not a leap year)
        assert add_months(date(2028, 2, 29), 12) == date(2029, 2, 28)


class TestCalculateNextDue:
    def test_once_returns_none(self):
        assert calculate_next_due("once", date(2026, 4, 1)) is None

    def test_weekly(self):
        assert calculate_next_due("weekly", date(2026, 4, 1)) == date(2026, 4, 8)

    def test_biweekly(self):
        assert calculate_next_due("biweekly", date(2026, 4, 1)) == date(2026, 4, 15)

    def test_monthly(self):
        assert calculate_next_due("monthly", date(2026, 4, 1)) == date(2026, 5, 1)

    def test_monthly_end_of_month(self):
        assert calculate_next_due("monthly", date(2026, 1, 31)) == date(2026, 2, 28)

    def test_quarterly(self):
        assert calculate_next_due("quarterly", date(2026, 1, 1)) == date(2026, 4, 1)

    def test_quarterly_year_rollover(self):
        assert calculate_next_due("quarterly", date(2026, 11, 1)) == date(2027, 2, 1)

    def test_annual(self):
        assert calculate_next_due("annual", date(2026, 6, 15)) == date(2027, 6, 15)

    def test_annual_leap_day(self):
        assert calculate_next_due("annual", date(2028, 2, 29)) == date(2029, 2, 28)

    def test_unknown_frequency_returns_none(self):
        assert calculate_next_due("unknown", date(2026, 4, 1)) is None


class TestSeasonalRecurrence:
    def test_spring_northeast(self):
        # Spring starts Mar 1 in northeast
        result = calculate_next_due("seasonal", date(2026, 1, 15), "northeast", "spring")
        assert result == date(2026, 3, 1)

    def test_fall_northeast(self):
        result = calculate_next_due("seasonal", date(2026, 6, 1), "northeast", "fall")
        assert result == date(2026, 9, 1)

    def test_spring_fall_next_is_fall(self):
        # After spring (Mar 1), next should be fall (Sep 1)
        result = calculate_next_due("seasonal", date(2026, 3, 15), "northeast", "spring,fall")
        assert result == date(2026, 9, 1)

    def test_year_wraparound(self):
        # After fall (Sep 1), next spring is next year
        result = calculate_next_due("seasonal", date(2026, 9, 15), "northeast", "spring")
        assert result == date(2027, 3, 1)

    def test_mountain_climate_zone(self):
        # Spring starts Apr 1 in mountain
        result = calculate_next_due("seasonal", date(2026, 1, 15), "mountain", "spring")
        assert result == date(2026, 4, 1)

    def test_default_climate_zone(self):
        # No climate zone uses default
        result = calculate_next_due("seasonal", date(2026, 1, 15), None, "spring")
        assert result == date(2026, 3, 1)

    def test_tropical_dry_season(self):
        result = calculate_next_due("seasonal", date(2026, 6, 1), "tropical", "dry")
        assert result == date(2026, 11, 1)

    def test_no_season_spec_returns_none(self):
        assert calculate_next_due("seasonal", date(2026, 1, 1), "northeast", None) is None

    def test_invalid_season_name_returns_none(self):
        assert calculate_next_due("seasonal", date(2026, 1, 1), "northeast", "monsoon") is None

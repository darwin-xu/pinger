"""Tests for formatting.fmt_duration."""
import pytest
from formatting import fmt_duration, _fmt_num


# ── _fmt_num ──────────────────────────────────────────────────────────────────

class TestFmtNum:
    def test_integer(self):
        assert _fmt_num(1.0) == "1"

    def test_one_decimal(self):
        assert _fmt_num(31.2) == "31.2"

    def test_two_decimals(self):
        assert _fmt_num(1.66) == "1.66"

    def test_strips_trailing_zeros(self):
        assert _fmt_num(1.50) == "1.5"
        assert _fmt_num(1.00) == "1"

    def test_thousands_separator(self):
        assert _fmt_num(1234.0) == "1,234"
        assert _fmt_num(1234.56) == "1,234.56"

    def test_max_two_decimals(self):
        # Values with more precision are rounded
        assert _fmt_num(1.999) == "2"
        assert _fmt_num(1.005) == "1"   # floating point: round(1.005,2) may be 1.0


# ── fmt_duration — sub-second ─────────────────────────────────────────────────

class TestSubSecond:
    def test_nanoseconds(self):
        # 0.000001 ms = 1 ns
        assert fmt_duration(0.000001) == "1 ns"

    def test_nanoseconds_fractional(self):
        # 0.0000001 ms = 0.1 ns
        assert fmt_duration(0.0000001) == "0.1 ns"

    def test_nanoseconds_large(self):
        # 0.0009 ms = 900 ns
        assert fmt_duration(0.0009) == "900 ns"

    def test_microseconds(self):
        # 0.00123 ms = 1.23 μs
        assert fmt_duration(0.00123) == "1.23 μs"

    def test_microseconds_boundary(self):
        # exactly 1 ms should be "1 ms" not μs
        assert fmt_duration(1.0) == "1 ms"

    def test_milliseconds_example(self):
        assert fmt_duration(31.2) == "31.2 ms"

    def test_milliseconds_integer(self):
        assert fmt_duration(100.0) == "100 ms"

    def test_milliseconds_two_decimals(self):
        assert fmt_duration(12.34) == "12.34 ms"

    def test_milliseconds_strips_trailing_zero(self):
        assert fmt_duration(50.10) == "50.1 ms"

    def test_seconds_example(self):
        # 1660 ms = 1.66 s
        assert fmt_duration(1660) == "1.66 s"

    def test_seconds_integer(self):
        assert fmt_duration(2000) == "2 s"

    def test_seconds_boundary(self):
        # exactly 60,000 ms = 60 s → should switch to hh:mm:ss "00:01:00"
        assert fmt_duration(60_000) == "00:01:00"

    def test_just_below_seconds_boundary(self):
        # 59,999 ms = 59.999 s → rounds to 2 decimals → "60 s"
        assert fmt_duration(59_999) == "60 s"


# ── fmt_duration — longer durations ──────────────────────────────────────────

class TestLongerDurations:
    def test_hms_example_1(self):
        # 3 min 42 s = 222 s = 222,000 ms
        assert fmt_duration(222_000) == "00:03:42"

    def test_hms_example_2(self):
        # 12:08:31
        total_ms = (12 * 3600 + 8 * 60 + 31) * 1000
        assert fmt_duration(total_ms) == "12:08:31"

    def test_hms_zero_padding(self):
        assert fmt_duration(3_661_000) == "01:01:01"

    def test_hms_boundary(self):
        # exactly 1 day = 86,400,000 ms → "1 d 00:00:00"
        assert fmt_duration(86_400_000) == "1 d 00:00:00"

    def test_days_example(self):
        # 2 d 04:12:55
        total_ms = (2 * 86400 + 4 * 3600 + 12 * 60 + 55) * 1000
        assert fmt_duration(total_ms) == "2 d 04:12:55"

    def test_days_thousands_separator(self):
        # 1000 days = 2 y 270 d (1000 > 365, so years branch applies)
        total_ms = 1000 * 86_400_000
        assert fmt_duration(total_ms) == "2 y 270 d 00:00:00"

    def test_years_example(self):
        # 3 y 12 d 05:44:11
        total_ms = (3 * 365 * 86400 + 12 * 86400 + 5 * 3600 + 44 * 60 + 11) * 1000
        assert fmt_duration(total_ms) == "3 y 12 d 05:44:11"

    def test_years_boundary(self):
        # exactly 1 year = 365 * 86,400,000 ms
        assert fmt_duration(365 * 86_400_000) == "1 y 0 d 00:00:00"


# ── fmt_duration — edge cases ─────────────────────────────────────────────────

class TestEdgeCases:
    def test_none_returns_dash(self):
        assert fmt_duration(None) == "—"

    def test_zero(self):
        assert fmt_duration(0) == "0 ns"

    def test_exactly_1ms(self):
        assert fmt_duration(1) == "1 ms"

    def test_exactly_1s(self):
        assert fmt_duration(1_000) == "1 s"

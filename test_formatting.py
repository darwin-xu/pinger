"""Tests for formatting.fmt_duration and template chart regressions."""
from pathlib import Path
import unittest

from formatting import fmt_duration, _fmt_num


# ── _fmt_num ──────────────────────────────────────────────────────────────────

class TestFmtNum(unittest.TestCase):
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

class TestSubSecond(unittest.TestCase):
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

class TestLongerDurations(unittest.TestCase):
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

class TestEdgeCases(unittest.TestCase):
    def test_none_returns_dash(self):
        assert fmt_duration(None) == "—"

    def test_zero(self):
        assert fmt_duration(0) == "0 ns"

    def test_exactly_1ms(self):
        assert fmt_duration(1) == "1 ms"

    def test_exactly_1s(self):
        assert fmt_duration(1_000) == "1 s"


# ── History chart template regressions ────────────────────────────────────────

class TestHistoryChartTemplate(unittest.TestCase):
    @staticmethod
    def _template_source():
        return (Path(__file__).parent / "templates" / "index.html").read_text()

    def test_12h_axis_has_date_format(self):
        src = self._template_source()
        assert "hour: zl.label === '12h' ? 'MMM d HH:mm' : 'HH:mm'" in src
        assert "month: 'short', day: 'numeric'" in src

    def test_non_12h_axes_do_not_install_custom_tick_callback(self):
        src = self._template_source()
        assert "function xAxisTicks(zl, color)" in src
        assert "if (callback) ticks.callback = callback;" in src
        assert "callback: xAxisTickCallback(zl)" not in src
        assert ".ticks.callback = xAxisTickCallback(zl)" not in src

    def test_zoom_update_replaces_entire_x_axis_ticks_config(self):
        src = self._template_source()
        assert "_charts[tab].options.scales.x.ticks = xAxisTicks(zl, CH.dim);" in src
        assert "ticks: xAxisTicks(zl, CH.dim)" in src

    def test_bandwidth_axis_starts_at_zero(self):
        src = self._template_source()
        assert "iperf3 interval (min)" in src
        assert 'name="iperf3_interval" type="number" min="0" step="1"' in src
        assert "const IPERF3_INTERVAL_MS = {{ (settings.iperf3_interval | default(60)) * 60 * 1000 }};" in src
        assert "const BANDWIDTH_GAP_THRESHOLD_MS = IPERF3_INTERVAL_MS > 0 ? IPERF3_INTERVAL_MS * 2 : GAP_THRESHOLD_MS;" in src
        assert "pointsWithGaps(rows, r => r.success ? r.download_mbps : null, BANDWIDTH_GAP_THRESHOLD_MS)" in src
        assert "pointsWithGaps(rows, r => r.success ? r.upload_mbps   : null, BANDWIDTH_GAP_THRESHOLD_MS)" in src
        assert "metricId: 'download'" in src and "borderWidth: 3" in src
        assert "yScales = { y: { min: 0, title: { display: true, text: 'Mbps'" in src
        assert '<input name="iperf3" type="hidden" value="off">' not in src
        assert "h.iperf3" not in src

    def test_dashboard_bandwidth_headers_preserve_mbps_case(self):
        src = self._template_source()
        assert 'th.unit-label { text-transform: none; letter-spacing: 0; }' in src
        assert '<th class="unit-label">↓ Mbps</th><th class="unit-label">↑ Mbps</th>' in src

    def test_dashboard_bandwidth_dash_can_show_direction_error(self):
        src = self._template_source()
        assert "ip.get('download_error')" in src
        assert "ip.get('upload_error')" in src
        assert "const dlErr   = (ip.success && ip.download_error) ? ip.download_error : null;" in src
        assert "const ulErr   = (ip.success && ip.upload_error) ? ip.upload_error : null;" in src
        assert 'title="{{ ip.get(\'download_error\')|e }}">—</span>' in src
        assert 'title="{{ ip.get(\'upload_error\')|e }}">—</span>' in src

    def test_dashboard_has_mobile_layout_support(self):
        src = self._template_source()
        assert '<div class="table-scroll" aria-label="Server status table">' in src
        assert "#dashboard { min-width: 980px; }" in src
        assert "#dashboard { min-width: 0; border-collapse: separate; border-spacing: 0 8px; }" in src
        assert "@media (max-width: 640px), (max-width: 900px) and (orientation: portrait)" in src
        assert ".page-header, .table-header { align-items: stretch; flex-direction: column; }" in src
        assert "#dashboard tr.data-row td.mobile-provider," in src
        assert "#dashboard tr.data-row td.host-col," in src
        assert "#dashboard tr.edit-row { display: none; }" in src
        assert "#dashboard tr.edit-row.active { display: block; }" in src
        assert "#dashboard tr.data-row td[data-label]::before" in src
        assert 'data-label="↓ Mbps"' in src
        assert 'data-label="↑ Mbps"' in src

    def test_dashboard_has_landscape_compact_table(self):
        src = self._template_source()
        assert "@media (max-width: 900px) and (orientation: landscape)" in src
        assert "#dashboard {\n            min-width: 0;\n            table-layout: fixed;\n        }" in src
        assert "#dashboard th:nth-child(4)," in src
        assert "#dashboard th:nth-child(5)," in src

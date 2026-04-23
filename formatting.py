"""Human-readable formatting utilities shared by the web UI and terminal display.

Duration formatting rules
─────────────────────────
Sub-second:
  < 1 μs     → "123 ns"
  < 1 ms     → "1.23 μs"
  < 1 s      → "12.34 ms"
  < 60 s     → "1.23 s"

Longer durations:
  < 1 day    → "hh:mm:ss"
  < 1 year   → "d d hh:mm:ss"
  >= 1 year  → "y y d d hh:mm:ss"

All numeric parts use a maximum of 2 decimal places (trailing zeros stripped)
and a thousands separator where the value is ≥ 1 000.
"""
from __future__ import annotations

_YEAR_MS = 365 * 86_400_000
_DAY_MS  =       86_400_000


def _fmt_num(v: float) -> str:
    """Format *v* with at most 2 decimal places, trailing zeros stripped, thousands separator."""
    r = round(v, 2)
    # Build the string with 2 decimal places then strip
    s = f"{r:,.2f}".rstrip("0").rstrip(".")
    return s


def fmt_duration(ms: float | None) -> str:
    """Format a millisecond value as a human-readable duration string.

    Returns '—' for None values.
    """
    if ms is None:
        return "\u2014"  # em-dash

    # ── Sub-second ────────────────────────────────────────────────────────────
    if ms < 0.001:
        return f"{_fmt_num(ms * 1_000_000)} ns"
    if ms < 1:
        return f"{_fmt_num(ms * 1_000)} \u03bcs"   # μs
    if ms < 1_000:
        return f"{_fmt_num(ms)} ms"
    if ms < 60_000:
        return f"{_fmt_num(ms / 1_000)} s"

    # ── Duration (integer arithmetic) ─────────────────────────────────────────
    total_sec = round(ms / 1_000)
    sec       = total_sec % 60
    total_min = total_sec // 60
    min_      = total_min % 60
    total_hr  = total_min // 60

    if ms < _DAY_MS:
        return f"{total_hr:02d}:{min_:02d}:{sec:02d}"

    day = total_hr // 24
    hr  = total_hr % 24

    if ms < _YEAR_MS:
        return f"{day:,} d {hr:02d}:{min_:02d}:{sec:02d}"

    yr      = day // 365
    rem_day = day % 365
    return f"{yr:,} y {rem_day:,} d {hr:02d}:{min_:02d}:{sec:02d}"

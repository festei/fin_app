"""Helpers shared by the nodes that read fiscal-year history from ``SecurityMetrics.annual``."""

from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

from fin_core.models import SecurityMetrics

# a fiscal year is 52-53 weeks or a calendar year; consecutive year ends are 350-380 days apart
YEAR_GAP_DAYS = (350, 380)
SHARES_PAIR_WINDOW = timedelta(days=120)  # the 10-K cover is dated shortly after the year end


def series_by_year(m: SecurityMetrics, concept: str) -> dict[date, float]:
    return {p.period_end: p.value for p in m.annual.get(concept, [])}


def recent_years(years: list[date], window_years: int) -> list[date]:
    """The fiscal years within about ``window_years`` calendar years of the newest one."""
    if not years:
        return []
    years = sorted(years)
    return [y for y in years if (years[-1] - y).days <= window_years * 360]


def previous_year(years, fy: date) -> date | None:
    """The fiscal year ending 350-380 days before ``fy``, if present among ``years``."""
    for y in sorted(years, reverse=True):
        if YEAR_GAP_DAYS[0] <= (fy - y).days <= YEAR_GAP_DAYS[1]:
            return y
    return None


def shares_for_year(m: SecurityMetrics, fy: date) -> float | None:
    """Cover-page share count of the annual report for ``fy`` (earliest on/after the year end)."""
    near = [
        p.value
        for p in sorted(m.annual.get("SharesOutstanding", []), key=lambda p: p.period_end)
        if fy <= p.period_end <= fy + SHARES_PAIR_WINDOW
    ]
    return near[0] if near else None


def cagr(
    points: list[tuple[date, float]], window_years: int = 10, min_points: int = 5
) -> float | None:
    """Smoothed annual growth rate of a fiscal-year series (oldest first).

    Compares the mean of the first 3 and the last 3 values within about ``window_years``
    calendar years, annualised over the distance between the two groups' middle years, so one
    odd year does not decide the result. With only 5 points the two groups share a year.
    ``None`` with fewer than ``min_points`` points or a non-positive start or end average.
    """
    pts = sorted(points)
    if not pts:
        return None
    pts = [p for p in pts if (pts[-1][0] - p[0]).days <= window_years * 360]
    if len(pts) < min_points:
        return None
    first, last = pts[:3], pts[-3:]
    start, end = mean(v for _, v in first), mean(v for _, v in last)
    years = (last[1][0] - first[1][0]).days / 365.25
    if start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1

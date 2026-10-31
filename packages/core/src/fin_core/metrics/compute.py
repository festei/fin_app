"""Compute point-in-time ``SecurityMetrics`` from stored facts and prices.

Everything is derived from data public at ``as_of``: fundamentals via ``repo.facts_as_of`` (by
filing date) and the latest price bar on or before ``as_of``. Gaps are reported in
``warnings`` and as ``None`` values, never filled with guesses.

CAVEAT: stored prices are source-adjusted, not as-traded. For the latest price (live scoring)
that is the same number; for historical ``as_of`` dates after a split or dividend it is not,
so price-based ratios are only trustworthy for recent dates until as-traded prices exist.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.orm import Session

from fin_core.db import repo
from fin_core.metrics.annual import ANNUAL_DAYS, Series, annual_series
from fin_core.models import AnnualPoint, SecurityMetrics

STALE_FUNDAMENTALS = timedelta(days=548)  # ~18 months without a new annual report
STALE_PRICE = timedelta(days=7)
GROWTH_WINDOW_YEARS = 10
GROWTH_MIN_YEARS = 6  # need two non-overlapping 3-year averages


class UnknownSecurityError(LookupError):
    pass


def _last(series: Series, concept: str) -> float | None:
    points = series.get(concept)
    return points[-1][1] if points else None


def _latest_common(series: Series, a: str, b: str) -> tuple[float, float] | None:
    """Latest fiscal year for which both concepts exist (so ratios compare like with like)."""
    left, right = dict(series.get(a, [])), dict(series.get(b, []))
    common = sorted(set(left) & set(right))
    return (left[common[-1]], right[common[-1]]) if common else None


def consecutive_positive_years(points: list[tuple[date, float]]) -> int:
    """Years of uninterrupted positive values counting back from the latest fiscal year."""
    count, previous = 0, None
    for end, value in reversed(points):
        if value <= 0 or (previous is not None and (previous - end).days > ANNUAL_DAYS[1]):
            break
        count, previous = count + 1, end
    return count


def earnings_growth(eps: list[tuple[date, float]]) -> float | None:
    """Growth of the average EPS of the latest 3 fiscal years over the earliest 3 in a window of
    up to 10 years (Graham's smoothing). ``None`` with fewer than 6 years or a non-positive base."""
    window = [v for _, v in eps[-GROWTH_WINDOW_YEARS:]]
    if len(window) < GROWTH_MIN_YEARS:
        return None
    start, end = sum(window[:3]) / 3, sum(window[-3:]) / 3
    return None if start <= 0 else end / start - 1


def compute_metrics(session: Session, ticker: str, as_of: date | datetime) -> SecurityMetrics:
    sec = repo.get_security(session, ticker)
    if sec is None:
        raise UnknownSecurityError(ticker)

    if isinstance(as_of, datetime):
        as_of_dt = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    else:
        as_of_dt = datetime.combine(as_of, time(23, 59, 59), tzinfo=UTC)
    as_of_date = as_of_dt.date()
    warnings: list[str] = []

    series = annual_series(repo.facts_as_of(session, sec.id, as_of_date))
    bar = repo.latest_price(session, sec.id, as_of_dt)

    price = price_date = None
    if bar is None:
        warnings.append("no price on or before as_of")
    else:
        price, price_date = bar.close, bar.ts.date()
        if as_of_date - price_date > STALE_PRICE:
            warnings.append(f"price is stale: last bar {price_date}")

    # newest fiscal year end across every annual series (the cover-page share count is a
    # snapshot of a later date, not a fiscal year)
    latest_fy = max(
        (pts[-1][0] for concept, pts in series.items() if concept != "SharesOutstanding"),
        default=None,
    )
    if latest_fy is None:
        warnings.append("no annual fundamentals available as of this date")
    elif as_of_date - latest_fy > STALE_FUNDAMENTALS:
        warnings.append(f"fundamentals are stale: latest fiscal year ended {latest_fy}")

    eps = _last(series, "EpsDiluted")
    equity = _last(series, "StockholdersEquity")
    shares = _last(series, "SharesOutstanding")
    net_income = _last(series, "NetIncome")
    long_term_debt = _last(series, "LongTermDebt")

    pe = price / eps if price is not None and eps else None
    bvps = equity / shares if equity is not None and shares else None
    pb = price / bvps if price is not None and bvps else None

    current = _latest_common(series, "AssetsCurrent", "LiabilitiesCurrent")
    current_ratio = current[0] / current[1] if current and current[1] > 0 else None

    # equity <= 0 is kept visible as a negative value (the Graham node fails it) rather than
    # hidden; exactly zero equity has no meaningful ratio.
    debt_to_equity = long_term_debt / equity if long_term_debt is not None and equity else None
    roe = net_income / equity if net_income is not None and equity and equity > 0 else None

    dps = series.get("DividendsPerShare")
    return SecurityMetrics(
        ticker=sec.ticker,
        as_of=as_of_dt,
        price=price,
        price_date=price_date,
        pe=pe,
        pb=pb,
        current_ratio=current_ratio,
        debt_to_equity=debt_to_equity,
        roe=roe,
        earnings_growth=earnings_growth(series.get("EpsDiluted", [])),
        dividend_years=consecutive_positive_years(dps) if dps else None,
        eps=eps,
        book_value_per_share=bvps,
        latest_fiscal_year_end=latest_fy,
        annual={
            c: [AnnualPoint(period_end=e, value=v) for e, v in pts] for c, pts in series.items()
        },
        warnings=warnings,
    )

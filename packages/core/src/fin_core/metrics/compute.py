"""Compute point-in-time ``SecurityMetrics`` from stored facts and prices.

Everything is derived from data public at ``as_of``: fundamentals via ``repo.facts_as_of`` (by
filing date) and the latest price bar on or before ``as_of``. Gaps are reported in
``warnings`` and as ``None`` values, never silently filled.

Rules that keep ratios honest:
* each ratio combines numerator and denominator from the *same* fiscal year (a concept that
  stopped being reported never leaks an old value into a newer ratio); when a ratio has to
  fall back to an older year, a warning says so;
* a missing debt tag is treated as zero debt only when the balance sheet for that year exists,
  and a warning says so; a company with no dividend data is treated as having paid none, with a
  warning;
* the per-share book value pairs the balance sheet with the share count on the cover of the
  same annual report;
* ``warnings`` that make the data too poor to act on are also listed in ``signal_blockers``.

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
GROWTH_WINDOW_DAYS = 3600  # ~10 fiscal years back (9 years = 3287 days, 10 = 3652)
GROWTH_MIN_YEARS = 6  # need two non-overlapping 3-year averages
LAG_TOLERANCE = timedelta(days=30)  # fiscal year-ends drift a little (52/53-week years)
SHARES_PAIR_WINDOW = timedelta(days=120)  # the 10-K cover is dated shortly after the year end

# Issuers with more than one share class. EDGAR's company facts carry no class dimension, so
# per-share and share-count figures can describe a single class only. Conservative list of
# tickers known to have multiple listed classes; extend as the universe grows.
MULTI_CLASS_TICKERS = frozenset(
    {"BRK-A", "BRK-B", "GOOG", "GOOGL", "FOX", "FOXA", "NWS", "NWSA", "BF-A", "BF-B", "LEN",
     "LEN-B", "UA", "UAA", "HEI", "HEI-A"}
)  # fmt: skip


class UnknownSecurityError(LookupError):
    pass


def _latest_common(series: Series, a: str, b: str) -> tuple[date, float, float] | None:
    """Latest fiscal year for which both concepts exist: ``(year_end, value_a, value_b)``."""
    left, right = dict(series.get(a, [])), dict(series.get(b, []))
    common = sorted(set(left) & set(right))
    return (common[-1], left[common[-1]], right[common[-1]]) if common else None


def consecutive_positive_years(points: list[tuple[date, float]]) -> int:
    """Years of uninterrupted positive values counting back from the latest fiscal year."""
    count, previous = 0, None
    for end, value in reversed(points):
        if value <= 0 or (previous is not None and (previous - end).days > ANNUAL_DAYS[1]):
            break
        count, previous = count + 1, end
    return count


def earnings_growth(eps: list[tuple[date, float]]) -> float | None:
    """Growth of the average EPS of the latest 3 fiscal years over the earliest 3 within about
    the last 10 *calendar* years (Graham's smoothing), so gaps in the series do not stretch the
    window. ``None`` with fewer than 6 years in the window or a non-positive base."""
    if not eps:
        return None
    latest = eps[-1][0]
    window = [v for end, v in eps if (latest - end).days <= GROWTH_WINDOW_DAYS]
    if len(window) < GROWTH_MIN_YEARS:
        return None
    start, end_avg = sum(window[:3]) / 3, sum(window[-3:]) / 3
    return None if start <= 0 else end_avg / start - 1


def average_eps_3y(eps: list[tuple[date, float]], latest_fy: date | None) -> float | None:
    """Mean of the 3 consecutive fiscal years ending at ``latest_fy``; ``None`` otherwise."""
    last3 = eps[-3:]
    if latest_fy is None or len(last3) < 3 or last3[-1][0] != latest_fy:
        return None
    if any((b[0] - a[0]).days > ANNUAL_DAYS[1] for a, b in zip(last3, last3[1:], strict=False)):
        return None
    return sum(v for _, v in last3) / 3


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
    blockers: list[str] = []

    def block(message: str) -> None:
        warnings.append(message)
        blockers.append(message)

    series = annual_series(repo.facts_as_of(session, sec.id, as_of_date))
    bar = repo.latest_price(session, sec.id, as_of_dt)

    price = price_date = None
    if bar is None:
        block("no price on or before as_of")
    else:
        price, price_date = bar.close, bar.ts.date()
        if as_of_date - price_date > STALE_PRICE:
            block(f"price is stale: last bar {price_date}")

    # newest fiscal year end across every annual series (the cover-page share count is a
    # snapshot of a later date, not a fiscal year)
    latest_fy = max(
        (pts[-1][0] for concept, pts in series.items() if concept != "SharesOutstanding"),
        default=None,
    )
    if latest_fy is None:
        block("no annual fundamentals available as of this date")
    elif as_of_date - latest_fy > STALE_FUNDAMENTALS:
        block(f"fundamentals are stale: latest fiscal year ended {latest_fy}")

    def note_lag(name: str, fy: date) -> None:
        if latest_fy is not None and latest_fy - fy > LAG_TOLERANCE:
            warnings.append(f"{name} uses fiscal year {fy}, older than the latest {latest_fy}")

    # --- earnings-based -------------------------------------------------------------------
    eps_points = series.get("EpsDiluted", [])
    eps = dict(eps_points).get(latest_fy) if latest_fy else None
    if eps_points and eps is None:
        warnings.append(f"no diluted EPS for the latest fiscal year {latest_fy}")
    pe = price / eps if price is not None and eps else None
    eps_avg3 = average_eps_3y(eps_points, latest_fy)
    pe_avg3 = price / eps_avg3 if price is not None and eps_avg3 else None

    # --- book value (balance sheet + the cover share count of the same annual report) ------
    equity_points = series.get("StockholdersEquity", [])
    equity_fy, equity = equity_points[-1] if equity_points else (None, None)
    if equity_fy is not None:
        note_lag("equity", equity_fy)

    shares = None
    snapshots = series.get("SharesOutstanding", [])
    if equity_fy is not None and snapshots:
        near = [v for end, v in snapshots if equity_fy <= end <= equity_fy + SHARES_PAIR_WINDOW]
        if near:
            shares = near[0]  # earliest cover on/after the year end = the 10-K's own cover
        else:
            shares_date, shares = snapshots[-1]
            warnings.append(
                f"share count dated {shares_date} does not match the balance sheet of {equity_fy}"
            )
    bvps = equity / shares if equity is not None and shares else None
    pb = price / bvps if price is not None and bvps else None
    if sec.ticker in MULTI_CLASS_TICKERS:
        warnings.append(
            "multiple share classes: share counts and per-share values may cover one class "
            "only, so book value per share and P/B are not reported"
        )
        bvps = pb = None

    # --- balance-sheet ratios -------------------------------------------------------------
    current = _latest_common(series, "AssetsCurrent", "LiabilitiesCurrent")
    current_ratio = None
    if current:
        note_lag("current ratio", current[0])
        current_ratio = current[1] / current[2] if current[2] > 0 else None

    debt_to_equity = None
    if equity_fy is not None and equity:
        debt = dict(series.get("LongTermDebt", [])).get(equity_fy)
        if debt is None:
            balance_sheet_exists = any(
                equity_fy in dict(series.get(c, []))
                for c in ("Assets", "Liabilities", "AssetsCurrent")
            )
            if balance_sheet_exists:
                debt = 0.0
                warnings.append(
                    f"no long-term debt reported for fiscal year {equity_fy}: treated as 0 "
                    "(it may be filed under a tag we do not map)"
                )
        # equity < 0 stays visible as a negative ratio (the Graham node fails it)
        debt_to_equity = debt / equity if debt is not None else None

    roe = None
    income = _latest_common(series, "NetIncome", "StockholdersEquity")
    if income:
        note_lag("return on equity", income[0])
        roe = income[1] / income[2] if income[2] > 0 else None

    # --- dividends ------------------------------------------------------------------------
    dps = series.get("DividendsPerShare")
    if latest_fy is None:
        dividend_years = None
    elif not dps:
        dividend_years = 0
        warnings.append("no dividend data reported: treated as 0 years (never paid, or not filed)")
    elif latest_fy - dps[-1][0] > timedelta(days=ANNUAL_DAYS[1]):
        dividend_years = 0
        warnings.append(
            f"last dividend data is for fiscal year {dps[-1][0]}: streak treated as ended"
        )
    else:
        dividend_years = consecutive_positive_years(dps)

    return SecurityMetrics(
        ticker=sec.ticker,
        as_of=as_of_dt,
        price=price,
        price_date=price_date,
        pe=pe,
        pe_avg3=pe_avg3,
        pb=pb,
        current_ratio=current_ratio,
        debt_to_equity=debt_to_equity,
        roe=roe,
        earnings_growth=earnings_growth(eps_points),
        dividend_years=dividend_years,
        eps=eps,
        book_value_per_share=bvps,
        latest_fiscal_year_end=latest_fy,
        sector=sec.sector,
        multi_class=sec.ticker in MULTI_CLASS_TICKERS,
        annual={
            c: [AnnualPoint(period_end=e, value=v) for e, v in pts] for c, pts in series.items()
        },
        warnings=warnings,
        signal_blockers=blockers,
    )

"""Turn point-in-time fundamental facts into fiscal-year series.

Input is what ``repo.facts_as_of`` returns (so nothing filed after ``as_of`` is visible).
Rules, chosen so the series is comparable year to year:

* only annual reports count (10-K family); quarterly figures and YTD sums are ignored;
* flow concepts (revenue, income, cash flow) need a duration of about one year;
* stock concepts (assets, equity, ...) are balance-sheet instants;
* a concept may be reported under several XBRL tags over the years; for each fiscal year the
  highest-priority tag (order in ``CONCEPT_TAGS``) wins, which keeps the series continuous
  when a company switches tags;
* cover-page share counts (``SnapshotConcepts``) are taken from any periodic report.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from fin_core.db.models import FundamentalFact
from fin_core.providers.edgar_concepts import CONCEPT_TAGS

STOCK_CONCEPTS = frozenset(
    {
        "Assets",
        "AssetsCurrent",
        "Liabilities",
        "LiabilitiesCurrent",
        "StockholdersEquity",
        "LongTermDebt",
        "Cash",
        "RetainedEarnings",
        "InventoryNet",
        "PropertyPlantAndEquipmentNet",
        "LongTermDebtCurrent",
    }
)
FLOW_CONCEPTS = frozenset(
    {
        "Revenue",
        "GrossProfit",
        "OperatingIncome",
        "NetIncome",
        "EpsDiluted",
        "DividendsPerShare",
        "InterestExpense",
        "NetCashFromOperating",
        "Capex",
        "DepreciationAmortization",
        "ResearchAndDevelopmentExpense",
    }
)
SNAPSHOT_CONCEPTS = frozenset({"SharesOutstanding"})

UNIT = {
    "EpsDiluted": "USD/shares",
    "DividendsPerShare": "USD/shares",
    "SharesOutstanding": "shares",
}
ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT"})
ANNUAL_DAYS = (350, 380)

Series = dict[str, list[tuple[date, float]]]


def _is_annual_flow(f: FundamentalFact) -> bool:
    days = (f.period_end - f.period_start).days
    return ANNUAL_DAYS[0] <= days <= ANNUAL_DAYS[1]


def _keep_one_snapshot_tag(best: dict[tuple[str, date], tuple[tuple[int, int], float]]) -> None:
    """Share counts come from one source per company: the highest-priority tag that has any
    data (cover page before balance sheet). Mixing them, e.g. a cover count one year and a
    balance-sheet count the next, would make year-over-year share changes meaningless."""
    for concept in SNAPSHOT_CONCEPTS:
        ranks = {key: rank[0] for key, (rank, _) in best.items() if key[0] == concept}
        if ranks:
            keep = min(ranks.values())
            for key, tag_rank in ranks.items():
                if tag_rank != keep:
                    del best[key]


def _derive_total_debt(best: dict[tuple[str, date], tuple[tuple[int, int], float]]) -> None:
    """Add a ``TotalDebt`` series: one definition of debt for every consumer.

    The ``LongTermDebt`` concept has two tags: ``LongTermDebtNoncurrent`` (rank 0, excludes the
    portion due within a year) and ``LongTermDebt`` (rank 1, already includes it). Adding the
    current portion to the second would count it twice, so it is added only to the first. A year
    with only current debt reported uses that.
    """
    ends = {end for (concept, end) in best if concept in ("LongTermDebt", "LongTermDebtCurrent")}
    for end in ends:
        long_term = best.get(("LongTermDebt", end))
        current = best.get(("LongTermDebtCurrent", end))
        current_value = current[1] if current else None
        if long_term is None:
            total = current_value
        elif long_term[0][0] == 0:  # noncurrent tag: add what is due within a year
            total = long_term[1] + (current_value or 0.0)
        else:  # total tag: already includes the current portion
            total = long_term[1]
        if total is not None:
            best[("TotalDebt", end)] = ((0, 0), total)


def annual_series(facts: Iterable[FundamentalFact]) -> Series:
    """``{concept: [(fiscal_year_end, value), ...]}``, oldest first."""
    best: dict[tuple[str, date], tuple[tuple[int, int], float]] = {}
    for f in facts:
        concept = f.concept
        tags = CONCEPT_TAGS.get(concept)
        if not tags or f.tag not in tags or f.unit != UNIT.get(concept, "USD"):
            continue
        if concept in SNAPSHOT_CONCEPTS:
            pass  # cover-page count from any periodic report
        elif f.form not in ANNUAL_FORMS:
            continue
        elif concept in STOCK_CONCEPTS:
            if not f.instant:
                continue
        elif concept in FLOW_CONCEPTS:
            if f.instant or not _is_annual_flow(f):
                continue
        else:  # mapped but not classified: refuse to guess
            continue
        # lower rank wins: better tag priority first, then the most recently filed version
        rank = (tags.index(f.tag), -f.filed.toordinal())
        key = (concept, f.period_end)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, float(f.value))
    _keep_one_snapshot_tag(best)
    _derive_total_debt(best)
    out: Series = {}
    for (concept, end), (_, value) in best.items():
        out.setdefault(concept, []).append((end, value))
    for points in out.values():
        points.sort()
    return out

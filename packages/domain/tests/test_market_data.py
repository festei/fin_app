"""Market Data rules: point-in-time facts, fiscal-year series, restatements, candles, universe."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from fin_domain.market_data import (
    CONCEPT_TAGS,
    FLOW_CONCEPTS,
    SNAPSHOT_CONCEPTS,
    STOCK_CONCEPTS,
    Fact,
    PriceBar,
    Security,
    SecurityKind,
    SecurityUpdate,
    aggregate_candles,
    annual_series,
    bond_series,
    is_restated,
    known_as_of,
    latest_versions,
    security_updates,
)
from fin_domain.shared import InvariantViolation, Ticker

FY23, FY24, COVER24 = date(2023, 12, 31), date(2024, 12, 31), date(2025, 2, 10)


def fact(concept, value, end, *, tag=None, unit="USD", instant=False, form="10-K", start=None,
         filed=None, sequence=0):  # fmt: skip
    return Fact(
        concept=concept,
        tag=tag or CONCEPT_TAGS[concept][0],
        unit=unit,
        period_start=end if instant else (start or date(end.year, 1, 1)),
        period_end=end,
        instant=instant,
        fiscal_year=end.year,
        fiscal_period="FY",
        form=form,
        filed=filed or date(end.year + 1, 2, 15),
        accession=f"acc-{concept}-{end}-{filed}-{sequence}",
        value=Decimal(str(value)),
        sequence=sequence,
    )


# --- Fact ---------------------------------------------------------------------------------------


def test_an_instant_fact_has_a_zero_length_period():
    with pytest.raises(InvariantViolation):
        replace(fact("Assets", 1, FY24, instant=True), period_start=date(2024, 1, 1))


def test_a_period_cannot_end_before_it_starts():
    with pytest.raises(InvariantViolation):
        fact("Revenue", 1, FY24, start=date(2025, 1, 1))


@pytest.mark.parametrize("bad", ["NaN", "Infinity"])
def test_a_fact_value_must_be_finite(bad):
    with pytest.raises(InvariantViolation):
        replace(fact("Revenue", 1, FY24), value=Decimal(bad))


# --- point in time ------------------------------------------------------------------------------


def test_only_the_latest_filed_version_of_a_figure_survives():
    original = fact("Revenue", 100, FY23, filed=date(2024, 2, 1))
    restated = fact("Revenue", 90, FY23, filed=date(2024, 8, 1), sequence=1)
    assert latest_versions([original, restated]) == [restated]
    assert latest_versions([restated, original]) == [restated]


def test_a_tie_on_the_filing_day_goes_to_the_one_stored_last():
    first = fact("Revenue", 100, FY23, filed=date(2024, 2, 1), sequence=1)
    second = fact("Revenue", 101, FY23, filed=date(2024, 2, 1), sequence=2)
    assert latest_versions([first, second]) == [second]


def test_a_restatement_is_invisible_until_it_is_filed():
    original = fact("Revenue", 100, FY23, filed=date(2024, 2, 1))
    restated = fact("Revenue", 90, FY23, filed=date(2024, 8, 1), sequence=1)
    assert known_as_of([original, restated], date(2024, 7, 31)) == [original]
    assert known_as_of([original, restated], date(2024, 8, 1)) == [restated]
    assert known_as_of([original, restated], date(2024, 1, 31)) == []


def test_different_periods_and_tags_are_different_figures():
    a = fact("Revenue", 100, FY23)
    b = fact("Revenue", 110, FY24)
    c = fact("Revenue", 95, FY23, tag=CONCEPT_TAGS["Revenue"][1])
    assert len(latest_versions([a, b, c])) == 3


# --- fiscal-year series (moved from the metrics tests) --------------------------------------------


def test_tag_priority_keeps_the_series_continuous_across_tag_changes():
    old, new = CONCEPT_TAGS["Revenue"][2], CONCEPT_TAGS["Revenue"][0]
    rows = [
        fact("Revenue", 100, date(2020, 12, 31), tag=old),  # only the old tag exists
        fact("Revenue", 110, date(2021, 12, 31), tag=old),
        fact("Revenue", 120, date(2021, 12, 31), tag=new),  # both exist: the preferred tag wins
        fact("Revenue", 130, date(2022, 12, 31), tag=new),
    ]
    assert annual_series(rows)["Revenue"] == [
        (date(2020, 12, 31), 100.0),
        (date(2021, 12, 31), 120.0),
        (date(2022, 12, 31), 130.0),
    ]


def test_quarterly_forms_and_partial_year_flows_are_ignored():
    quarter = fact("Revenue", 25, date(2024, 3, 31), form="10-Q", start=date(2024, 1, 1))
    half_year = fact("Revenue", 50, FY24, start=date(2024, 7, 1))
    assert annual_series([quarter, half_year]) == {}


def test_every_mapped_concept_is_classified_so_annual_series_cannot_drop_one_silently():
    classified = STOCK_CONCEPTS | FLOW_CONCEPTS | SNAPSHOT_CONCEPTS
    assert set(CONCEPT_TAGS) == classified  # none unclassified, none classified but unmapped
    assert not (STOCK_CONCEPTS & FLOW_CONCEPTS)


DEBT_NONCURRENT = CONCEPT_TAGS["LongTermDebt"][0]  # excludes the portion due within a year
DEBT_TOTAL = CONCEPT_TAGS["LongTermDebt"][1]  # already includes it
SHARES_COVER, SHARES_BALANCE = CONCEPT_TAGS["SharesOutstanding"][:2]


def derived(concept, *rows):
    return annual_series(rows).get(concept)


def debt_fact(value, end, tag, concept="LongTermDebt"):
    return fact(concept, value, end, tag=tag, instant=True)


def test_total_debt_adds_the_current_portion_to_the_noncurrent_tag():
    rows = (
        debt_fact(400, FY24, DEBT_NONCURRENT),
        debt_fact(100, FY24, None, "LongTermDebtCurrent"),
    )
    assert derived("TotalDebt", *rows) == [(FY24, 500.0)]


def test_total_debt_does_not_double_count_when_the_total_tag_already_includes_it():
    rows = (debt_fact(500, FY24, DEBT_TOTAL), debt_fact(100, FY24, None, "LongTermDebtCurrent"))
    assert derived("TotalDebt", *rows) == [(FY24, 500.0)]  # not 600


def test_total_debt_per_year_follows_the_tag_in_use_that_year():
    rows = (
        debt_fact(450, FY23, DEBT_TOTAL),
        debt_fact(50, FY23, None, "LongTermDebtCurrent"),
        debt_fact(400, FY24, DEBT_NONCURRENT),
        debt_fact(100, FY24, None, "LongTermDebtCurrent"),
    )
    assert derived("TotalDebt", *rows) == [(FY23, 450.0), (FY24, 500.0)]


def test_total_debt_edge_cases():
    assert derived("TotalDebt", debt_fact(400, FY24, DEBT_NONCURRENT)) == [(FY24, 400.0)]
    assert derived("TotalDebt", debt_fact(100, FY24, None, "LongTermDebtCurrent")) == [
        (FY24, 100.0)
    ]
    assert derived("TotalDebt") is None


def test_share_counts_come_from_one_source_not_a_mix_of_cover_and_balance_sheet():
    rows = (
        fact("SharesOutstanding", 95, FY23, tag=SHARES_BALANCE, unit="shares", instant=True),
        fact("SharesOutstanding", 98, FY24, tag=SHARES_BALANCE, unit="shares", instant=True),
        fact("SharesOutstanding", 100, COVER24, tag=SHARES_COVER, unit="shares", instant=True),
    )
    # the cover page has data (for one year only): balance-sheet counts are dropped, so a
    # year-over-year change is never computed across two different kinds of count
    assert derived("SharesOutstanding", *rows) == [(COVER24, 100.0)]
    assert derived("SharesOutstanding", *rows[:2]) == [(FY23, 95.0), (FY24, 98.0)]


# --- restatement --------------------------------------------------------------------------------

D1 = datetime(2026, 1, 5, tzinfo=UTC)


def bar(day_offset, close, **kw):
    return PriceBar(ts=D1 + timedelta(days=day_offset), close=close, **kw)


def test_a_changed_close_on_a_stored_day_is_a_restatement():
    stored = {D1: 100.0, D1 + timedelta(days=1): 101.0}
    assert is_restated(stored, [bar(0, 50.0), bar(1, 50.5)])  # a 2:1 split rewrote history


def test_unchanged_closes_are_not_a_restatement():
    assert not is_restated({D1: 100.0}, [bar(0, 100.0)])
    assert not is_restated({D1: 100.0}, [bar(0, 100.0 + 1e-9)])  # rounding noise


def test_a_bar_we_do_not_hold_yet_is_new_not_restated():
    assert not is_restated({D1: 100.0}, [bar(5, 42.0)])
    assert not is_restated({}, [bar(0, 1.0)])


def test_naive_bar_timestamps_are_rejected():
    with pytest.raises(InvariantViolation):
        PriceBar(ts=datetime(2026, 1, 5), close=1.0)  # noqa: DTZ001


# --- candles ------------------------------------------------------------------------------------


def make_bars(n):
    return [
        bar(i, close=float(i), open=float(i) - 0.5, high=float(i) + 1, low=float(i) - 1, volume=10)
        for i in range(n)
    ]


def test_few_bars_pass_through_unchanged():
    bars = make_bars(5)
    assert aggregate_candles(bars, 10) == bars


def test_many_bars_merge_into_candles_that_keep_the_true_extremes():
    bars = make_bars(10)
    candles = aggregate_candles(bars, 5)  # step 2
    assert len(candles) == 5
    first = candles[0]
    assert (first.open, first.close) == (bars[0].open, bars[1].close)
    assert first.high == max(bars[0].high, bars[1].high)
    assert first.low == min(bars[0].low, bars[1].low)
    assert first.volume == 20
    assert candles[-1].ts == bars[-1].ts  # the latest candle ends on the latest bar


def test_buckets_are_counted_from_the_newest_bar():
    candles = aggregate_candles(make_bars(7), 3)  # step 3: [bar0], [bar1-3], [bar4-6]
    assert [c.close for c in candles] == [0.0, 3.0, 6.0]
    assert len(candles) <= 3


def test_missing_highs_lows_and_volumes_stay_missing():
    bars = [bar(i, close=1.0) for i in range(4)]
    candle = aggregate_candles(bars, 2)[0]
    assert (candle.high, candle.low, candle.volume) == (None, None, None)


def test_candles_need_a_positive_limit():
    with pytest.raises(InvariantViolation):
        aggregate_candles(make_bars(3), 0)


# --- security -----------------------------------------------------------------------------------


def test_an_update_only_touches_the_fields_it_provides():
    held = Security(Ticker("KO"), name="Coca-Cola", cik="0000021344", sector="Staples")
    merged = held.merged_with(SecurityUpdate(Ticker("KO"), {"sector": "Beverages"}))
    assert (merged.sector, merged.cik, merged.name) == ("Beverages", "0000021344", "Coca-Cola")


def test_an_explicit_none_does_overwrite():
    held = Security(Ticker("KO"), sector="Staples")
    assert held.merged_with(SecurityUpdate(Ticker("KO"), {"sector": None})).sector is None


def test_an_update_for_another_ticker_is_refused():
    with pytest.raises(InvariantViolation):
        Security(Ticker("KO")).merged_with(SecurityUpdate(Ticker("PG"), {"name": "x"}))


def test_a_new_security_takes_the_defaults_for_what_is_not_provided():
    sec = Security.new(SecurityUpdate(Ticker("shy"), {"kind": "etf"}))
    assert (str(sec.ticker), sec.kind, sec.active, sec.has_fundamentals) == (
        "SHY",
        SecurityKind.ETF,
        True,
        False,
    )


def test_resolving_the_identity_keeps_our_name_over_the_secs():
    named = Security(Ticker("IBM"), name="IBM")
    named.resolve_identity("0000051143", "INTERNATIONAL BUSINESS MACHINES CORP")
    assert (named.cik, named.name) == ("0000051143", "IBM")
    unnamed = Security(Ticker("KO"))
    unnamed.resolve_identity("0000021344", "COCA COLA CO")
    assert unnamed.name == "COCA COLA CO"


def test_unknown_update_fields_and_bad_kinds_are_errors():
    with pytest.raises(InvariantViolation):
        SecurityUpdate(Ticker("KO"), {"colour": "red"})
    with pytest.raises(InvariantViolation, match="kind must be one of"):
        SecurityUpdate(Ticker("KO"), {"kind": "crypto"})


# --- universe validation ------------------------------------------------------------------------


def test_universe_rows_become_updates_without_inventing_fields():
    [update] = security_updates([{"ticker": "aapl", "name": "Apple", "sector": "Tech"}])
    assert update.ticker == Ticker("AAPL")
    assert dict(update.changes) == {"name": "Apple", "sector": "Tech"}  # no kind, no cik


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"ticker": "A", "colour": "red"}], "unknown universe fields"),
        ([{"ticker": "A"}, {"ticker": "a"}], "duplicate ticker"),
        ([{"ticker": "A", "kind": "crypto"}], "kind must be one of"),
    ],
)
def test_invalid_universes_are_rejected(rows, message):
    with pytest.raises(InvariantViolation, match=message):
        security_updates(rows)


def test_bond_series_are_validated():
    ok = bond_series([{"id": "DGS10", "kind": "yield", "name": "10Y"}])
    assert (ok[0].id, ok[0].kind) == ("DGS10", "yield")
    with pytest.raises(InvariantViolation, match="duplicate series id"):
        bond_series([{"id": "X", "kind": "yield"}, {"id": "X", "kind": "spread"}])
    with pytest.raises(InvariantViolation, match="kind must be"):
        bond_series([{"id": "X", "kind": "price"}])

"""Metrics layer tests with hand-computed expectations (see EXPECTED below)."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from fin_core.db import repo
from fin_core.db.session import session_scope
from fin_core.metrics import UnknownSecurityError, compute_metrics
from fin_core.metrics.annual import annual_series
from fin_core.metrics.compute import consecutive_positive_years, earnings_growth
from fin_core.providers.edgar_concepts import CONCEPT_TAGS
from fin_core.scoring import score_security

YEARS = list(range(2013, 2025))
EPS = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.0, 2.2, 2.5]
DPS = [0.5, 0.5, 0.5, 0.6, 0.6, 0.0, 0.7, 0.7, 0.8, 0.8, 0.9, 1.0]  # 2018 skipped

# Latest fiscal year (2024): eps 2.5, equity 1000, shares 100 -> bvps 10; price 25.
EXPECTED = dict(
    pe=10.0,  # 25 / 2.5
    pb=2.5,  # 25 / (1000 / 100)
    current_ratio=2.0,  # 500 / 250
    debt_to_equity=0.4,  # 400 / 1000
    roe=0.25,  # 250 / 1000
    dividend_years=6,  # 2019..2024; the 2018 zero ends the streak
    earnings_growth=(2.0 + 2.2 + 2.5) / 3 / ((1.2 + 1.3 + 1.4) / 3) - 1,  # 2015-17 -> 2022-24
)


def short_accession(*parts) -> str:
    """Unique per (tag, period, form, filing) and within VARCHAR(32), like a real accession."""
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:20]  # noqa: S324


def fact(
    concept,
    value,
    end,
    *,
    tag=None,
    unit="USD",
    instant=False,
    form="10-K",
    start=None,
    filed=None,
    accession=None,
):
    tag = tag or CONCEPT_TAGS[concept][0]
    start = end if instant else (start or date(end.year, 1, 1))
    filed = filed or date(end.year + 1, 2, 15)
    return dict(
        concept=concept,
        tag=tag,
        unit=unit,
        period_start=start,
        period_end=end,
        instant=instant,
        fiscal_year=end.year,
        fiscal_period="FY",
        form=form,
        filed=filed,
        accession=accession or short_accession(tag, end, form, filed),
        value=Decimal(str(value)),
    )


def company_facts():
    rows = []
    for year, eps, dps in zip(YEARS, EPS, DPS, strict=True):
        end = date(year, 12, 31)
        rows += [
            fact("EpsDiluted", eps, end, unit="USD/shares"),
            fact("DividendsPerShare", dps, end, unit="USD/shares"),
        ]
    end = date(2024, 12, 31)
    rows += [
        fact("StockholdersEquity", 1000, end, instant=True),
        fact("AssetsCurrent", 500, end, instant=True),
        fact("LiabilitiesCurrent", 250, end, instant=True),
        fact("LongTermDebt", 400, end, instant=True),
        fact("Assets", 2000, end, instant=True),
        fact("NetIncome", 250, end),
        # cover-page share count from the latest 10-K
        fact(
            "SharesOutstanding",
            100,
            date(2025, 1, 31),
            unit="shares",
            instant=True,
            filed=date(2025, 2, 15),
        ),
    ]
    return rows


@pytest.fixture
def db(raw_engine):
    with session_scope(raw_engine) as s:
        repo.upsert_securities(s, [{"ticker": "ACME"}, {"ticker": "EMPTY"}])
        sid = repo.get_security(s, "ACME").id
        repo.upsert_facts(s, sid, company_facts())
        bar = dict(
            ts=datetime(2025, 3, 3, 21, tzinfo=UTC),
            open=25.0,
            high=25.0,
            low=25.0,
            close=25.0,
            volume=1,
        )
        repo.upsert_prices(s, sid, [bar])
    return raw_engine


def metrics(db, as_of=date(2025, 3, 10), ticker="ACME"):
    with session_scope(db) as s:
        return compute_metrics(s, ticker, as_of)


def test_ratios_match_hand_computed_values(db):
    m = metrics(db)
    for name, expected in EXPECTED.items():
        assert getattr(m, name) == pytest.approx(expected), name
    assert (m.price, m.eps, m.book_value_per_share) == (25.0, 2.5, 10.0)
    assert m.latest_fiscal_year_end == date(2024, 12, 31)
    assert m.warnings == []
    assert [p.value for p in m.annual["EpsDiluted"]] == EPS  # full history, oldest first


def test_point_in_time_only_sees_filings_made_by_as_of(db):
    m = metrics(db, as_of=date(2024, 3, 1))  # FY2023 report was filed 2024-02-15; FY2024 not yet
    assert m.latest_fiscal_year_end == date(2023, 12, 31)
    assert m.eps == 2.2
    assert m.pe is None  # no price existed yet
    assert "no price on or before as_of" in m.warnings
    assert metrics(db, as_of=date(2024, 1, 15)).latest_fiscal_year_end == date(2022, 12, 31)


def test_restatement_is_invisible_until_it_is_filed(db):
    with session_scope(db) as s:
        sid = repo.get_security(s, "ACME").id
        repo.upsert_facts(
            s,
            sid,
            [
                fact(
                    "EpsDiluted",
                    2.0,
                    date(2024, 12, 31),
                    unit="USD/shares",
                    form="10-K/A",
                    filed=date(2025, 6, 1),
                )
            ],
        )
    assert metrics(db, as_of=date(2025, 3, 10)).eps == 2.5  # original number
    assert metrics(db, as_of=date(2025, 7, 1)).eps == 2.0  # amended number, once public


def test_quarterly_and_partial_year_figures_are_ignored(db):
    with session_scope(db) as s:
        sid = repo.get_security(s, "ACME").id
        repo.upsert_facts(
            s,
            sid,
            [
                # a 10-Q: not an annual report
                fact(
                    "EpsDiluted",
                    9.9,
                    date(2025, 3, 31),
                    unit="USD/shares",
                    form="10-Q",
                    start=date(2025, 1, 1),
                    filed=date(2025, 3, 5),
                ),
                # inside a 10-K but only a 91-day period
                fact(
                    "EpsDiluted",
                    8.8,
                    date(2024, 12, 31),
                    unit="USD/shares",
                    start=date(2024, 10, 1),
                    filed=date(2025, 2, 15),
                    accession="q4",
                ),
            ],
        )
    m = metrics(db, as_of=date(2025, 3, 10))
    assert m.eps == 2.5 and len(m.annual["EpsDiluted"]) == 12


def test_share_count_comes_from_the_latest_report_of_any_periodic_form(db):
    with session_scope(db) as s:
        sid = repo.get_security(s, "ACME").id
        repo.upsert_facts(
            s,
            sid,
            [
                fact(
                    "SharesOutstanding",
                    125,
                    date(2025, 4, 30),
                    unit="shares",
                    instant=True,
                    form="10-Q",
                    filed=date(2025, 5, 5),
                )
            ],
        )
    assert metrics(db, as_of=date(2025, 6, 1)).book_value_per_share == pytest.approx(8.0)


def test_tag_priority_keeps_the_series_continuous_across_tag_changes():
    old, new = CONCEPT_TAGS["Revenue"][2], CONCEPT_TAGS["Revenue"][0]

    class F:  # minimal stand-in; annual_series only reads attributes
        def __init__(self, row):
            self.__dict__.update(row)

    rows = [
        F(fact("Revenue", 100, date(2020, 12, 31), tag=old)),  # only the old tag exists
        F(fact("Revenue", 110, date(2021, 12, 31), tag=old)),
        F(fact("Revenue", 120, date(2021, 12, 31), tag=new)),  # both exist: the preferred tag wins
        F(fact("Revenue", 130, date(2022, 12, 31), tag=new)),
    ]
    series = annual_series(rows)
    assert series["Revenue"] == [
        (date(2020, 12, 31), 100.0),
        (date(2021, 12, 31), 120.0),
        (date(2022, 12, 31), 130.0),
    ]


def test_loss_making_and_negative_equity_stay_visible_as_negative_values(raw_engine):
    end = date(2024, 12, 31)
    with session_scope(raw_engine) as s:
        repo.upsert_securities(s, [{"ticker": "LOSS"}])
        sid = repo.get_security(s, "LOSS").id
        repo.upsert_facts(
            s,
            sid,
            [
                fact("EpsDiluted", -2.0, end, unit="USD/shares"),
                fact("StockholdersEquity", -500, end, instant=True),
                fact("LongTermDebt", 300, end, instant=True),
                fact("NetIncome", -100, end),
                fact(
                    "SharesOutstanding",
                    100,
                    date(2025, 1, 31),
                    unit="shares",
                    instant=True,
                    filed=date(2025, 2, 15),
                ),
            ],
        )
        repo.upsert_prices(
            s,
            sid,
            [
                dict(
                    ts=datetime(2025, 3, 3, tzinfo=UTC),
                    open=10.0,
                    high=10.0,
                    low=10.0,
                    close=10.0,
                    volume=1,
                )
            ],
        )
        m = compute_metrics(s, "LOSS", date(2025, 3, 10))
    assert m.pe == pytest.approx(-5.0)  # kept, so the Graham node can reject it explicitly
    assert m.pb == pytest.approx(-2.0)  # 10 / (-500 / 100)
    assert m.debt_to_equity == pytest.approx(-0.6)
    assert m.roe is None  # return on negative equity is meaningless


def test_zero_eps_zero_equity_have_no_ratio(raw_engine):
    end = date(2024, 12, 31)
    with session_scope(raw_engine) as s:
        repo.upsert_securities(s, [{"ticker": "ZERO"}])
        sid = repo.get_security(s, "ZERO").id
        repo.upsert_facts(
            s,
            sid,
            [
                fact("EpsDiluted", 0, end, unit="USD/shares"),
                fact("StockholdersEquity", 0, end, instant=True),
                fact("LongTermDebt", 5, end, instant=True),
            ],
        )
        repo.upsert_prices(
            s,
            sid,
            [
                dict(
                    ts=datetime(2025, 3, 3, tzinfo=UTC),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1,
                )
            ],
        )
        m = compute_metrics(s, "ZERO", date(2025, 3, 10))
    assert (m.pe, m.pb, m.debt_to_equity) == (None, None, None)


def test_missing_everything_gives_nones_and_explicit_warnings(db):
    m = metrics(db, ticker="EMPTY")
    assert m.pe is None and m.pb is None and m.current_ratio is None and m.annual == {}
    assert m.dividend_years is None  # unknown, not zero
    assert "no price on or before as_of" in m.warnings
    assert "no annual fundamentals available as of this date" in m.warnings


def test_stale_data_is_flagged(db):
    m = metrics(db, as_of=date(2026, 12, 1))  # last bar 2025-03-03, last FY 2024-12-31
    assert any(w.startswith("price is stale") for w in m.warnings)
    assert any(w.startswith("fundamentals are stale") for w in m.warnings)


def test_unknown_ticker_raises(db):
    with session_scope(db) as s, pytest.raises(UnknownSecurityError):
        compute_metrics(s, "NOPE", date(2025, 3, 10))


def test_consecutive_positive_years():
    d = lambda y: date(y, 12, 31)  # noqa: E731
    assert consecutive_positive_years([(d(2021), 1), (d(2022), 1), (d(2023), 1)]) == 3
    assert consecutive_positive_years([(d(2021), 1), (d(2022), 0), (d(2023), 1)]) == 1
    assert consecutive_positive_years([(d(2019), 1), (d(2022), 1), (d(2023), 1)]) == 2  # gap
    assert consecutive_positive_years([(d(2023), 0)]) == 0


def test_earnings_growth_rules():
    d = lambda y: date(y, 12, 31)  # noqa: E731
    five = [(d(2020 + i), 1.0 + i) for i in range(5)]
    assert earnings_growth(five) is None  # fewer than 6 years
    six = [(d(2019 + i), v) for i, v in enumerate([1, 1, 1, 2, 2, 2])]
    assert earnings_growth(six) == pytest.approx(1.0)  # avg 1 -> avg 2
    assert earnings_growth([(d(2019 + i), v) for i, v in enumerate([0, 0, 0, 1, 1, 1])]) is None


def test_score_security_runs_the_graph_on_database_metrics(db):
    with session_scope(db) as s:
        out = score_security(s, "ACME", date(2025, 3, 10))
    graham = out["philosophy_results"]["graham"]
    passed = {c.name for c in graham.passed_criteria}
    # pe 10 < 15, current ratio 2.0 >= 2, debt/equity 0.4 < 1, growth 0.72 >= 0
    assert passed == {
        "P/E below limit",
        "Current ratio at least",
        "Debt/equity below limit",
        "Earnings growth at least",
    }
    # pb 2.5 > 1.5, pe x pb 25 > 22.5, only 6 dividend years < 10
    assert graham.score == pytest.approx(100 * 4 / 7, abs=0.1)
    assert out["metrics"].warnings == []
    assert out["signal"] is None  # 57 is below the buy threshold

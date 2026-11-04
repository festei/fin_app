"""One contract for the market-data repositories, run against the in-memory fakes and the SQL
adapter (SQLite, and Postgres when TEST_DATABASE_URL is set)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db_fixtures import uow_factory  # noqa: F401 - pytest fixture

from fin_domain.market_data import (
    CONCEPT_TAGS,
    Fact,
    PriceBar,
    Security,
    SecurityKind,
    SecurityUpdate,
    YieldPoint,
    known_as_of,
)
from fin_domain.shared import NotFound, Ticker

KO, IBM, SHY = Ticker("KO"), Ticker("IBM"), Ticker("SHY")
T0 = datetime(2026, 1, 5, 5, 0, tzinfo=UTC)


def update(ticker, **changes):
    return SecurityUpdate(ticker, changes)


@pytest.fixture
def seeded(uow_factory):  # noqa: F811
    with uow_factory() as uow:
        uow.securities.upsert(
            [update(KO, name="Coca-Cola", sector="Staples"), update(IBM), update(SHY, kind="etf")]
        )
    return uow_factory


def fact(value, *, filed=date(2025, 2, 15), accession="a1", tag=None, concept="Revenue"):
    end = date(2024, 12, 31)
    return Fact(
        concept=concept,
        tag=tag or CONCEPT_TAGS[concept][0],
        unit="USD",
        period_start=date(2024, 1, 1),
        period_end=end,
        instant=False,
        fiscal_year=2024,
        fiscal_period="FY",
        form="10-K",
        filed=filed,
        accession=accession,
        value=Decimal(str(value)),
    )


# --- securities ---------------------------------------------------------------------------------


def test_new_securities_take_defaults_and_come_back_ordered(seeded):
    with seeded() as uow:
        listed = uow.securities.list()
    assert [str(s.ticker) for s in listed] == ["IBM", "KO", "SHY"]
    by = {str(s.ticker): s for s in listed}
    assert by["IBM"].kind is SecurityKind.STOCK and by["IBM"].active
    assert by["SHY"].kind is SecurityKind.ETF and not by["SHY"].has_fundamentals
    assert (by["KO"].name, by["KO"].sector) == ("Coca-Cola", "Staples")


def test_an_update_touches_only_the_fields_it_provides(seeded):
    with seeded() as uow:
        ko = uow.securities.get(KO)
        ko.resolve_identity("0000021344", "ignored")
        uow.securities.save(ko)
    with seeded() as uow:  # the universe sync knows no CIK and must not wipe it
        uow.securities.upsert([update(KO, sector="Beverages")])
    with seeded() as uow:
        ko = uow.securities.get(KO)
    assert (ko.cik, ko.name, ko.sector) == ("0000021344", "Coca-Cola", "Beverages")


def test_an_explicit_none_overwrites(seeded):
    with seeded() as uow:
        uow.securities.upsert([update(KO, sector=None)])
    with seeded() as uow:
        assert uow.securities.get(KO).sector is None


def test_upserting_the_same_universe_twice_changes_nothing(seeded):
    with seeded() as uow:
        before = uow.securities.list()
        assert uow.securities.upsert([update(KO, name="Coca-Cola"), update(IBM)]) == 2
    with seeded() as uow:
        assert uow.securities.list() == before


def test_inactive_securities_are_listed_only_on_request(seeded):
    with seeded() as uow:
        uow.securities.upsert([update(IBM, active=False)])
    with seeded() as uow:
        assert [str(s.ticker) for s in uow.securities.list()] == ["KO", "SHY"]
        assert [str(s.ticker) for s in uow.securities.list(active_only=False)] == [
            "IBM",
            "KO",
            "SHY",
        ]


def test_an_unknown_security_is_none_and_cannot_be_saved(seeded):
    with seeded() as uow:
        assert uow.securities.get(Ticker("ZZZ")) is None
        with pytest.raises(LookupError):
            uow.securities.save(Security(Ticker("ZZZ")))


# --- prices -------------------------------------------------------------------------------------


def bars(*closes, start=T0):
    return [
        PriceBar(ts=start + timedelta(days=i), close=c, open=c - 1, high=c + 1, low=c - 2, volume=7)
        for i, c in enumerate(closes)
    ]


def test_prices_round_trip_as_aware_utc(seeded):
    with seeded() as uow:
        assert uow.prices.upsert(KO, bars(10.0, 11.0)) == 2
    with seeded() as uow:
        latest = uow.prices.latest(KO)
    assert latest == bars(10.0, 11.0)[1]
    assert latest.ts.utcoffset() == timedelta(0)


def test_a_later_run_overwrites_the_bar_of_the_same_day(seeded):
    with seeded() as uow:
        uow.prices.upsert(KO, bars(10.0, 11.0))
        uow.prices.upsert(KO, bars(10.5))
    with seeded() as uow:
        closes = uow.prices.closes_since(KO, T0)
    assert closes == {T0: 10.5, T0 + timedelta(days=1): 11.0}


def test_latest_respects_as_of(seeded):
    with seeded() as uow:
        uow.prices.upsert(KO, bars(10.0, 11.0, 12.0))
    with seeded() as uow:
        assert uow.prices.latest(KO, T0 + timedelta(days=1)).close == 11.0
        assert uow.prices.latest(KO, T0 - timedelta(days=1)) is None
        assert uow.prices.latest(IBM) is None


def test_closes_since_is_inclusive(seeded):
    with seeded() as uow:
        uow.prices.upsert(KO, bars(10.0, 11.0, 12.0))
    with seeded() as uow:
        assert set(uow.prices.closes_since(KO, T0 + timedelta(days=1))) == {
            T0 + timedelta(days=1),
            T0 + timedelta(days=2),
        }


def test_only_securities_with_bars_are_reported(seeded):
    with seeded() as uow:
        assert uow.prices.tickers_with_bars() == set()
        uow.prices.upsert(KO, bars(10.0))
    with seeded() as uow:
        assert uow.prices.tickers_with_bars() == {KO}


def test_prices_for_an_unknown_security_are_refused(seeded):
    with seeded() as uow, pytest.raises(NotFound):
        uow.prices.upsert(Ticker("ZZZ"), bars(1.0))


# --- facts --------------------------------------------------------------------------------------


def test_facts_are_idempotent_on_their_natural_key(seeded):
    with seeded() as uow:
        assert uow.facts.add(KO, [fact(100), fact(100)]) == 2  # rows offered, as before
    with seeded() as uow:
        uow.facts.add(KO, [fact(100)])
    with seeded() as uow:
        assert len(uow.facts.filed_up_to(KO, date(2030, 1, 1))) == 1


def test_every_filed_version_is_kept_and_the_domain_picks_among_them(seeded):
    original = fact(1000, filed=date(2025, 2, 15), accession="orig")
    restated = fact(900, filed=date(2025, 8, 1), accession="rest")
    with seeded() as uow:
        uow.facts.add(KO, [original, restated])
    with seeded() as uow:
        before = known_as_of(uow.facts.filed_up_to(KO, date(2025, 7, 31)), date(2025, 7, 31))
        after = known_as_of(uow.facts.filed_up_to(KO, date(2025, 9, 1)), date(2025, 9, 1))
    assert [f.value for f in before] == [Decimal("1000")]
    assert [f.value for f in after] == [Decimal("900")]


def test_filed_up_to_filters_by_date_and_concept(seeded):
    rows = [
        fact(100, concept="Revenue", accession="r"),
        fact(5, concept="NetIncome", accession="n"),
        fact(7, concept="Revenue", filed=date(2026, 1, 1), accession="late"),
    ]
    with seeded() as uow:
        uow.facts.add(KO, rows)
    with seeded() as uow:
        assert len(uow.facts.filed_up_to(KO, date(2025, 12, 31))) == 2
        only = uow.facts.filed_up_to(KO, date(2025, 12, 31), ["Revenue"])
    assert [f.concept for f in only] == ["Revenue"]


def test_later_stored_versions_get_a_higher_sequence(seeded):
    with seeded() as uow:
        uow.facts.add(KO, [fact(1, accession="a"), fact(2, accession="b")])
    with seeded() as uow:
        first, second = sorted(
            uow.facts.filed_up_to(KO, date(2030, 1, 1)), key=lambda f: f.sequence
        )
    assert (first.accession, second.accession) == ("a", "b")


def test_facts_for_an_unknown_security_are_refused(seeded):
    with seeded() as uow, pytest.raises(NotFound):
        uow.facts.add(Ticker("ZZZ"), [fact(1)])


# --- yields -------------------------------------------------------------------------------------


def test_yields_overwrite_by_day_and_report_the_latest(seeded):
    d1, d2 = date(2025, 1, 2), date(2025, 1, 6)
    with seeded() as uow:
        assert uow.yields.latest_date("DGS10") is None
        uow.yields.upsert("DGS10", [YieldPoint(d1, Decimal("4.5")), YieldPoint(d2, Decimal("4.6"))])
        uow.yields.upsert("DGS10", [YieldPoint(d2, Decimal("4.62"))])
    with seeded() as uow:
        assert uow.yields.latest_date("DGS10") == d2
        assert uow.yields.latest("DGS10").value == Decimal("4.62")
        assert uow.yields.latest("DGS10", d1).value == Decimal("4.5")
        assert uow.yields.latest("DGS10", date(2024, 1, 1)) is None
        assert uow.yields.latest("DGS2") is None

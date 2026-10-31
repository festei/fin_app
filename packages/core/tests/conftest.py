import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from fin_core.db import migrate, repo
from fin_core.db.session import make_engine, session_scope
from fin_core.providers.edgar_concepts import CONCEPT_TAGS

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_json():
    def load(name: str):
        return json.loads((FIXTURES / name).read_text())

    return load


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def raw_engine(request, tmp_path):
    """A migrated, empty database. Always SQLite; also real Postgres when TEST_DATABASE_URL
    is set (e.g. postgresql+psycopg://user:pw@localhost:5432/fin_test, UTF-8 encoded)."""
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'test.db'}"
    else:
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            pytest.skip("TEST_DATABASE_URL not set")
    eng = make_engine(url)
    if request.param == "postgres":  # start from an empty schema
        with eng.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    migrate.upgrade(url)
    yield eng
    eng.dispose()


EPS_HISTORY = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.0, 2.2, 2.5]  # 2013-2024
DPS_HISTORY = [0.5, 0.5, 0.5, 0.6, 0.6, 0.0, 0.7, 0.7, 0.8, 0.8, 0.9, 1.0]


def _fact(concept, value, end, *, unit="USD", instant=False, filed=None):
    tag = CONCEPT_TAGS[concept][0]
    return dict(
        concept=concept, tag=tag, unit=unit,
        period_start=end if instant else date(end.year, 1, 1), period_end=end, instant=instant,
        fiscal_year=end.year, fiscal_period="FY", form="10-K",
        filed=filed or date(end.year + 1, 2, 15), accession=f"{concept[:7]}-{end.year}-{tag[-6:]}",
        value=Decimal(str(value)),
    )  # fmt: skip


def bar(day: date, close: float) -> dict:
    return dict(ts=datetime(day.year, day.month, day.day, 21, tzinfo=UTC), open=close, high=close,
                low=close, close=close, volume=1)  # fmt: skip


@pytest.fixture
def seed_company():
    """Seed a company with 12 years of EPS/dividends and a clean 2024 balance sheet (equity 1000,
    100 shares, book value 10/share, EPS 2.5). Its conviction is steered by the price alone
    (graham + lynch, equal weights; buffett/greenblatt/fisher abstain for lack of history;
    each score is rounded to one decimal before averaging, e.g. (85.7 + 100) / 2 = 92.85 -> 92.8):

        price 12  -> graham 6/7 = 85.7, lynch 100  -> conviction 92.8
        price 25  -> graham 4/7 = 57.1, lynch 100  -> conviction 78.5
        price 100 -> graham 3/7 = 42.9, lynch 1/3  -> conviction 38.1
    """

    def seed(engine, ticker="ACME", *, price=25.0, price_day=date(2025, 3, 3), sector=None):
        rows = []
        for year, eps, dps in zip(range(2013, 2025), EPS_HISTORY, DPS_HISTORY, strict=True):
            end = date(year, 12, 31)
            rows += [_fact("EpsDiluted", eps, end, unit="USD/shares"),
                     _fact("DividendsPerShare", dps, end, unit="USD/shares")]  # fmt: skip
        end = date(2024, 12, 31)
        rows += [
            _fact("StockholdersEquity", 1000, end, instant=True),
            _fact("AssetsCurrent", 500, end, instant=True),
            _fact("LiabilitiesCurrent", 250, end, instant=True),
            _fact("LongTermDebt", 400, end, instant=True),
            _fact("Assets", 2000, end, instant=True),
            _fact("NetIncome", 250, end),
            _fact("SharesOutstanding", 100, date(2025, 1, 31), unit="shares", instant=True,
                  filed=date(2025, 2, 15)),
        ]  # fmt: skip
        with session_scope(engine) as s:
            repo.upsert_securities(
                s, [{"ticker": ticker, **({"sector": sector} if sector else {})}]
            )
            sid = repo.get_security(s, ticker).id
            repo.upsert_facts(s, sid, rows)
            repo.upsert_prices(s, sid, [bar(price_day, price)])
        return sid

    return seed

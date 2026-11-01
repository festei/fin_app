"""Seed a throw-away database with SYNTHETIC data so the UI can be run and looked at.

Everything here is made up (fictional companies, invented fundamentals and prices). The real
scoring, alert and sandbox code runs over it week by week, so the opportunities, charts, alerts
and portfolios are what the app would produce, not hand-drawn fixtures.

    uv run python scripts/seed_demo.py sqlite:///demo.db
    DATABASE_URL=sqlite:///demo.db API_TOKEN=dev-token uv run uvicorn fin_api.main:app

Never point this at a real database: it creates securities, scores, alerts and portfolios. It
therefore refuses anything but a SQLite URL, and any database that already holds securities,
unless you pass --force. The dates are relative to today (the last scored week is the most recent
Friday), so re-seed when the demo gets old; the Opportunities list hides scores older than 10 days.
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fin_core.alerts.channels import InAppChannel
from fin_core.db import migrate, repo, signals_repo
from fin_core.db.session import make_engine, session_scope
from fin_core.providers.edgar_concepts import CONCEPT_TAGS
from fin_core.sandbox import engine as sandbox
from fin_core.sandbox.config import SandboxConfig
from fin_core.sandbox.run import run_sandbox
from fin_core.scoring_run import run_scoring

EPS = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.0, 2.2, 2.5, 2.7]  # oldest first
DPS = [0.5, 0.5, 0.5, 0.6, 0.6, 0.0, 0.7, 0.7, 0.8, 0.8, 0.9, 1.0, 1.0]

# ticker, name, sector, scale k (fundamentals and price level), price path anchors
# (week 0 = first scored week, price as a multiple of "12 / 25 / 100" style levels: cheap=12,
# fair=25, expensive=100 at k=1)
COMPANIES = [
    ("ACME", "Acme Industrial", "Industrials", 1.0, [(0, 100), (14, 25), (26, 25), (34, 12)]),
    ("BOLT", "Bolt Software", "Technology", 2.0, [(0, 12), (12, 25), (22, 100), (34, 100)]),
    ("CRUX", "Crux Health", "Healthcare", 0.8, [(0, 25), (34, 25)]),
    ("DELT", "Delta Foods", "Consumer Staples", 1.5, [(0, 100), (34, 100)]),
    ("EMBR", "Ember Energy", "Energy", 1.2, [(0, 25), (10, 12), (20, 25), (30, 100), (34, 12)]),
    ("FLUX", "Flux Materials", "Materials", 0.6, [(0, 12), (34, 25)]),
]
WEEKS = 35


def last_friday(today: date) -> date:
    return today - timedelta(days=(today.weekday() - 4) % 7)


TODAY = datetime.now(UTC).date()
START = last_friday(TODAY) - timedelta(weeks=WEEKS - 1)  # the last scored week is the latest Friday
# latest fiscal year whose 10-K (filed mid February) is public by START; the demo's history then
# also sees the following year's report arrive during the run
LATEST_FY = START.year - 1 if START >= date(START.year, 3, 1) else START.year - 2
YEARS = list(range(LATEST_FY - 11, LATEST_FY + 2))  # 12 years of EPS history at START, plus 1


def fact(concept, value, end, *, unit="USD", instant=False, filed=None):
    tag = CONCEPT_TAGS[concept][0]
    return dict(
        concept=concept, tag=tag, unit=unit,
        period_start=end if instant else date(end.year, 1, 1), period_end=end, instant=instant,
        fiscal_year=end.year, fiscal_period="FY", form="10-K",
        filed=filed or date(end.year + 1, 2, 15), accession=f"{concept[:7]}-{end.year}-{tag[-6:]}",
        value=Decimal(str(round(value, 4))),
    )  # fmt: skip


def facts_for(k: float) -> list[dict]:
    rows = []
    for year, eps, dps in zip(YEARS, EPS, DPS, strict=True):
        end = date(year, 12, 31)
        rows += [fact("EpsDiluted", eps * k, end, unit="USD/shares"),
                 fact("DividendsPerShare", dps * k, end, unit="USD/shares")]  # fmt: skip
    for year in (LATEST_FY - 1, LATEST_FY, LATEST_FY + 1):
        end = date(year, 12, 31)
        rows += [
            fact("StockholdersEquity", 1000 * k, end, instant=True),
            fact("AssetsCurrent", 500 * k, end, instant=True),
            fact("LiabilitiesCurrent", 250 * k, end, instant=True),
            fact("LongTermDebt", 400 * k, end, instant=True),
            fact("Assets", 2000 * k, end, instant=True),
            fact("NetIncome", 250 * k, end),
            fact("SharesOutstanding", 100, date(year + 1, 1, 31), unit="shares", instant=True,
                 filed=date(year + 1, 2, 15)),
        ]  # fmt: skip
    return rows


def price_path(anchors, k, rng, days):
    """Daily closes, one per weekday, interpolated between weekly anchors with a little noise."""
    out = {}
    d = START - timedelta(days=days)
    total_days = (START + timedelta(weeks=WEEKS) - d).days
    for i in range(total_days + 1):
        day = d + timedelta(days=i)
        if day.weekday() >= 5:
            continue
        week = (day - START).days / 7
        if week <= anchors[0][0]:
            level = anchors[0][1]
        elif week >= anchors[-1][0]:
            level = anchors[-1][1]
        else:
            for (w0, p0), (w1, p1) in zip(anchors, anchors[1:], strict=False):
                if w0 <= week <= w1:
                    level = p0 + (p1 - p0) * (week - w0) / (w1 - w0)
                    break
        out[day] = max(0.5, level * k * (1 + rng.uniform(-0.012, 0.012)))
    return out


def bars(prices):
    out = []
    for d, p in prices.items():
        ts = datetime(d.year, d.month, d.day, 21, tzinfo=UTC)
        out.append(
            {"ts": ts, "open": p * 0.995, "high": p * 1.01, "low": p * 0.985, "close": p,
             "volume": 1_000_000}
        )  # fmt: skip
    return out


def check_safe(url: str, force: bool) -> None:
    if force:
        return
    if not url.startswith("sqlite"):
        sys.exit(f"refusing to seed a non-SQLite database ({url.split(':')[0]}); use --force")
    engine = make_engine(url)
    try:
        migrate.upgrade(url)
        with session_scope(engine) as s:
            if repo.list_securities(s, active_only=False):
                sys.exit("refusing to seed a database that already has securities; use --force")
    finally:
        engine.dispose()


def seed(url: str, force: bool = False) -> None:
    check_safe(url, force)
    migrate.upgrade(url)
    engine = make_engine(url)
    rng = random.Random(42)
    with session_scope(engine) as s:
        repo.upsert_securities(
            s, [{"ticker": t, "name": n, "sector": sec} for t, n, sec, _, _ in COMPANIES]
        )
        for ticker, _, _, k, anchors in COMPANIES:
            sec_id = repo.get_security(s, ticker).id
            repo.upsert_facts(s, sec_id, facts_for(k))
            repo.upsert_prices(s, sec_id, bars(price_path(anchors, k, rng, days=400)))
        # follow two stocks, so sell alerts exist for them
        for t in ("ACME", "BOLT"):
            signals_repo.watchlist_add(s, repo.get_security(s, t).id)

    defaults = SandboxConfig.load()
    with session_scope(engine) as s:
        strategy = sandbox.create_portfolio(
            s, "My $100 (signals)", mode="strategy", cash=100,
            strategy={"position_pct": 0.3}, defaults=defaults,
        ).id  # fmt: skip
        manual = sandbox.create_portfolio(
            s, "Manual picks", mode="manual", cash=1000, defaults=defaults
        ).id

    channels = [InAppChannel()]
    for week in range(WEEKS):
        friday = START + timedelta(weeks=week)
        stamp = datetime(friday.year, friday.month, friday.day, 23, tzinfo=UTC)
        if week == 3:  # the manual portfolio buys two stocks early on
            with session_scope(engine) as s:
                for t, amount in (("CRUX", 300), ("DELT", 200)):
                    sandbox.place_order(s, manual, t, "buy", notional=amount, now=stamp)
        if week == 20:
            with session_scope(engine) as s:
                sandbox.place_order(s, manual, "DELT", "sell", now=stamp)
        run_scoring(engine, channels=channels, as_of=friday)
        run_sandbox(engine, as_of=stamp)
    print(f"seeded {url}: {len(COMPANIES)} synthetic companies, {WEEKS} weeks of history")
    print(f"portfolios: strategy={strategy} manual={manual}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force"]
    if len(args) != 1:
        sys.exit(__doc__)
    seed(args[0], force="--force" in sys.argv[1:])

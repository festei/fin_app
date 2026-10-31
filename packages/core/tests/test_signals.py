"""Signal engine: the persisted state machine.

`seed_company` (conftest) steers the conviction by price alone: 92.8 at price 12, 78.5 at 25 and
38.1 at 100 (philosophy scores are rounded to one decimal before they are averaged).
"""

from __future__ import annotations

from datetime import date

import pytest
from conftest import bar
from sqlalchemy import func, select

from fin_core.db import repo, signals_repo
from fin_core.db.models import ScoreRecord, SignalRecord
from fin_core.db.session import session_scope
from fin_core.signals import score_and_record, score_universe

AS_OF = date(2025, 3, 10)


def run(engine, as_of=AS_OF, ticker="ACME"):
    with session_scope(engine) as s:
        outcome = score_and_record(s, ticker, as_of)
        return outcome.score.conviction, outcome.signal.kind if outcome.signal else None


def count(engine, model):
    with session_scope(engine) as s:
        return s.scalar(select(func.count()).select_from(model))


def add_price(engine, day, close, ticker="ACME"):
    with session_scope(engine) as s:
        repo.upsert_prices(s, repo.get_security(s, ticker).id, [bar(day, close)])


def test_first_high_score_is_emerging_and_everything_is_stored(raw_engine, seed_company):
    seed_company(raw_engine, price=25.0)
    conviction, kind = run(raw_engine)
    assert conviction == pytest.approx(78.5, abs=0.06) and kind == "emerging"
    with session_scope(raw_engine) as s:
        score = s.scalars(select(ScoreRecord)).one()
        assert score.signal_blocked is False and score.all_abstained is False
        assert set(score.details["philosophies"]) == {
            "graham", "buffett", "lynch", "greenblatt", "fisher"
        }  # fmt: skip
        assert score.details["philosophies"]["lynch"]["score"] == 100.0
        assert score.details["metrics"]["price"] == 25.0
        assert score.details["metrics"]["price_date"] == "2025-03-03"
        signal = s.scalars(select(SignalRecord)).one()
        assert (signal.kind, signal.score_id) == ("emerging", score.id)


def test_rerunning_with_unchanged_data_is_idempotent(raw_engine, seed_company):
    seed_company(raw_engine, price=25.0)
    run(raw_engine)
    assert run(raw_engine)[1] is None and run(raw_engine)[1] is None
    assert count(raw_engine, SignalRecord) == 1  # no duplicate signals...
    assert count(raw_engine, ScoreRecord) == 3  # ...but every run is kept as history


def test_transitions_emerging_then_sell_then_buy(raw_engine, seed_company):
    seed_company(raw_engine, price=25.0)
    assert run(raw_engine) == (pytest.approx(78.5, abs=0.06), "emerging")

    add_price(raw_engine, date(2025, 3, 11), 100.0)  # expensive: 38.1, below the sell line of 50
    conviction, kind = run(raw_engine, date(2025, 3, 12))
    assert conviction == pytest.approx(38.1, abs=0.06) and kind == "sell"

    add_price(raw_engine, date(2025, 3, 13), 12.0)  # cheap: 92.8, back over the buy line of 70
    conviction, kind = run(raw_engine, date(2025, 3, 14))
    assert conviction == pytest.approx(92.8, abs=0.06) and kind == "buy"  # not "emerging" again

    with session_scope(raw_engine) as s:
        kinds = [x.kind for x in s.scalars(select(SignalRecord).order_by(SignalRecord.id))]
    assert kinds == ["emerging", "sell", "buy"]


def test_a_score_between_the_lines_signals_nothing(raw_engine, seed_company):
    seed_company(raw_engine, price=100.0)  # 38.1 and no prior view: no signal at all
    assert run(raw_engine)[1] is None
    with session_scope(raw_engine) as s:
        assert s.scalar(select(func.count()).select_from(SignalRecord)) == 0


def test_a_run_on_stale_data_is_stored_but_never_becomes_the_baseline(raw_engine, seed_company):
    """If the blocked run became the baseline, the buy would be swallowed when fresh data
    arrives: the stale run would already hold 92.8 and the fresh one would see no transition."""
    seed_company(raw_engine, price=100.0)  # 3/3: baseline 38.1
    run(raw_engine)
    add_price(raw_engine, date(2025, 3, 20), 12.0)  # the price became attractive on 3/20 ...
    conviction, kind = run(raw_engine, date(2025, 4, 10))  # ... but is 21 days old on 4/10
    assert conviction == pytest.approx(92.8, abs=0.06) and kind is None  # withheld, not emitted
    with session_scope(raw_engine) as s:
        blocked = s.scalars(select(ScoreRecord).order_by(ScoreRecord.id.desc())).first()
        assert blocked.signal_blocked is True
        assert any("price is stale" in b for b in blocked.details["signal_blockers"])
        assert "Signal withheld" in blocked.explanation

    add_price(raw_engine, date(2025, 4, 9), 12.0)  # fresh price, same value
    conviction, kind = run(raw_engine, date(2025, 4, 10))
    assert conviction == pytest.approx(92.8, abs=0.06) and kind == "buy"  # vs the 38.1 baseline


def test_a_null_conviction_is_not_a_baseline_so_the_next_score_is_emerging(
    raw_engine, seed_company
):
    sid = seed_company(raw_engine, price=25.0)
    with session_scope(raw_engine) as s:  # a run where no philosophy could be evaluated
        signals_repo.insert_score(
            s, security_id=sid, as_of=repo.latest_price(s, sid).ts, conviction=None,
            all_abstained=True, signal_blocked=False, explanation="none", details={},
        )  # fmt: skip
        assert signals_repo.latest_actionable_score(s, sid) is None
    assert run(raw_engine)[1] == "emerging"  # not "buy", and not read as a collapse from 0.0


def test_latest_actionable_score_skips_blocked_and_null_rows(raw_engine, seed_company):
    sid = seed_company(raw_engine, price=25.0)
    ts = date(2025, 3, 10)
    with session_scope(raw_engine) as s:
        base = dict(security_id=sid, as_of=repo.latest_price(s, sid).ts, details={}, explanation="")
        good = signals_repo.insert_score(
            s, conviction=61.0, all_abstained=False, signal_blocked=False, **base
        )
        signals_repo.insert_score(
            s, conviction=99.0, all_abstained=False, signal_blocked=True, **base
        )
        signals_repo.insert_score(
            s, conviction=None, all_abstained=True, signal_blocked=False, **base
        )
        assert signals_repo.latest_actionable_score(s, sid).id == good.id
        assert signals_repo.latest_score(s, sid).conviction is None  # newest of any kind
    assert ts  # (the date above only documents the as_of used elsewhere)


def test_score_universe_isolates_failures_and_reports_new_signals(
    raw_engine, seed_company, monkeypatch
):
    seed_company(raw_engine, "AAA", price=25.0)
    seed_company(raw_engine, "BBB", price=12.0)
    seed_company(raw_engine, "BAD", price=25.0)
    with session_scope(raw_engine) as s:
        repo.upsert_securities(s, [{"ticker": "SHY", "kind": "etf"}])

    import fin_core.signals.engine as engine_mod

    real = engine_mod.score_and_record

    def flaky(session, ticker, as_of, **kw):
        if ticker == "BAD":
            raise RuntimeError("boom")
        return real(session, ticker, as_of, **kw)

    monkeypatch.setattr(engine_mod, "score_and_record", flaky)
    summary = score_universe(raw_engine, AS_OF)
    assert summary.scored == 2 and set(summary.failed) == {"BAD"}
    assert "RuntimeError: boom" in summary.failed["BAD"]
    assert sorted((t, k) for _, t, k in summary.signals) == [
        ("AAA", "emerging"),
        ("BBB", "emerging"),
    ]
    only = score_universe(raw_engine, AS_OF, tickers=["aaa"])
    assert only.scored == 1 and only.signals == []  # idempotent: already in that state


def test_opportunities_rank_latest_actionable_scores(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)  # 78.5
    seed_company(raw_engine, "BBB", price=12.0)  # 92.8
    seed_company(raw_engine, "CCC", price=100.0)  # 38.1
    seed_company(raw_engine, "OLD", price=12.0, price_day=date(2025, 1, 2))  # stale at AS_OF
    score_universe(raw_engine, AS_OF)
    with session_scope(raw_engine) as s:
        ranked = [
            (sec.ticker, round(sc.conviction, 1)) for sec, sc in signals_repo.opportunities(s)
        ]
        assert ranked == [("BBB", 92.8), ("AAA", 78.5), ("CCC", 38.1)]  # OLD is blocked: absent
        assert [
            t for t, _ in [(x.ticker, 0) for x, _ in signals_repo.opportunities(s, limit=1)]
        ] == ["BBB"]


def test_watchlist_is_idempotent(raw_engine, seed_company):
    sid = seed_company(raw_engine, price=25.0)
    with session_scope(raw_engine) as s:
        assert signals_repo.watchlist_add(s, sid) is True
        assert signals_repo.watchlist_add(s, sid) is False
        assert signals_repo.is_watched(s, sid) and signals_repo.watchlist_tickers(s) == ["ACME"]
        assert signals_repo.watchlist_remove(s, sid) is True
        assert signals_repo.watchlist_remove(s, sid) is False
        assert not signals_repo.is_watched(s, sid)

"""Scheduled scoring: every run is stored, transitions raise signals, nothing signals twice."""

from __future__ import annotations

from datetime import date, timedelta

from scenario import AS_OF, NOW, World

from fin_application.scoring import baseline_from_history
from fin_domain.shared import Ticker

AAA = Ticker("AAA")
STALE = date(2025, 3, 20)  # 13 days after the newest seeded price


def kinds(world):
    return [s.signal.kind for _, s in sorted(world.store.signals.items())]


def test_the_first_high_score_is_emerging_and_everything_is_stored():
    w = World(conviction=80.0)
    w.seed("AAA")
    summary = w.score()
    assert summary.scored == 1 and [k for _, _, k in summary.signals] == ["emerging"]
    (entry,) = w.store.scores.values()
    assert (str(entry.ticker), entry.conviction, entry.signal_blocked) == ("AAA", 80.0, False)
    assert entry.computed_at == NOW and entry.as_of.date() == AS_OF
    assert list(entry.details.philosophies) == ["stub"]
    (signal,) = w.store.signals.values()
    assert signal.score_id == entry.id and signal.is_pending


def test_rerunning_with_unchanged_data_is_idempotent():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.score()
    again = w.score()
    assert again.signals == [] and len(w.store.scores) == 2 and len(w.store.signals) == 1


def test_transitions_emerging_then_sell_then_buy():
    w = World()
    w.seed("AAA")
    seen = []
    for conviction in (80.0, 40.0, 75.0):
        w.engine.conviction = conviction
        w.clock.advance(timedelta(minutes=1))
        w.score()
        seen = kinds(w)
    assert seen == ["emerging", "sell", "buy"]


def test_a_score_between_the_lines_signals_nothing():
    w = World(conviction=60.0)
    w.seed("AAA")
    w.score()
    w.engine.conviction = 65.0
    assert w.score().signals == []
    assert len(w.store.scores) == 2 and w.store.signals == {}


def test_a_run_on_stale_data_is_stored_but_never_becomes_the_baseline():
    w = World(conviction=60.0)
    w.seed("AAA")
    w.score()  # baseline 60
    w.engine.conviction = 80.0
    summary = w.score(as_of=STALE)  # the newest price is now 13 days old
    assert summary.signals == []  # withheld
    blocked = max(w.store.scores.values(), key=lambda s: s.id)
    assert (
        blocked.signal_blocked and blocked.conviction == 80.0 and "withheld" in blocked.explanation
    )
    with w.uow_factory() as uow:
        assert uow.scores.latest_actionable(AAA).conviction == 60.0  # not the blocked 80
    # fresh data arrives: the buy is NOT swallowed, because the baseline is still 60
    w.seed("AAA", price_date=date(2025, 3, 19))
    assert [k for _, _, k in w.score(as_of=STALE).signals] == ["buy"]


def test_a_null_conviction_is_not_a_baseline_so_the_next_score_is_emerging():
    w = World(conviction=None)
    w.seed("AAA")
    w.score()
    (entry,) = w.store.scores.values()
    assert entry.conviction is None and entry.all_abstained
    w.engine.conviction = 80.0
    assert [k for _, _, k in w.score().signals] == ["emerging"]  # not "buy"


def test_the_baseline_is_the_newest_actionable_score_skipping_blocked_and_null_rows():
    w = World()
    w.seed("AAA")
    for conviction in (55.0, None):
        w.engine.conviction = conviction
        w.clock.advance(timedelta(minutes=1))
        w.score()
    w.engine.conviction = 99.0
    w.clock.advance(timedelta(minutes=1))
    w.score(as_of=STALE)  # blocked: the price is too old by then
    with w.uow_factory() as uow:
        assert uow.scores.latest_actionable(AAA).conviction == 55.0
        assert uow.scores.latest(AAA).conviction == 99.0
        assert baseline_from_history(uow, AAA).previous_conviction == 55.0


def test_one_failing_security_neither_blocks_nor_rolls_back_the_others():
    w = World(conviction=80.0)
    for t in ("AAA", "BBB", "CCC"):
        w.seed(t)
    w.engine.raise_for = {"BBB"}
    summary = w.score()
    assert summary.scored == 2 and sorted(t for _, t, _ in summary.signals) == ["AAA", "CCC"]
    assert list(summary.failed) == ["BBB"] and "RuntimeError" in summary.failed["BBB"]
    assert len(w.store.scores) == 2  # the failed one left nothing behind


def test_only_active_stocks_are_scored_and_a_ticker_filter_narrows_it():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.seed("BBB")
    w.seed("SHY", kind="etf")  # no fundamentals: not scored
    w.seed("OLD", active=False)
    assert w.score().scored == 2
    assert w.score(tickers=["bbb"]).scored == 1


def test_a_score_flapping_around_the_buy_line_signals_once():
    w = World()
    w.seed("AAA")
    for conviction in (68.0, 71.0, 69.0, 71.0, 69.0, 72.0):
        w.engine.conviction = conviction
        w.clock.advance(timedelta(minutes=1))
        w.score()
    assert kinds(w) == ["buy"] or kinds(w) == ["emerging"]  # one signal, however it is labelled
    assert len(kinds(w)) == 1
    # it only re-arms once the score has fallen below 65, and the next crossing signals again
    for conviction in (60.0, 72.0):
        w.engine.conviction = conviction
        w.clock.advance(timedelta(minutes=1))
        w.score()
    assert kinds(w)[-1] == "buy" and len(kinds(w)) == 2


def test_a_score_flapping_around_the_sell_line_signals_once():
    w = World()
    w.seed("AAA")
    for conviction in (60.0, 49.0, 51.0, 49.0, 52.0, 48.0):
        w.engine.conviction = conviction
        w.clock.advance(timedelta(minutes=1))
        w.score()
    assert kinds(w) == ["sell"]
    for conviction in (57.0, 45.0):  # recovers to 55 or more, then falls again
        w.engine.conviction = conviction
        w.clock.advance(timedelta(minutes=1))
        w.score()
    assert kinds(w) == ["sell", "sell"]


def test_the_armed_state_follows_the_stored_history():
    w = World()
    w.seed("AAA")
    with w.uow_factory() as uow:
        assert baseline_from_history(uow, AAA).buy_armed  # no signal yet: armed
    w.engine.conviction = 80.0
    w.score()  # emerging
    with w.uow_factory() as uow:
        b = baseline_from_history(uow, AAA)
        assert (b.buy_armed, b.sell_armed, b.previous_conviction) == (False, True, 80.0)
    w.engine.conviction = 60.0
    w.clock.advance(timedelta(minutes=1))
    w.score()  # fell below 65: re-armed
    with w.uow_factory() as uow:
        assert baseline_from_history(uow, AAA).buy_armed

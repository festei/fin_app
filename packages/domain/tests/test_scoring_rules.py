"""Scoring and signal rules that need no data and no framework."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from fin_domain.market_data import CONCEPT_TAGS, Fact, PriceBar, Security
from fin_domain.scoring import (
    PHILOSOPHIES,
    VETO_CAP,
    Assessment,
    GuardrailVerdict,
    ScoreResult,
    SecurityMetrics,
    aggregate,
    assess,
    calculate_metrics,
    run_philosophy,
    validate_config,
)
from fin_domain.shared import InvariantViolation, Ticker
from fin_domain.signals import (
    BUY_REARM_BELOW,
    BUY_THRESHOLD,
    SELL_REARM_ABOVE,
    SELL_THRESHOLD,
    decide_signal,
)

NOW = datetime(2025, 3, 1, 12, tzinfo=UTC)


def result(name, score, evaluated=True):
    return ScoreResult(name, score, evaluated=evaluated)


def metrics(**kw):
    return SecurityMetrics(ticker="ACME", as_of=NOW, **kw)


# --- ScoreResult --------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [-0.1, 100.1])
def test_a_score_must_be_between_0_and_100(bad):
    with pytest.raises(InvariantViolation):
        ScoreResult("x", bad)


def test_an_integer_score_is_stored_as_a_float():
    assert isinstance(ScoreResult("x", 50).score, float)


# --- aggregation --------------------------------------------------------------------------------


def test_conviction_is_the_weighted_mean_of_evaluated_philosophies():
    agg = aggregate(
        {"a": result("a", 80), "b": result("b", 40)}, {"a": 3.0, "b": 1.0}, GuardrailVerdict()
    )
    assert agg.conviction == 70.0 and not agg.all_abstained
    assert "a 80" in agg.explanation and "b 40" in agg.explanation


def test_an_abstaining_philosophy_is_left_out_not_counted_as_zero():
    agg = aggregate(
        {"a": result("a", 80), "b": result("b", 0, evaluated=False)}, {}, GuardrailVerdict()
    )
    assert agg.conviction == 80.0
    assert "Not evaluated (insufficient data): b." in agg.explanation


def test_when_everything_abstains_there_is_no_conviction_not_zero():
    agg = aggregate({"a": result("a", 0, evaluated=False)}, {}, GuardrailVerdict())
    assert agg.conviction is None and agg.all_abstained
    assert aggregate({}, {}, GuardrailVerdict()).conviction is None


def test_a_guardrail_veto_caps_conviction_and_says_why():
    verdict = GuardrailVerdict(veto=True, reasons=["Altman Z'' 0.4 is in the distress zone"])
    agg = aggregate({"a": result("a", 95)}, {}, verdict)
    assert agg.conviction == VETO_CAP
    assert "Capped by guardrails" in agg.explanation


def test_a_veto_below_the_cap_changes_nothing_but_the_note():
    agg = aggregate({"a": result("a", 30)}, {}, GuardrailVerdict(veto=True, reasons=["weak"]))
    assert agg.conviction == 30.0


def test_guardrail_notes_are_reported_without_capping():
    agg = aggregate({"a": result("a", 90)}, {}, GuardrailVerdict(notes=["buyback-driven"]))
    assert agg.conviction == 90.0 and "Guardrail notes: buyback-driven" in agg.explanation


def test_a_vetoed_company_can_never_read_as_a_buy():
    assert VETO_CAP < SELL_THRESHOLD < BUY_THRESHOLD  # the two contexts agree on the lines


# --- the reference engine ---------------------------------------------------------------------


def test_assess_runs_every_scorer_and_the_guardrails():
    scorers = {"a": lambda m, cfg: result("a", cfg["target"]), "b": lambda m, cfg: result("b", 20)}
    config = {
        "a": {"target": 60},
        "b": {},
        "weights": {"a": 1, "b": 1},
        "guardrails": {"enabled": False},
    }
    out = assess(metrics(), config, scorers)
    assert out.conviction == 40.0 and set(out.philosophy_results) == {"a", "b"}
    assert out.guardrail_results == {} and not out.guardrail_reasons


def test_an_llm_backed_philosophy_is_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="Graham"):
        run_philosophy("graham", PHILOSOPHIES["graham"], metrics(), {"graham": {"impl": "llm"}})


def test_the_config_must_cover_exactly_the_registered_philosophies():
    full = {name: {} for name in PHILOSOPHIES} | {"weights": dict.fromkeys(PHILOSOPHIES, 1.0)}
    validate_config(full)
    with pytest.raises(InvariantViolation, match="weights"):
        validate_config({**full, "weights": {"graham": 1.0}})
    with pytest.raises(InvariantViolation, match="no thresholds"):
        validate_config({k: v for k, v in full.items() if k != "lynch"})


# --- the signal decision --------------------------------------------------------------------------


def assessment(conviction, *, blockers=(), abstained=False):
    return Assessment(
        metrics=metrics(signal_blockers=list(blockers)),
        conviction=conviction,
        all_abstained=abstained,
        explanation="why",
        philosophy_results={},
    )


def decide(conviction, previous, **kw):
    return decide_signal(
        assessment(conviction, **kw.pop("a", {})), previous_conviction=previous, **kw
    )


def test_the_first_real_score_above_the_line_is_emerging_not_buy():
    assert decide(80, None).signal.kind == "emerging"


def test_crossing_up_is_a_buy_and_staying_high_is_nothing():
    assert decide(75, 60).signal.kind == "buy"
    assert decide(75, 72).signal is None
    assert decide(69.9, 60).signal is None


def test_crossing_down_is_a_sell_and_staying_low_is_nothing():
    assert decide(40, 60).signal.kind == "sell"
    assert decide(40, 45).signal is None
    assert decide(40, None).signal is None  # no baseline: nothing to fall from


def test_hysteresis_blocks_a_second_signal_until_the_score_has_moved_away():
    assert decide(75, 60, buy_armed=False).signal is None
    assert decide(40, 60, sell_armed=False).signal is None
    assert BUY_REARM_BELOW < BUY_THRESHOLD and SELL_REARM_ABOVE > SELL_THRESHOLD


def test_a_signal_carries_its_reason_and_time():
    signal = decide(80, 60).signal
    assert (signal.ticker, signal.conviction, signal.as_of, signal.reason) == (
        "ACME",
        80,
        NOW,
        "why",
    )


def test_poor_data_withholds_the_signal_and_says_so():
    d = decide(80, 60, a={"blockers": ["price is stale: last bar 2025-01-01"]})
    assert d.signal is None
    assert d.explanation == "why Signal withheld: price is stale: last bar 2025-01-01."


def test_nothing_evaluated_is_no_view_and_never_a_signal():
    d = decide(None, 60, a={"abstained": True})
    assert d.signal is None and d.explanation == "why"


# --- metrics from facts and a price ---------------------------------------------------------------


def fact(concept, value, end, *, instant=False, filed=None):
    return Fact(
        concept=concept,
        tag=CONCEPT_TAGS[concept][0],
        unit="USD/shares" if concept == "EpsDiluted" else "USD",
        period_start=end if instant else date(end.year, 1, 1),
        period_end=end,
        instant=instant,
        fiscal_year=end.year,
        fiscal_period="FY",
        form="10-K",
        filed=filed or date(end.year + 1, 2, 15),
        accession=f"{concept}-{end}-{filed}",
        value=Decimal(str(value)),
    )


def bar(day, close=25.0):
    return PriceBar(ts=datetime.combine(day, datetime.min.time(), tzinfo=UTC), close=close)


ACME = Security(Ticker("ACME"), sector="Industrials")
FY24 = date(2024, 12, 31)


def test_a_price_and_one_year_of_eps_give_a_pe():
    m = calculate_metrics(
        ACME, [fact("EpsDiluted", 2.5, FY24)], bar(date(2025, 2, 28)), date(2025, 3, 1)
    )
    assert m.pe == 10.0 and m.price == 25.0 and m.sector == "Industrials"
    assert m.signal_blockers == []


def test_no_price_and_no_fundamentals_block_signals_and_are_named():
    m = calculate_metrics(ACME, [], None, date(2025, 3, 1))
    assert m.price is None and m.pe is None
    assert any("no price" in b for b in m.signal_blockers)
    assert any("no annual fundamentals" in b for b in m.signal_blockers)


def test_a_week_old_price_and_year_old_filings_are_stale():
    old_price = bar(date(2025, 3, 1) - timedelta(days=8))
    m = calculate_metrics(ACME, [fact("EpsDiluted", 2.5, FY24)], old_price, date(2025, 3, 1))
    assert any("price is stale" in b for b in m.signal_blockers)
    later = calculate_metrics(
        ACME, [fact("EpsDiluted", 2.5, FY24)], bar(date(2026, 8, 1)), date(2026, 8, 2)
    )
    assert any("fundamentals are stale" in b for b in later.signal_blockers)


def test_a_restatement_filed_after_as_of_is_not_used():
    original = fact("EpsDiluted", 2.0, FY24, filed=date(2025, 2, 15))
    restated = fact("EpsDiluted", 4.0, FY24, filed=date(2025, 9, 1))
    px = bar(date(2025, 2, 28))
    before = calculate_metrics(ACME, [original, restated], px, date(2025, 3, 1))
    assert before.eps == 2.0 and before.pe == 12.5
    after = calculate_metrics(ACME, [original, restated], bar(date(2025, 9, 20)), date(2025, 9, 21))
    assert after.eps == 4.0


def test_a_plain_date_means_the_end_of_that_day_in_utc():
    m = calculate_metrics(ACME, [], None, date(2025, 3, 1))
    assert m.as_of == datetime(2025, 3, 1, 23, 59, 59, tzinfo=UTC)


def test_multi_class_issuers_report_no_book_value():
    brk = Security(Ticker("BRK-B"))
    facts = [
        fact("StockholdersEquity", 1000, FY24, instant=True),
        Fact(**{**fact("SharesOutstanding", 100, date(2025, 2, 10), instant=True).__dict__,
                "unit": "shares", "tag": CONCEPT_TAGS["SharesOutstanding"][0]}),
    ]  # fmt: skip
    m = calculate_metrics(brk, facts, bar(date(2025, 2, 28)), date(2025, 3, 1))
    assert m.multi_class and m.book_value_per_share is None and m.pb is None

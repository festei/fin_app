from datetime import datetime

from fin_core.graph import build_graph, load_config
from fin_core.graph.nodes.graham import score_graham
from fin_core.models import SecurityMetrics

CFG = load_config()


def metrics(**kw) -> SecurityMetrics:
    base = dict(
        ticker="TEST",
        as_of=datetime(2025, 1, 1),
        pe=10.0,
        pb=1.2,
        current_ratio=2.5,
        debt_to_equity=0.4,
        dividend_years=20,
        earnings_growth=0.05,
    )
    base.update(kw)
    return SecurityMetrics(**base)


def test_graham_all_pass():
    r = score_graham(metrics(), CFG["graham"])
    assert r.score == 100.0
    assert not r.failed_criteria


def test_graham_just_inside_limits_passes_and_pe_over_limit_fails():
    # 14.9 * 1.49 = 22.2 < 22.5, and each ratio is under its own limit.
    r = score_graham(metrics(pe=14.9, pb=1.49), CFG["graham"])
    assert r.score == 100.0
    r = score_graham(metrics(pe=16.0), CFG["graham"])
    names = {c.name for c in r.failed_criteria}
    assert "P/E below limit" in names


def test_graham_missing_data_fails_criterion_not_crash():
    r = score_graham(metrics(pe=None), CFG["graham"])
    assert any(c.detail == "no data" for c in r.criteria)
    assert r.score < 100


def failed_names(r) -> set[str]:
    return {c.name for c in r.failed_criteria}


def test_graham_negative_pe_loss_maker_fails_pe_and_product():
    # Without a sign check, -5 < 15 and -5 * 1.2 < 22.5 would both "pass".
    r = score_graham(metrics(pe=-5.0), CFG["graham"])
    assert {"P/E below limit", "P/E x P/B below limit"} <= failed_names(r)
    assert r.score < 100


def test_graham_negative_pb_negative_equity_fails_pb_and_product():
    r = score_graham(metrics(pb=-2.0), CFG["graham"])
    assert {"P/B below limit", "P/E x P/B below limit"} <= failed_names(r)


def test_graham_two_negatives_do_not_multiply_into_a_pass():
    # (-5) * (-2) = +10 would pass the product limit if only the product were checked.
    r = score_graham(metrics(pe=-5.0, pb=-2.0), CFG["graham"])
    assert {"P/E below limit", "P/B below limit", "P/E x P/B below limit"} <= failed_names(r)


def test_graham_zero_pe_is_not_meaningful():
    assert "P/E below limit" in failed_names(score_graham(metrics(pe=0.0), CFG["graham"]))


def test_graham_negative_debt_to_equity_fails_but_zero_debt_passes():
    assert "Debt/equity below limit" in failed_names(
        score_graham(metrics(debt_to_equity=-0.5), CFG["graham"])
    )
    assert "Debt/equity below limit" not in failed_names(
        score_graham(metrics(debt_to_equity=0.0), CFG["graham"])
    )


def run(m, previous=None):
    return build_graph().invoke({"metrics": m, "config": CFG, "previous_conviction": previous})


def test_graph_emits_emerging_on_first_high_score():
    out = run(metrics())
    assert out["conviction"] == 100.0
    assert out["signal"].kind == "emerging"


def test_graph_signals_only_on_transition():
    assert run(metrics(), previous=100.0)["signal"] is None
    assert run(metrics(), previous=40.0)["signal"].kind == "buy"


def test_graph_sell_when_score_collapses():
    bad = metrics(
        pe=40, pb=5, current_ratio=0.5, debt_to_equity=3, dividend_years=0, earnings_growth=-0.2
    )
    out = run(bad, previous=90.0)
    assert out["signal"].kind == "sell"

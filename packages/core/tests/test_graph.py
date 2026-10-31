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

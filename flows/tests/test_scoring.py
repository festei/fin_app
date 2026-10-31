from datetime import datetime

from prefect.testing.utilities import prefect_test_harness

from fin_core.models import SecurityMetrics
from fin_flows.scoring import score_universe


def test_score_universe_runs_graph_per_security():
    m = SecurityMetrics(
        ticker="TEST",
        as_of=datetime(2025, 1, 1),
        pe=10,
        pb=1.2,
        current_ratio=2.5,
        debt_to_equity=0.4,
        dividend_years=20,
        earnings_growth=0.05,
    )
    with prefect_test_harness():
        out = score_universe([m])
    assert out[0]["ticker"] == "TEST"
    assert out[0]["conviction"] == 100.0

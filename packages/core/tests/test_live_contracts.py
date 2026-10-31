"""Opt-in checks of the REAL SEC / FRED response shapes (the offline tests use synthetic
fixtures). Skipped unless RUN_LIVE_TESTS=1.

    RUN_LIVE_TESTS=1 SEC_USER_AGENT="Your Name you@example.com" FRED_API_KEY=... \
        uv run pytest -m live -v
"""

import os

import pytest

from fin_core.providers.edgar import EdgarClient, normalize_facts
from fin_core.providers.fred import FredClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]


def test_live_edgar_ticker_map_and_company_facts():
    edgar = EdgarClient()  # needs SEC_USER_AGENT
    mapping = edgar.ticker_map()
    assert "AAPL" in mapping and len(mapping["AAPL"]["cik"]) == 10

    rows = normalize_facts(edgar.company_facts(mapping["AAPL"]["cik"]))
    concepts = {r["concept"] for r in rows}
    assert {"Revenue", "NetIncome", "Assets", "StockholdersEquity"} <= concepts
    assert all(r["filed"] and r["accession"] for r in rows)
    assert any(r["instant"] for r in rows) and any(not r["instant"] for r in rows)


@pytest.mark.skipif(not os.environ.get("FRED_API_KEY"), reason="needs FRED_API_KEY")
def test_live_fred_observations():
    pts = FredClient().observations("DGS10")
    assert len(pts) > 1000
    assert all(0 < float(v) < 25 for _, v in pts[-30:])

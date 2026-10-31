import logging

import httpx
import pandas as pd
import pytest

from fin_core.providers import alphavantage, yfinance_provider
from fin_core.providers.alphavantage import AlphaVantageProvider
from fin_core.providers.base import ProviderError, RateLimitError
from fin_core.providers.yfinance_provider import YFinancePriceProvider

KEY = "SECRET-KEY-123"


def av(handler) -> AlphaVantageProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AlphaVantageProvider(api_key=KEY, client=client)


def test_av_requires_api_key(monkeypatch):
    monkeypatch.delenv("FINANCE_API", raising=False)
    with pytest.raises(ProviderError, match="API key missing"):
        AlphaVantageProvider()


def test_av_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("FINANCE_API", "from-env")
    AlphaVantageProvider()  # does not raise


def test_av_success_keys_each_statement_by_its_function():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        fn = request.url.params["function"]
        seen.append(fn)
        return httpx.Response(200, json={"annualReports": [{"fn": fn, "year": "2024"}]})

    out = av(handler).statements("IBM")
    assert seen == ["INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"]
    # Regression: the old Kedro pipeline saved balance under cashflow and vice versa.
    assert out["income"]["fn"][0] == "INCOME_STATEMENT"
    assert out["balance"]["fn"][0] == "BALANCE_SHEET"
    assert out["cashflow"]["fn"][0] == "CASH_FLOW"
    assert (out["balance"]["ticker"] == "IBM").all()


@pytest.mark.parametrize("field", ["Note", "Information"])
def test_av_rate_limit_body_with_http_200_raises(field):
    provider = av(lambda r: httpx.Response(200, json={field: "call frequency exceeded"}))
    with pytest.raises(RateLimitError, match="call frequency exceeded"):
        provider.statements("IBM")


def test_av_error_message_and_missing_reports_raise():
    with pytest.raises(ProviderError, match="Invalid API call"):
        av(lambda r: httpx.Response(200, json={"Error Message": "Invalid API call"})).statements(
            "X"
        )
    with pytest.raises(ProviderError, match="no annualReports"):
        av(lambda r: httpx.Response(200, json={})).statements("X")


def test_av_http_error_does_not_leak_the_key():
    with pytest.raises(ProviderError) as exc:
        av(lambda r: httpx.Response(500, json={})).statements("IBM")
    assert "HTTP 500" in str(exc.value)
    assert KEY not in str(exc.value)
    assert KEY not in repr(exc.value.__cause__)
    assert exc.value.__suppress_context__  # `from None`: no chained exception holding the URL


def test_av_transport_error_does_not_leak_the_key():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("cannot connect", request=request)

    with pytest.raises(ProviderError) as exc:
        av(boom).statements("IBM")
    assert KEY not in str(exc.value)


def test_av_module_keeps_httpx_info_logging_off():
    assert alphavantage  # imported for its side effect
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


class FakeTicker:
    frame = pd.DataFrame()

    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, period="max"):
        return self.frame


def patch_ticker(monkeypatch, frame):
    FakeTicker.frame = frame
    monkeypatch.setattr(yfinance_provider.yf, "Ticker", FakeTicker)


def test_yf_history_normal_frame(monkeypatch):
    idx = pd.DatetimeIndex(["2025-01-02", "2025-01-03"], name="Date")
    frame = pd.DataFrame(
        {
            "Open": [1, 2],
            "High": [2, 3],
            "Low": [0.5, 1],
            "Close": [1.5, 2.5],
            "Volume": [10, 20],
            "Dividends": [0, 0],
        },
        index=idx,
    )
    patch_ticker(monkeypatch, frame)
    out = YFinancePriceProvider().history("IBM")
    assert list(out.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert len(out) == 2


def test_yf_history_empty_frame_returns_empty_with_columns_not_keyerror(monkeypatch):
    patch_ticker(monkeypatch, pd.DataFrame())
    out = YFinancePriceProvider().history("DELISTED")
    assert out.empty
    assert list(out.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]


def test_yf_last_price(monkeypatch):
    patch_ticker(monkeypatch, pd.DataFrame({"Close": [1.0, 2.5]}))
    assert YFinancePriceProvider().last_price("IBM") == 2.5
    patch_ticker(monkeypatch, pd.DataFrame())
    assert YFinancePriceProvider().last_price("IBM") is None

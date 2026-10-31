import logging
from datetime import date
from decimal import Decimal

import httpx
import pytest

from fin_core.providers.base import ProviderError, RateLimitError
from fin_core.providers.fred import FredClient

KEY = "FRED-SECRET-KEY"


def fred(handler) -> FredClient:
    return FredClient(api_key=KEY, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_requires_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="API key missing"):
        FredClient()


def test_observations_parse_skip_missing_and_send_expected_params(fixture_json):
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json=fixture_json("fred_dgs10_synthetic.json"))

    pts = fred(handler).observations("DGS10", start=date(2025, 1, 1))
    assert pts == [
        (date(2025, 1, 2), Decimal("4.57")),
        (date(2025, 1, 3), Decimal("4.60")),
        (date(2025, 1, 6), Decimal("4.62")),
    ]  # the "." observation is skipped
    assert seen["series_id"] == "DGS10"
    assert seen["file_type"] == "json"
    assert seen["observation_start"] == "2025-01-01"


def test_no_start_param_when_not_given():
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"observations": []})

    assert fred(handler).observations("DGS10") == []
    assert "observation_start" not in seen


def test_bad_series_error_message_surfaces_without_key():
    body = {"error_code": 400, "error_message": "Bad Request.  The series does not exist."}
    with pytest.raises(ProviderError, match="series does not exist") as exc:
        fred(lambda r: httpx.Response(400, json=body)).observations("NOPE")
    assert KEY not in str(exc.value)


def test_rate_limit_and_unexpected_shape():
    with pytest.raises(RateLimitError):
        fred(lambda r: httpx.Response(429)).observations("DGS10")
    with pytest.raises(ProviderError, match="shape"):
        fred(lambda r: httpx.Response(200, json={"oops": 1})).observations("DGS10")


def test_transport_error_does_not_leak_key():
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(ProviderError) as exc:
        fred(boom).observations("DGS10")
    assert KEY not in str(exc.value)


def test_httpx_info_logging_stays_off():
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING

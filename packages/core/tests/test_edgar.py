import httpx
import pytest

from fin_core.providers.base import AccessDeniedError, ProviderError, RateLimitError
from fin_core.providers.edgar import EdgarClient, normalize_facts, pad_cik

UA = "fin-app tests tester@example.com"


def edgar(handler) -> EdgarClient:
    return EdgarClient(
        user_agent=UA, client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0
    )


def test_requires_user_agent_with_contact(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(ProviderError, match="User-Agent"):
        EdgarClient()
    with pytest.raises(ProviderError, match="User-Agent"):
        EdgarClient(user_agent="no contact info")


def test_pad_cik():
    assert pad_cik(320193) == "0000320193"
    assert pad_cik("0000320193") == "0000320193"


def test_ticker_map_pads_uppercases_and_skips_malformed_rows(fixture_json):
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["User-Agent"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json=fixture_json("company_tickers_synthetic.json"))

    m = edgar(handler).ticker_map()
    assert seen["ua"] == UA
    assert seen["url"] == "https://www.sec.gov/files/company_tickers.json"
    assert m["TEST"] == {"cik": "0001000001", "name": "Test Corp"}
    assert "BRK-B" in m  # upper-cased
    assert "BAD" not in m and "NOCIK" not in m


def test_company_facts_url_uses_padded_cik(fixture_json):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=fixture_json("companyfacts_synthetic.json"))

    edgar(handler).company_facts(1000001)
    assert seen["url"] == "https://data.sec.gov/api/xbrl/companyfacts/CIK0001000001.json"


@pytest.mark.parametrize(
    "status, exc",
    [(404, ProviderError), (429, RateLimitError), (500, ProviderError)],
)
def test_http_errors_map_to_provider_errors(status, exc):
    with pytest.raises(exc):
        edgar(lambda r: httpx.Response(status)).company_facts(1)


def test_403_for_rate_limit_and_403_for_bad_user_agent_are_told_apart():
    rate = "<html><body>Request Rate Threshold Exceeded</body></html>"
    agent = "<html><body>Your Request Originates from an Undeclared Automated Tool</body></html>"
    with pytest.raises(RateLimitError, match="rate threshold"):
        edgar(lambda r: httpx.Response(403, text=rate)).company_facts(1)
    with pytest.raises(AccessDeniedError, match="SEC_USER_AGENT") as exc:
        edgar(lambda r: httpx.Response(403, text=agent)).company_facts(1)
    assert not isinstance(exc.value, RateLimitError)  # configuration problem, not throttling


def test_unexpected_shape_and_non_json():
    with pytest.raises(ProviderError, match="shape"):
        edgar(lambda r: httpx.Response(200, json={"nope": 1})).company_facts(1)
    with pytest.raises(ProviderError, match="not JSON"):
        edgar(lambda r: httpx.Response(200, text="<html>")).company_facts(1)


def test_normalize_maps_filters_and_skips(fixture_json):
    rows = normalize_facts(fixture_json("companyfacts_synthetic.json"))
    by = lambda tag: [r for r in rows if r["tag"] == tag]  # noqa: E731

    # unmapped tag, 8-K fact and the two malformed entries are dropped
    assert not by("us-gaap/SomethingWeCannotMap")
    revenue = by("us-gaap/Revenues")
    assert sorted(r["accession"] for r in revenue) == [
        "0001-24-000001",
        "0001-24-000009",
        "0001-25-000002",
    ]
    assert all(r["form"] != "8-K" for r in rows)

    # both revenue tags land on the same normalized concept
    assert {r["concept"] for r in rows if "Revenue" in r["concept"]} == {"Revenue"}
    assert (
        by("us-gaap/RevenueFromContractWithCustomerExcludingAssessedTax")[0]["concept"] == "Revenue"
    )

    # instant vs duration
    assets = by("us-gaap/Assets")[0]
    assert assets["instant"] is True and assets["period_start"] == assets["period_end"]
    assert revenue[0]["instant"] is False
    assert str(revenue[0]["period_start"]) == "2023-01-01"

    # units and taxonomies are preserved
    assert by("us-gaap/EarningsPerShareDiluted")[0]["unit"] == "USD/shares"
    assert by("dei/EntityCommonStockSharesOutstanding")[0]["concept"] == "SharesOutstanding"


def test_normalize_keeps_every_filed_version_for_point_in_time(fixture_json):
    rows = normalize_facts(fixture_json("companyfacts_synthetic.json"))
    versions = {
        (str(r["filed"]), r["value"])
        for r in rows
        if r["tag"] == "us-gaap/Revenues" and str(r["period_end"]) == "2023-12-31"
    }
    assert {v for _, v in versions} == {1000, 900}
    assert len(versions) == 3


def test_normalize_collapses_exact_duplicates(fixture_json):
    payload = fixture_json("companyfacts_synthetic.json")
    entries = payload["facts"]["us-gaap"]["Assets"]["units"]["USD"]
    entries.append(dict(entries[0]))
    assets = [r for r in normalize_facts(payload) if r["tag"] == "us-gaap/Assets"]
    assert len(assets) == 1


def test_conflicting_values_for_the_same_key_are_dropped_not_overwritten():
    """A multi-class issuer can report one number per class under the same tag/period/filing;
    the payload has no class dimension, so keeping the last one would silently pick a class."""
    entry = lambda val, accn="0001-25-000001": {  # noqa: E731
        "end": "2024-12-31",
        "start": "2024-01-01",
        "val": val,
        "accn": accn,
        "fy": 2024,
        "fp": "FY",
        "form": "10-K",
        "filed": "2025-02-15",
    }
    payload = {
        "facts": {
            "us-gaap": {
                "EarningsPerShareDiluted": {
                    "units": {"USD/shares": [entry(5.0), entry(0.003)]}
                },  # A vs B
                "Revenues": {
                    "units": {"USD": [entry(100), entry(100)]}
                },  # exact duplicate: kept once
                "NetIncomeLoss": {
                    "units": {"USD": [entry(7, "a-1"), entry(8, "a-2")]}
                },  # distinct filings
            }
        }
    }
    rows = normalize_facts(payload)
    tags = [r["tag"] for r in rows]
    assert "us-gaap/EarningsPerShareDiluted" not in tags  # ambiguous: dropped entirely
    assert tags.count("us-gaap/Revenues") == 1
    assert tags.count("us-gaap/NetIncomeLoss") == 2  # different accessions are not a conflict

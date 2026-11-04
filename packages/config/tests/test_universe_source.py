import pytest

from fin_config import YamlUniverseSource
from fin_domain.shared import InvariantViolation, Ticker


def test_the_default_universe_is_valid_and_has_stocks_and_bond_etfs():
    updates = YamlUniverseSource().securities()
    assert {u.changes.get("kind", "stock") for u in updates} == {"stock", "etf"}
    assert len({u.ticker for u in updates}) == len(updates)
    assert Ticker("TLT") in {u.ticker for u in updates}


@pytest.mark.parametrize(
    ("yaml_text", "msg"),
    [
        ("securities:\n  - {ticker: A}\n  - {ticker: a}\n", "duplicate"),
        ("securities:\n  - {ticker: A, kind: bond}\n", "kind must be"),
        ("securities:\n  - {ticker: A, colour: red}\n", "unknown universe fields"),
    ],
)
def test_an_invalid_universe_is_rejected(tmp_path, yaml_text, msg):
    path = tmp_path / "u.yml"
    path.write_text(yaml_text)
    with pytest.raises(ValueError, match=msg):  # InvariantViolation is a ValueError
        YamlUniverseSource(universe_path=path).securities()


def test_the_bond_series_config_is_valid():
    series = YamlUniverseSource().bond_series()
    assert {"DGS2", "DGS10", "T10Y2Y", "BAMLC0A0CM", "BAMLH0A0HYM2"} <= {s.id for s in series}
    assert {s.kind for s in series} == {"yield", "spread"}


def test_bad_bond_series_config_is_rejected(tmp_path):
    path = tmp_path / "b.yml"
    path.write_text("series:\n  - {id: A, kind: yield}\n  - {id: A, kind: yield}\n")
    with pytest.raises(InvariantViolation, match="duplicate"):
        YamlUniverseSource(bond_series_path=path).bond_series()
    path.write_text("series:\n  - {id: A, kind: price}\n")
    with pytest.raises(InvariantViolation, match="kind must be"):
        YamlUniverseSource(bond_series_path=path).bond_series()

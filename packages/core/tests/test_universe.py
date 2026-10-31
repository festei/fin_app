import pytest

from fin_core.db import migrate, repo
from fin_core.db.session import make_engine, session_scope
from fin_core.universe import load_bond_series, load_universe, sync_universe


def test_default_universe_is_valid_and_has_stocks_and_bond_etfs():
    rows = load_universe()
    kinds = {r.get("kind", "stock") for r in rows}
    assert kinds == {"stock", "etf"}
    assert len({r["ticker"] for r in rows}) == len(rows)


def test_sync_is_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path / 'u.db'}"
    migrate.upgrade(url)
    engine = make_engine(url)
    with session_scope(engine) as s:
        n = sync_universe(s)
        sync_universe(s)
    with session_scope(engine) as s:
        assert len(repo.list_securities(s)) == n
        assert repo.get_security(s, "tlt").kind == "etf"


@pytest.mark.parametrize(
    "yaml_text, msg",
    [
        ("securities:\n  - {ticker: A}\n  - {ticker: a}\n", "duplicate"),
        ("securities:\n  - {ticker: A, kind: bond}\n", "kind must be"),
        ("securities:\n  - {ticker: A, colour: red}\n", "unknown universe fields"),
    ],
)
def test_invalid_universe_rejected(tmp_path, yaml_text, msg):
    p = tmp_path / "u.yml"
    p.write_text(yaml_text)
    with pytest.raises(ValueError, match=msg):
        load_universe(p)


def test_bond_series_config_is_valid():
    series = load_bond_series()
    ids = {x["id"] for x in series}
    assert {"DGS2", "DGS10", "T10Y2Y", "BAMLC0A0CM", "BAMLH0A0HYM2"} <= ids
    assert {x["kind"] for x in series} == {"yield", "spread"}


def test_bond_series_rejects_bad_config(tmp_path):
    p = tmp_path / "b.yml"
    p.write_text("series:\n  - {id: A, kind: yield}\n  - {id: A, kind: yield}\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_bond_series(p)
    p.write_text("series:\n  - {id: A, kind: price}\n")
    with pytest.raises(ValueError, match="kind must be"):
        load_bond_series(p)


def test_sync_does_not_wipe_a_resolved_cik(tmp_path):
    url = f"sqlite:///{tmp_path / 'cik.db'}"
    migrate.upgrade(url)
    engine = make_engine(url)
    with session_scope(engine) as s:
        sync_universe(s)
        repo.upsert_securities(s, [{"ticker": "IBM", "cik": "0000051143"}])  # as EDGAR resolves it
    with session_scope(engine) as s:
        sync_universe(s)  # the daily sync: the YAML has no cik
    with session_scope(engine) as s:
        assert repo.get_security(s, "IBM").cik == "0000051143"

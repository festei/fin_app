import pytest

from fin_core.db import migrate, repo
from fin_core.db.session import make_engine, session_scope
from fin_core.universe import load_universe, sync_universe


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

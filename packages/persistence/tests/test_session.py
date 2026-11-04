from fin_persistence import session as session_mod


def test_make_engine_sets_a_connect_timeout_for_server_databases(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        session_mod, "create_engine", lambda url, **kw: captured.update(kw) or object()
    )
    session_mod.make_engine("postgresql+psycopg://u:p@db:5432/x", connect_timeout=3)
    assert captured["connect_args"] == {"connect_timeout": 3}
    session_mod.make_engine("sqlite:///x.db")
    assert captured["connect_args"] == {}


def test_postgres_url_quotes_credentials():
    url = session_mod.postgres_url("u@x", "db", "p/w d", "h", "6543")
    assert url == "postgresql+psycopg://u%40x:p%2Fw%20d@h:6543/db"

"""The HTTP contract for storage failures: an outage is 503 with only the kind in the body; any
other database error is a plain 500 that tells the caller nothing about the database."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import fin_api.main as main
from fin_application.errors import StorageError, StorageUnavailable
from fin_application.sandbox import PortfolioReader
from fin_persistence import migrate

# the server error must come back as a response, not be re-raised into the test
client = TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def db(monkeypatch, tmp_path):
    for key in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(key, raising=False)
    url = f"sqlite:///{tmp_path / 'map.db'}"
    migrate.upgrade(url)
    monkeypatch.setenv("DATABASE_URL", url)
    yield
    main.dispose_engines()


def failing(error: Exception):
    def boom(self):
        raise error

    return boom


def test_an_outage_is_503_with_only_the_kind_in_the_body(db, monkeypatch):
    monkeypatch.setattr(
        PortfolioReader,
        "list",
        failing(StorageUnavailable("database unavailable (OperationalError)")),
    )
    response = client.get("/api/portfolios")
    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable (OperationalError)"}


def test_any_other_database_error_is_a_500_with_no_detail_in_the_body(db, monkeypatch):
    secret = "IntegrityError: duplicate key value violates unique constraint ix_secret_name"
    monkeypatch.setattr(PortfolioReader, "list", failing(StorageError(secret)))
    response = client.get("/api/portfolios")
    assert response.status_code == 500
    assert "constraint" not in response.text and "ix_secret_name" not in response.text
    assert "IntegrityError" not in response.text


def test_readyz_reports_an_unreachable_database_as_503_not_500(monkeypatch, tmp_path):
    for key in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'missing-dir' / 'x.db'}")
    try:
        response = client.get("/readyz")
    finally:
        main.dispose_engines()
    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable (OperationalError)"}

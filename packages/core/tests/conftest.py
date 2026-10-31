import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from fin_core.db import migrate
from fin_core.db.session import make_engine

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_json():
    def load(name: str):
        return json.loads((FIXTURES / name).read_text())

    return load


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def raw_engine(request, tmp_path):
    """A migrated, empty database. Always SQLite; also real Postgres when TEST_DATABASE_URL
    is set (e.g. postgresql+psycopg://user:pw@localhost:5432/fin_test, UTF-8 encoded)."""
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'test.db'}"
    else:
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            pytest.skip("TEST_DATABASE_URL not set")
    eng = make_engine(url)
    if request.param == "postgres":  # start from an empty schema
        with eng.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    migrate.upgrade(url)
    yield eng
    eng.dispose()

"""``fin_bootstrap.Settings`` must read the database configuration exactly like the legacy
``fin_core.db.session`` functions it replaces. Delete this test together with those functions."""

import pytest

from fin_bootstrap import Settings
from fin_core.db.session import database_configured, database_url

ENV_KEYS = [
    "DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
]

CASES = [
    {},
    {"DATABASE_URL": "sqlite:///x.db"},
    {"DATABASE_URL": "sqlite:///x.db", "POSTGRES_USER": "u", "POSTGRES_DB": "d"},
    {"POSTGRES_USER": "u", "POSTGRES_DB": "d"},
    {"POSTGRES_USER": "u"},
    {"POSTGRES_DB": "d"},
    {
        "POSTGRES_USER": "u@x",
        "POSTGRES_DB": "d",
        "POSTGRES_PASSWORD": "p/w d",
        "POSTGRES_HOST": "h",
    },
    {"POSTGRES_USER": "u", "POSTGRES_DB": "d", "POSTGRES_PORT": "6543"},
]


@pytest.mark.parametrize("env", CASES)
def test_settings_match_the_legacy_database_lookup(env, monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings.from_environ(env)
    assert settings.database_configured == database_configured()
    if database_configured():
        assert settings.database_url == database_url()

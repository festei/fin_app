from pathlib import Path

import pytest

from fin_config import CONFIG_DIR_ENV, ConfigLocator, FeatureFlags


def test_the_default_search_finds_the_repositorys_config():
    found = ConfigLocator(environ={}).path("philosophies.yml")
    assert found.name == "philosophies.yml" and found.parent.name == "config"


@pytest.mark.parametrize(
    "name",
    [
        "philosophies.yml",
        "alerts.yml",
        "sandbox.yml",
        "features.yml",
        "universe.yml",
        "bond_series.yml",
    ],
)
def test_every_config_file_the_application_reads_is_found(name):
    assert ConfigLocator(environ={}).find(name) is not None


def test_an_explicit_directory_wins_over_everything(tmp_path):
    (tmp_path / "alerts.yml").write_text("alerts: {}")
    other = tmp_path / "other"
    other.mkdir()
    (other / "alerts.yml").write_text("alerts: {}")
    locator = ConfigLocator(tmp_path, environ={CONFIG_DIR_ENV: str(other)})
    assert locator.path("alerts.yml") == tmp_path / "alerts.yml"


def test_the_environment_directory_wins_over_the_search(tmp_path):
    (tmp_path / "alerts.yml").write_text("alerts: {}")
    locator = ConfigLocator(environ={CONFIG_DIR_ENV: str(tmp_path)})
    assert locator.path("alerts.yml") == tmp_path / "alerts.yml"


def test_a_configured_directory_is_authoritative_even_when_the_file_is_missing(tmp_path):
    # no silent fall-back to the checkout's config: that would hide a wrong FIN_CONFIG_DIR
    assert ConfigLocator(environ={CONFIG_DIR_ENV: str(tmp_path)}).find("alerts.yml") is None
    with pytest.raises(FileNotFoundError):
        ConfigLocator(environ={CONFIG_DIR_ENV: str(tmp_path)}).path("alerts.yml")


def test_a_search_that_finds_nothing_returns_none(tmp_path):
    locator = ConfigLocator(search_from=tmp_path / "a" / "b" / "c.py", environ={})
    assert locator.find("does-not-exist.yml") is None


def test_the_image_layout_is_found(tmp_path):
    """Docker puts config/ next to the virtualenv, far above the installed package."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "alerts.yml").write_text("alerts: {}")
    deep = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "fin_config" / "x.py"
    assert ConfigLocator(search_from=deep, environ={}).find("alerts.yml") == (
        tmp_path / "config" / "alerts.yml"
    )


def test_feature_flags_follow_the_config_directory(tmp_path, monkeypatch):
    (tmp_path / "features.yml").write_text("features:\n  telegram_enabled: true\n")
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.delenv("FEATURES_FILE", raising=False)
    monkeypatch.delenv("FEATURE_TELEGRAM_ENABLED", raising=False)
    assert FeatureFlags.load().is_enabled("telegram_enabled") is True
    assert isinstance(Path(tmp_path), Path)

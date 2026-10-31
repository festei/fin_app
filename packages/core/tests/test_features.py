from fin_core.features import FeatureFlags


def test_telegram_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FEATURE_TELEGRAM_ENABLED", raising=False)
    assert FeatureFlags.load().is_enabled("telegram_enabled") is False


def test_env_overrides_file(monkeypatch):
    monkeypatch.setenv("FEATURE_TELEGRAM_ENABLED", "true")
    assert FeatureFlags.load().is_enabled("telegram_enabled") is True
    monkeypatch.setenv("FEATURE_TELEGRAM_ENABLED", "false")
    assert FeatureFlags.load().is_enabled("telegram_enabled") is False


def test_unknown_flag_is_off():
    assert FeatureFlags({}).is_enabled("whatever") is False

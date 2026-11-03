import pytest

from fin_config import ConfigLocator, YamlAlertPolicy, load_alert_policy
from fin_domain.shared import InvariantViolation
from fin_domain.signals import AlertPolicy


def test_the_shipped_policy_matches_the_documented_defaults():
    policy = load_alert_policy()
    assert (policy.cooldown_hours, policy.material_change) == (24, 10)
    assert policy.sell_only_for_watchlist is True and policy.max_delivery_attempts == 6
    assert policy.retry_backoff_minutes == (2, 10, 30, 120, 360)
    assert policy == AlertPolicy()  # config/alerts.yml and the domain defaults agree


def test_the_source_loads_through_the_locator(tmp_path):
    (tmp_path / "alerts.yml").write_text(
        "alerts:\n  cooldown_hours: 1\n  retry_backoff_minutes: [1, 2]\n"
    )
    policy = YamlAlertPolicy(ConfigLocator(tmp_path)).load()
    assert policy.cooldown_hours == 1 and policy.retry_backoff_minutes == (1, 2)


def test_unknown_policy_fields_are_rejected(tmp_path):
    bad = tmp_path / "alerts.yml"
    bad.write_text("alerts:\n  cooldown_hours: 1\n  colour: red\n")
    with pytest.raises(ValueError, match="unknown alert policy fields"):  # a ValueError subclass
        load_alert_policy(bad)
    with pytest.raises(InvariantViolation):
        load_alert_policy(bad)

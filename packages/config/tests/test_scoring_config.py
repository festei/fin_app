import pytest

from fin_config import ConfigLocator, YamlScoringConfig, load_scoring_config
from fin_domain.scoring import PHILOSOPHIES
from fin_domain.shared import InvariantViolation


def test_the_shipped_thresholds_cover_every_philosophy_and_guardrail():
    config = load_scoring_config()
    assert set(config["weights"]) == set(PHILOSOPHIES)
    assert {"piotroski", "altman"} <= set(config["guardrails"])


def test_the_config_source_loads_through_the_locator(tmp_path):
    shipped = ConfigLocator().path("philosophies.yml")
    (tmp_path / "philosophies.yml").write_text(shipped.read_text())
    source = YamlScoringConfig(ConfigLocator(tmp_path))
    assert source.load()["weights"] == load_scoring_config()["weights"]


def test_a_config_that_disagrees_with_the_registry_is_rejected_at_load_time(tmp_path):
    path = tmp_path / "philosophies.yml"
    path.write_text("weights:\n  graham: 1.0\ngraham: {}\n")
    with pytest.raises(InvariantViolation, match="do not match"):
        load_scoring_config(path)

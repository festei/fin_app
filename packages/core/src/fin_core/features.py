"""Feature flags moved to ``fin_config``; this keeps the old import path working."""

from fin_config.features import FeatureFlags

__all__ = ["FeatureFlags"]

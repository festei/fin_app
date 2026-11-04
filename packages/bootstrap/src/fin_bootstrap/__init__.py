"""Composition root: reads settings and wires adapters into the use cases."""

from fin_bootstrap.container import Container, DatabaseNotConfigured
from fin_bootstrap.settings import Settings
from fin_config import FeatureFlags  # apps get the flags through the composition root

__all__ = ["Container", "DatabaseNotConfigured", "FeatureFlags", "Settings"]

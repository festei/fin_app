"""Errors of the application layer. Domain errors (``fin_domain.shared``) pass through unchanged."""

from __future__ import annotations


class ApplicationError(Exception):
    """Base class for errors raised by use cases."""


class InvalidCommand(ApplicationError, ValueError):
    """The caller asked for something that makes no sense (bad input, unknown option)."""


class ConcurrencyConflict(ApplicationError):
    """Someone else changed the thing since it was loaded; reload and decide again."""

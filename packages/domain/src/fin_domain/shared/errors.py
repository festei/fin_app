"""Errors the domain raises. Adapters translate them (HTTP status, log line, exit code)."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every error raised by the domain."""


class InvariantViolation(DomainError, ValueError):
    """A rule of the domain would be broken (bad value, illegal state transition)."""


class NotFound(DomainError, LookupError):
    """A thing the caller named does not exist."""

    def __init__(self, entity: str, key: object):
        self.entity = entity
        self.key = key
        super().__init__(f"{entity} {key!r} not found")

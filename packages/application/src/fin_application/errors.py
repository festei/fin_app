"""Errors of the application layer. Domain errors (``fin_domain.shared``) pass through unchanged."""

from __future__ import annotations


class ApplicationError(Exception):
    """Base class for errors raised by use cases."""


class InvalidCommand(ApplicationError, ValueError):
    """The caller asked for something that makes no sense (bad input, unknown option)."""


class ConcurrencyConflict(ApplicationError):
    """Someone else changed the thing since it was loaded; reload and decide again."""


class ProviderError(ApplicationError, RuntimeError):
    """A data provider failed or returned something unusable. Never contains credentials."""


class RateLimitError(ProviderError):
    """The provider throttled us (often reported as HTTP 200 with a note in the body)."""


class AccessDeniedError(ProviderError):
    """The provider refused us (e.g. EDGAR 403 for a missing/invalid User-Agent). Retrying
    cannot help until the configuration is fixed, so ingestion aborts the run."""


class ChannelError(ApplicationError, RuntimeError):
    """Delivery through a channel failed. Never contains credentials (tokens, URLs with keys)."""


class ChannelConfigError(ApplicationError, RuntimeError):
    """A channel is switched on but not configured. Raised at startup, not at first alert."""


class InstanceAlreadyRunning(ApplicationError, RuntimeError):
    """Another instance holds the single-instance guard."""


class StorageUnavailable(ApplicationError, RuntimeError):
    """The database could not be reached (connection refused, timeout, broken connection). Only
    the kind of error is in the message (``database unavailable (OperationalError)``): the
    original can hold connection details, and the adapter logs it in full."""


class StorageError(ApplicationError, RuntimeError):
    """The database was reached but an operation failed: a constraint violation, a missing table
    (migration not applied), bad data. This is a bug or a deployment problem, not an outage, so
    callers must not treat it as one (the API answers 500, not 503). The message is the error's
    kind plus the driver's first line (``IntegrityError: UNIQUE constraint failed: ...``), never
    the SQL statement or its parameters; the original is chained and logged."""

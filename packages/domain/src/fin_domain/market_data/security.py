"""A tradable instrument the application tracks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fin_domain.shared import InvariantViolation, Ticker


class SecurityKind(StrEnum):
    STOCK = "stock"
    ETF = "etf"


# The columns an update may carry besides the ticker.
UPDATABLE = ("cik", "name", "kind", "sector", "active")


@dataclass(frozen=True)
class SecurityUpdate:
    """The fields a source (the universe file, the SEC) provides for one security.

    Only what is listed in ``changes`` is touched on an existing security: a field that is absent
    is left alone, so syncing the universe (which has no ``cik``) never wipes a CIK that was
    resolved from the SEC. Passing a field explicitly, even as ``None``, does overwrite it.
    """

    ticker: Ticker
    changes: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        unknown = set(self.changes) - set(UPDATABLE)
        if unknown:
            raise InvariantViolation(f"{self.ticker}: unknown fields {sorted(unknown)}")
        if "kind" in self.changes:
            object.__setattr__(
                self, "changes", {**self.changes, "kind": _kind(self.ticker, self.changes["kind"])}
            )


def _kind(ticker: Ticker, value: object) -> SecurityKind:
    try:
        return SecurityKind(value)
    except ValueError:
        raise InvariantViolation(
            f"{ticker}: kind must be one of {sorted(k.value for k in SecurityKind)}"
        ) from None


@dataclass
class Security:
    ticker: Ticker
    kind: SecurityKind = SecurityKind.STOCK
    active: bool = True
    name: str | None = None
    cik: str | None = None
    sector: str | None = None

    @property
    def has_fundamentals(self) -> bool:
        """ETFs file no financial statements."""
        return self.kind is SecurityKind.STOCK

    def resolve_identity(self, cik: str, name: str) -> None:
        """Take the SEC's CIK; the SEC's name only fills a gap, it never replaces ours."""
        self.cik = cik
        self.name = self.name or name

    def merged_with(self, update: SecurityUpdate) -> Security:
        """A copy with the provided fields replaced."""
        if update.ticker != self.ticker:
            raise InvariantViolation(f"cannot merge {update.ticker} into {self.ticker}")
        values = {**self.__dict__, **update.changes}
        return Security(**values)

    @classmethod
    def new(cls, update: SecurityUpdate) -> Security:
        return cls(update.ticker).merged_with(update)

"""The identity every bounded context uses to refer to a security."""

from __future__ import annotations

import re
from dataclasses import dataclass

from fin_domain.shared.errors import InvariantViolation

# Letters and digits, with "." and "-" inside (BRK-B, BF.B); at most 16 characters (the column).
_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,15}")


@dataclass(frozen=True, order=True)
class Ticker:
    """A ticker symbol in upper case. Contexts hold a ``Ticker``, never a database id."""

    value: str

    def __post_init__(self) -> None:
        normalised = self.value.strip().upper()
        if not _PATTERN.fullmatch(normalised):
            raise InvariantViolation(f"not a valid ticker: {self.value!r}")
        object.__setattr__(self, "value", normalised)

    def __str__(self) -> str:
        return self.value

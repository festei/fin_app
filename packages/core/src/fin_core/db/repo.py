"""The repository functions moved to ``fin_persistence.repo``; this keeps the old import path
working for the modules that still use it (metrics, signals, sandbox, alerts, the HTTP app). It
goes away with the last of them."""

from fin_persistence.repo import (
    facts_as_of,
    get_security,
    latest_price,
    latest_yield,
    list_securities,
    upsert_facts,
    upsert_prices,
    upsert_securities,
    upsert_yields,
)

__all__ = [
    "facts_as_of",
    "get_security",
    "latest_price",
    "latest_yield",
    "list_securities",
    "upsert_facts",
    "upsert_prices",
    "upsert_securities",
    "upsert_yields",
]

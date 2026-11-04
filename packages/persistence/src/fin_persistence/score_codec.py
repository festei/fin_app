"""``scores.details`` <-> ``ScoreDetails``.

The stored JSON keeps exactly the shape it has always had (there is no data migration), so rows
written before this codec existed read back correctly, and so do partial ones. Reading is
tolerant: a missing key means "nothing recorded".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import date
from typing import Any

from fin_domain.scoring import Criterion, ScoreResult
from fin_domain.signals import MetricsSummary, ScoreDetails


def _day(value: Any) -> date | None:
    return date.fromisoformat(value) if value else None


def _result(name: str, raw: Mapping[str, Any]) -> ScoreResult:
    return ScoreResult(
        philosophy=raw.get("philosophy", name),
        score=raw.get("score", 0.0),
        criteria=[Criterion(**c) for c in raw.get("criteria") or []],
        explanation=raw.get("explanation", ""),
        evidence=list(raw.get("evidence") or []),
        impl=raw.get("impl", "deterministic"),
        evaluated=raw.get("evaluated", True),
    )


def details_to_json(d: ScoreDetails) -> dict[str, Any]:
    m = d.metrics
    return {
        "philosophies": {n: asdict(r) for n, r in d.philosophies.items()},
        "guardrails": {n: asdict(r) for n, r in d.guardrails.items()},
        "guardrail_reasons": list(d.guardrail_reasons),
        "guardrail_notes": list(d.guardrail_notes),
        "warnings": list(d.warnings),
        "signal_blockers": list(d.signal_blockers),
        "metrics": {
            "price": m.price,
            "price_date": m.price_date.isoformat() if m.price_date else None,
            "pe": m.pe,
            "pe_avg3": m.pe_avg3,
            "pb": m.pb,
            "latest_fiscal_year_end": (
                m.latest_fiscal_year_end.isoformat() if m.latest_fiscal_year_end else None
            ),
        },
    }


def details_from_json(raw: Mapping[str, Any] | None) -> ScoreDetails:
    raw = raw or {}
    m = raw.get("metrics") or {}
    return ScoreDetails(
        philosophies={n: _result(n, r) for n, r in (raw.get("philosophies") or {}).items()},
        guardrails={n: _result(n, r) for n, r in (raw.get("guardrails") or {}).items()},
        guardrail_reasons=tuple(raw.get("guardrail_reasons") or ()),
        guardrail_notes=tuple(raw.get("guardrail_notes") or ()),
        warnings=tuple(raw.get("warnings") or ()),
        signal_blockers=tuple(raw.get("signal_blockers") or ()),
        metrics=MetricsSummary(
            price=m.get("price"),
            price_date=_day(m.get("price_date")),
            pe=m.get("pe"),
            pe_avg3=m.get("pe_avg3"),
            pb=m.get("pb"),
            latest_fiscal_year_end=_day(m.get("latest_fiscal_year_end")),
        ),
    )

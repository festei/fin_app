"""Persist scoring runs and the signals they produce.

The graph decides *whether* a transition happened (``decide_node``) given a
``previous_conviction``. This module supplies that baseline from storage and records the result:

* baseline = the last *actionable* stored score (conviction set and not computed on blocked data).
  A run on stale data is stored but never becomes the baseline; otherwise the buy would be
  swallowed once fresh data arrives. A run where nothing could be evaluated is stored with a NULL
  conviction and is not a baseline either, so the next real score is "emerging", not "buy".
* re-running with unchanged data yields the same conviction, hence no transition, hence no
  signal: scoring is idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from fin_core.db import repo, signals_repo
from fin_core.db.models import ScoreRecord, SignalRecord
from fin_core.db.session import session_scope
from fin_core.graph import build_graph, load_config
from fin_core.scoring import score_security


@dataclass
class ScoreOutcome:
    score: ScoreRecord
    signal: SignalRecord | None  # only a signal created by THIS run
    state: dict[str, Any] = field(repr=False, default_factory=dict)  # the graph's final state


def _details(out: dict[str, Any]) -> dict[str, Any]:
    metrics = out["metrics"]
    return {
        "philosophies": {
            n: r.model_dump(mode="json") for n, r in out["philosophy_results"].items()
        },
        "guardrails": {
            n: r.model_dump(mode="json") for n, r in out.get("guardrail_results", {}).items()
        },
        "guardrail_reasons": out.get("guardrail_reasons", []),
        "guardrail_notes": out.get("guardrail_notes", []),
        "warnings": metrics.warnings,
        "signal_blockers": metrics.signal_blockers,
        "metrics": {
            "price": metrics.price,
            "price_date": metrics.price_date.isoformat() if metrics.price_date else None,
            "pe": metrics.pe,
            "pe_avg3": metrics.pe_avg3,
            "pb": metrics.pb,
            "latest_fiscal_year_end": (
                metrics.latest_fiscal_year_end.isoformat()
                if metrics.latest_fiscal_year_end
                else None
            ),
        },
    }


def score_and_record(
    session: Session,
    ticker: str,
    as_of: date | datetime,
    *,
    graph: Any = None,
    config: dict[str, Any] | None = None,
) -> ScoreOutcome:
    """Score one stored security, store the run, and store the signal if one was emitted."""
    sec = repo.get_security(session, ticker)
    previous = signals_repo.latest_actionable_score(session, sec.id) if sec else None
    out = score_security(
        session,
        ticker,
        as_of,
        previous_conviction=previous.conviction if previous else None,
        graph=graph,
        config=config,
    )
    metrics = out["metrics"]
    score = signals_repo.insert_score(
        session,
        security_id=sec.id,
        as_of=metrics.as_of,
        conviction=out["conviction"],
        all_abstained=bool(out.get("all_abstained")),
        signal_blocked=bool(metrics.signal_blockers),
        explanation=out.get("explanation", ""),
        details=_details(out),
    )
    emitted = out.get("signal")
    signal = None
    if emitted is not None:
        signal = signals_repo.insert_signal(
            session,
            security_id=sec.id,
            score_id=score.id,
            kind=emitted.kind,
            conviction=emitted.conviction,
            as_of=emitted.as_of,
            reason=emitted.reason,
        )
    return ScoreOutcome(score=score, signal=signal, state=out)


@dataclass
class BatchSummary:
    scored: int = 0
    signals: list[tuple[int, str, str]] = field(default_factory=list)  # (signal_id, ticker, kind)
    failed: dict[str, str] = field(default_factory=dict)


def score_universe(
    engine: Engine,
    as_of: date | datetime,
    *,
    tickers: list[str] | None = None,
    graph: Any = None,
    config: dict[str, Any] | None = None,
) -> BatchSummary:
    """Score every active stock, one transaction per security so one failure cannot roll back or
    block the others. Returns the signals this run created (ids, so alerts can be built)."""
    graph = graph or build_graph()
    config = config or load_config()
    wanted = {t.upper() for t in tickers} if tickers else None
    with session_scope(engine) as s:
        targets = [
            x.ticker
            for x in repo.list_securities(s)
            if x.kind == "stock" and (wanted is None or x.ticker in wanted)
        ]
    summary = BatchSummary()
    for ticker in targets:
        try:
            with session_scope(engine) as s:
                outcome = score_and_record(s, ticker, as_of, graph=graph, config=config)
                summary.scored += 1
                if outcome.signal is not None:
                    summary.signals.append((outcome.signal.id, ticker, outcome.signal.kind))
        except Exception as exc:
            summary.failed[ticker] = f"{type(exc).__name__}: {exc}"[:200]
    return summary

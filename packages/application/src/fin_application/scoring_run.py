"""Use case: one scheduled scoring run. Score the universe, store scores and signals, create
alerts, deliver them, and finish anything an earlier run left undone. Recorded as a job run."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from fin_application.alerts import ChannelsProvider, DeliverDueAlerts, ProcessSignals
from fin_application.job_runs import JobRunTracker
from fin_application.ports import Clock, UnitOfWorkFactory
from fin_application.runs import RunSummary, tracked_run
from fin_application.signals import ScoreUniverse


class RunScoring:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        tracker: JobRunTracker,
        clock: Clock,
        channels: ChannelsProvider,
        score_universe: ScoreUniverse,
        process_signals: ProcessSignals,
        deliver: DeliverDueAlerts,
    ):
        self._uow_factory = uow_factory
        self._tracker = tracker
        self._clock = clock
        self._channels = channels
        self._score_universe = score_universe
        self._process = process_signals
        self._deliver = deliver

    def execute(
        self, as_of: date | datetime | None = None, tickers: Sequence[str] | None = None
    ) -> RunSummary:
        """Status: ``failed`` if every security failed (or the channel configuration is invalid,
        which raises), ``partial`` if anything failed (a security, or the alert decision for a
        signal), else ``ok``. One failure never blocks the others, and a signal whose alert
        decision failed is retried by the next run because it stays unprocessed."""
        with tracked_run(self._tracker, "scoring") as summary:
            # Invalid channel configuration (e.g. Telegram on without credentials) fails loudly
            # here, recorded as a failed run, rather than at the first alert.
            channels = list(self._channels())
            now = self._clock.now()
            batch = self._score_universe.execute(as_of or now.date(), tickers)
            # Every signal still owed an alert decision: this run's new ones AND any left behind
            # by an earlier run that was interrupted (already-decided ones are never re-evaluated).
            with self._uow_factory() as uow:
                owed = uow.signals.pending_ids()
            outcomes = self._process.execute(owed, now, channels=channels)
            self._deliver.execute(now, channels=channels)  # drain retries that are due
            summary.rows = batch.scored
            summary.signals = len(batch.signals)
            summary.alerts = sum(1 for o in outcomes if o.alert_id is not None)
            summary.failed.update(batch.failed)
            summary.failed.update({f"signal {o.signal_id}": o.error for o in outcomes if o.error})
            securities = batch.scored + len(batch.failed)
            if securities and len(batch.failed) >= securities:
                summary.status = "failed"
            else:
                summary.status = "partial" if summary.failed else "ok"
        return summary

"""Use cases around the user's own settings: the watchlist, channel muting, reading alerts, and
the read models behind the feed and the ranking."""

from __future__ import annotations

from datetime import datetime

from fin_application.ports import Clock, OpportunityRow, UnitOfWorkFactory
from fin_application.scoring import parse_ticker
from fin_domain.shared import NotFound, Ticker
from fin_domain.signals import Alert, ChannelState, ScoreEntry


class WatchlistService:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def _known(self, uow, raw: str) -> Ticker:
        ticker = parse_ticker(raw)
        if uow.securities.get(ticker) is None:
            raise NotFound("security", str(ticker))
        return ticker

    def add(self, raw: str) -> tuple[Ticker, bool]:
        """``(ticker, newly_added)``. ``NotFound`` for a ticker outside the universe."""
        with self._uow_factory() as uow:
            ticker = self._known(uow, raw)
            return ticker, uow.watchlist.add(ticker)

    def remove(self, raw: str) -> tuple[Ticker, bool]:
        with self._uow_factory() as uow:
            ticker = self._known(uow, raw)
            return ticker, uow.watchlist.remove(ticker)

    def tickers(self) -> list[str]:
        with self._uow_factory() as uow:
            return uow.watchlist.tickers()


class ChannelService:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock):
        self._uow_factory = uow_factory
        self._clock = clock

    def state(self, channel: str) -> ChannelState:
        with self._uow_factory() as uow:
            return uow.channels.get(channel)

    def mute(self, channel: str, until: datetime) -> None:
        with self._uow_factory() as uow:
            state = uow.channels.get(channel)
            state.mute_until(until)
            uow.channels.save(state)

    def unmute(self, channel: str) -> None:
        with self._uow_factory() as uow:
            state = uow.channels.get(channel)
            state.unmute()
            uow.channels.save(state)


class AlertFeed:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock):
        self._uow_factory = uow_factory
        self._clock = clock

    def page(
        self, limit: int = 50, before_id: int | None = None, unread_only: bool = False
    ) -> list[Alert]:
        with self._uow_factory() as uow:
            return uow.alert_feed.page(limit, before_id, unread_only)

    def mark_read(self, alert_id: int) -> None:
        """Raises ``NotFound`` for an alert that does not exist."""
        with self._uow_factory() as uow:
            if not uow.alerts.mark_read(alert_id, self._clock.now()):
                raise NotFound("alert", alert_id)


class Opportunities:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def top(self, limit: int = 20, newer_than: datetime | None = None) -> list[OpportunityRow]:
        with self._uow_factory() as uow:
            return uow.opportunities.top(limit, newer_than)

    def explain(self, raw: str) -> ScoreEntry | None:
        """The latest stored score of a security (any kind), or ``None`` if it has none yet.
        ``NotFound`` for a ticker outside the universe."""
        ticker = parse_ticker(raw)
        with self._uow_factory() as uow:
            if uow.securities.get(ticker) is None:
                raise NotFound("security", str(ticker))
            return uow.scores.latest(ticker)

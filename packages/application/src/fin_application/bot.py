"""What a chat bot may ask of the application. The bot itself (parsing commands, wording the
answers, talking to the chat service) is an adapter; everything it *does* is here."""

from __future__ import annotations

from datetime import datetime

from fin_application.ports import OpportunityRow, UnitOfWorkFactory
from fin_application.watchlist import ChannelService, Opportunities, WatchlistService
from fin_domain.shared import Ticker
from fin_domain.signals import ScoreEntry


class BotCommands:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        watchlist: WatchlistService,
        channels: ChannelService,
        opportunities: Opportunities,
    ):
        self._uow_factory = uow_factory
        self._watchlist = watchlist
        self._channels = channels
        self._opportunities = opportunities

    # --- the watchlist --------------------------------------------------------------------------
    def watch(self, raw: str) -> tuple[Ticker, bool]:
        """``(ticker, newly_added)``; ``NotFound`` for a ticker outside the universe."""
        return self._watchlist.add(raw)

    def unwatch(self, raw: str) -> tuple[Ticker, bool]:
        return self._watchlist.remove(raw)

    def watchlist(self) -> list[str]:
        return self._watchlist.tickers()

    # --- scores ---------------------------------------------------------------------------------
    def top(self, n: int) -> list[OpportunityRow]:
        return self._opportunities.top(limit=n)

    def explain(self, raw: str) -> ScoreEntry | None:
        """The latest stored score, or ``None`` if there is none yet; ``NotFound`` for an unknown
        ticker."""
        return self._opportunities.explain(raw)

    # --- muting ---------------------------------------------------------------------------------
    def mute(self, channel: str, until: datetime) -> None:
        self._channels.mute(channel, until)

    def unmute(self, channel: str) -> None:
        self._channels.unmute(channel)

    # --- where the bot has read up to -----------------------------------------------------------
    def load_offset(self, channel: str) -> int | None:
        with self._uow_factory() as uow:
            return uow.cursors.load(channel)

    def save_offset(self, channel: str, value: int) -> None:
        with self._uow_factory() as uow:
            uow.cursors.save(channel, value)

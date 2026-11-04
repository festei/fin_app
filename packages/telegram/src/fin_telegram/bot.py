"""The Telegram command bot: parses commands, words the answers, and asks the application.

Commands, answered only for the configured chat id (other chats are ignored silently):
/watch T, /unwatch T, /watchlist, /top [n], /why T, /mute [hours], /unmute, /help.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fin_application.bot import BotCommands
from fin_application.errors import ChannelError
from fin_domain.shared import NotFound
from fin_telegram.client import CHANNEL, TelegramClient

log = logging.getLogger(__name__)

MAX_MUTE_HOURS = 24 * 30

HELP = (
    "/watch T - follow a ticker (sell alerts are sent only for watched stocks)\n"
    "/unwatch T - stop following\n"
    "/watchlist - list followed tickers\n"
    "/top [n] - best-scoring securities now\n"
    "/why T - the latest score breakdown\n"
    "/mute [hours] - pause Telegram alerts (default 24); /unmute to resume\n"
    "The in-app feed is never muted."
)


class TelegramBot:
    """Answers commands from the one configured chat. All state lives behind ``BotCommands``."""

    def __init__(
        self,
        client: TelegramClient,
        commands: BotCommands,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.client, self.commands, self._now = client, commands, now

    # --- dispatch ---------------------------------------------------------------------------

    def handle_update(self, update: dict[str, Any]) -> str | None:
        """The reply text for an update, or ``None`` (stranger's chat, not a command)."""
        message = update.get("message") or {}
        if str((message.get("chat") or {}).get("id")) != self.client.chat_id:
            return None
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return None
        command, *args = text.split()
        handlers = {
            "/watch": self._watch,
            "/unwatch": self._unwatch,
            "/watchlist": self._watchlist,
            "/top": self._top,
            "/why": self._why,
            "/mute": self._mute,
            "/unmute": self._unmute,
            "/help": lambda _: HELP,
            "/start": lambda _: HELP,
        }
        handler = handlers.get(command.split("@")[0].lower())
        if handler is None:
            return f"Unknown command.\n{HELP}"
        try:
            return handler(args)
        except Exception:
            log.exception("telegram command %s failed", command)
            return "Something went wrong handling that command."

    def load_offset(self) -> int | None:
        """The persisted next-update offset (``None`` on first run or if it cannot be read)."""
        try:
            return self.commands.load_offset(CHANNEL)
        except Exception as exc:
            log.warning("could not load the telegram offset (%s)", type(exc).__name__)
            return None

    def _save_offset(self, offset: int) -> None:
        try:
            self.commands.save_offset(CHANNEL, offset)
        except Exception as exc:
            log.warning("could not save the telegram offset (%s)", type(exc).__name__)

    def poll_once(self, offset: int | None, timeout: int = 25) -> int | None:
        """One long-poll round: answer every command, return the next update offset.

        Each update is acknowledged (offset advanced and persisted) *before* its command runs, so
        a command is executed at most once: a failing reply, an exception, or a restart can never
        replay ``/mute`` or ``/unwatch``. The price is that a command interrupted mid-way is lost
        and the user has to resend it. A reply that cannot be sent is logged and dropped.
        """
        next_offset = offset
        for update in self.client.get_updates(offset, timeout):
            if "update_id" not in update:
                continue
            next_offset = update["update_id"] + 1
            self._save_offset(next_offset)
            reply = self.handle_update(update)
            if reply:
                try:
                    self.client.send_message(reply)
                except ChannelError as exc:
                    log.warning("could not send a telegram reply: %s", exc)
        return next_offset

    # --- commands ---------------------------------------------------------------------------

    def _watch(self, args: list[str]) -> str:
        if not args:
            return "Usage: /watch TICKER"
        try:
            ticker, added = self.commands.watch(args[0])
        except NotFound:
            return f"Unknown ticker {args[0].upper()} (it is not in the universe)."
        return f"Now watching {ticker}." if added else f"{ticker} is already watched."

    def _unwatch(self, args: list[str]) -> str:
        if not args:
            return "Usage: /unwatch TICKER"
        try:
            ticker, removed = self.commands.unwatch(args[0])
        except NotFound:
            return f"Unknown ticker {args[0].upper()}."
        return f"Stopped watching {ticker}." if removed else f"{ticker} was not watched."

    def _watchlist(self, _: list[str]) -> str:
        tickers = self.commands.watchlist()
        return "Watching: " + ", ".join(tickers) if tickers else "The watchlist is empty."

    def _top(self, args: list[str]) -> str:
        try:
            n = max(1, min(int(args[0]), 20)) if args else 5
        except ValueError:
            return "Usage: /top [n]"
        lines = []
        for i, row in enumerate(self.commands.top(n), start=1):
            parts = ", ".join(
                f"{name} {result.score:.0f}"
                for name, result in sorted(row.details.philosophies.items())
                if result.evaluated
            )
            lines.append(f"{i}. {row.ticker} {row.conviction:.0f}  ({parts})")
        return "\n".join(lines) if lines else "No scored securities yet."

    def _why(self, args: list[str]) -> str:
        if not args:
            return "Usage: /why TICKER"
        try:
            score = self.commands.explain(args[0])
        except NotFound:
            return f"Unknown ticker {args[0].upper()}."
        if score is None:
            return f"No score yet for {args[0].upper()}."
        head = "no conviction (nothing could be evaluated)"
        if score.conviction is not None:
            head = f"conviction {score.conviction:.1f}"
        lines = [f"{score.ticker}: {head} (as of {score.as_of.date()})", score.explanation]
        for name, p in sorted(score.details.philosophies.items()):
            if not p.evaluated:
                lines.append(f"- {name}: not evaluated ({p.explanation})")
                continue
            passed = sum(1 for c in p.criteria if c.passed)
            failing = [c.name for c in p.criteria if not c.passed][:3]
            tail = f"; failing: {', '.join(failing)}" if failing else ""
            lines.append(f"- {name} {p.score:.0f} ({passed}/{len(p.criteria)} criteria{tail})")
        for items in (
            score.details.guardrail_reasons,
            score.details.guardrail_notes,
            score.details.warnings,
        ):
            lines += [f"* {item}" for item in items]
        return "\n".join(line for line in lines if line)

    def _mute(self, args: list[str]) -> str:
        try:
            hours = float(args[0]) if args else 24.0
        except ValueError:
            return "Usage: /mute [hours]"
        if not 0 < hours <= MAX_MUTE_HOURS:
            return f"Choose between 0 and {MAX_MUTE_HOURS} hours."
        until = self._now() + timedelta(hours=hours)
        self.commands.mute(CHANNEL, until)
        return (
            f"Telegram alerts muted until {until:%Y-%m-%d %H:%M} UTC. "
            "The in-app feed is unaffected."
        )

    def _unmute(self, _: list[str]) -> str:
        self.commands.unmute(CHANNEL)
        return "Telegram alerts resumed."

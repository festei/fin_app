"""Telegram channel and command bot over the plain Bot API (httpx, no extra dependency).

STATUS: written from the public Bot API documentation and tested only against a mocked API. It has
not been run against Telegram (the build sandbox cannot reach it).

Feature-flagged: the channel and the bot exist only when ``telegram_enabled`` is on (see
``channels.build_channels``). The bot token is part of every request URL, so it is kept out of
errors and logs (httpx's request logging is switched to WARNING, exceptions are re-raised without
the URL, and any echo of the token is masked).

Commands, answered only for the configured chat id (other chats are ignored silently):
/watch T, /unwatch T, /watchlist, /top [n], /why T, /mute [hours], /unmute, /help.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Engine

from fin_core.alerts.base import AlertMessage, ChannelConfigError, ChannelError
from fin_core.db import repo, signals_repo
from fin_core.db.session import session_scope

logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs contain the bot token
log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_TEXT = 4000  # Telegram's limit is 4096
CHANNEL = "telegram"
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


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str,
        client: httpx.Client | None = None,
        base_url: str = API_BASE,
    ):
        if not token or not chat_id:
            raise ChannelConfigError("Telegram needs both a bot token and a chat id")
        self._token = token
        self.chat_id = str(chat_id)
        self._base = base_url.rstrip("/")
        self._http = client or httpx.Client(timeout=60)

    def _mask(self, text: str) -> str:
        return text.replace(self._token, "***")

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        url = f"{self._base}/bot{self._token}/{method}"
        try:
            resp = self._http.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise ChannelError(f"Telegram {method}: {type(exc).__name__}") from None
        try:
            body = resp.json()
        except ValueError:
            raise ChannelError(f"Telegram {method}: HTTP {resp.status_code}, not JSON") from None
        if resp.status_code >= 400 or not body.get("ok"):
            detail = self._mask(str(body.get("description", "")))
            raise ChannelError(f"Telegram {method}: HTTP {resp.status_code} {detail}"[:300])
        return body.get("result")

    def send_message(self, text: str) -> None:
        self._call(
            "sendMessage",
            {"chat_id": self.chat_id, "text": text[:MAX_TEXT], "disable_web_page_preview": True},
        )

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload) or []


class TelegramChannel:
    name = CHANNEL

    def __init__(self, client: TelegramClient):
        self.client = client

    def send(self, message: AlertMessage) -> None:
        self.client.send_message(message.text())


class TelegramBot:
    """Answers commands from the one configured chat, using the database for all state."""

    def __init__(
        self,
        client: TelegramClient,
        engine: Engine,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.client, self.engine, self._now = client, engine, now

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
            with session_scope(self.engine) as s:
                return signals_repo.update_cursor(s, CHANNEL)
        except Exception as exc:
            log.warning("could not load the telegram offset (%s)", type(exc).__name__)
            return None

    def _save_offset(self, offset: int) -> None:
        try:
            with session_scope(self.engine) as s:
                signals_repo.set_update_cursor(s, CHANNEL, offset)
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
        with session_scope(self.engine) as s:
            sec = repo.get_security(s, args[0])
            if sec is None:
                return f"Unknown ticker {args[0].upper()} (it is not in the universe)."
            added = signals_repo.watchlist_add(s, sec.id)
            return f"Now watching {sec.ticker}." if added else f"{sec.ticker} is already watched."

    def _unwatch(self, args: list[str]) -> str:
        if not args:
            return "Usage: /unwatch TICKER"
        with session_scope(self.engine) as s:
            sec = repo.get_security(s, args[0])
            if sec is None:
                return f"Unknown ticker {args[0].upper()}."
            removed = signals_repo.watchlist_remove(s, sec.id)
            return (
                f"Stopped watching {sec.ticker}." if removed else f"{sec.ticker} was not watched."
            )

    def _watchlist(self, _: list[str]) -> str:
        with session_scope(self.engine) as s:
            tickers = signals_repo.watchlist_tickers(s)
        return "Watching: " + ", ".join(tickers) if tickers else "The watchlist is empty."

    def _top(self, args: list[str]) -> str:
        try:
            n = max(1, min(int(args[0]), 20)) if args else 5
        except ValueError:
            return "Usage: /top [n]"
        with session_scope(self.engine) as s:
            rows = signals_repo.opportunities(s, limit=n)
            lines = []
            for i, (sec, score) in enumerate(rows, start=1):
                phil = (score.details or {}).get("philosophies", {})
                parts = ", ".join(
                    f"{k} {v['score']:.0f}" for k, v in sorted(phil.items()) if v.get("evaluated")
                )
                lines.append(f"{i}. {sec.ticker} {score.conviction:.0f}  ({parts})")
        return "\n".join(lines) if lines else "No scored securities yet."

    def _why(self, args: list[str]) -> str:
        if not args:
            return "Usage: /why TICKER"
        with session_scope(self.engine) as s:
            sec = repo.get_security(s, args[0])
            if sec is None:
                return f"Unknown ticker {args[0].upper()}."
            score = signals_repo.latest_score(s, sec.id)
            if score is None:
                return f"No score yet for {sec.ticker}."
            d = score.details or {}
            head = "no conviction (nothing could be evaluated)"
            if score.conviction is not None:
                head = f"conviction {score.conviction:.1f}"
            lines = [f"{sec.ticker}: {head} (as of {score.as_of.date()})", score.explanation]
            for name, p in sorted((d.get("philosophies") or {}).items()):
                if not p.get("evaluated", True):
                    lines.append(f"- {name}: not evaluated ({p.get('explanation', '')})")
                    continue
                crit = p.get("criteria") or []
                passed = sum(1 for c in crit if c.get("passed"))
                failing = [c["name"] for c in crit if not c.get("passed")][:3]
                tail = f"; failing: {', '.join(failing)}" if failing else ""
                lines.append(f"- {name} {p['score']:.0f} ({passed}/{len(crit)} criteria{tail})")
            for key in ("guardrail_reasons", "guardrail_notes", "warnings"):
                lines += [f"* {item}" for item in d.get(key) or []]
        return "\n".join(line for line in lines if line)

    def _mute(self, args: list[str]) -> str:
        try:
            hours = float(args[0]) if args else 24.0
        except ValueError:
            return "Usage: /mute [hours]"
        if not 0 < hours <= MAX_MUTE_HOURS:
            return f"Choose between 0 and {MAX_MUTE_HOURS} hours."
        until = self._now() + timedelta(hours=hours)
        with session_scope(self.engine) as s:
            signals_repo.set_muted_until(s, CHANNEL, until)
        return (
            f"Telegram alerts muted until {until:%Y-%m-%d %H:%M} UTC. "
            "The in-app feed is unaffected."
        )

    def _unmute(self, _: list[str]) -> str:
        with session_scope(self.engine) as s:
            signals_repo.set_muted_until(s, CHANNEL, None)
        return "Telegram alerts resumed."

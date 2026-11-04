"""Telegram client and alert channel over the plain Bot API (httpx, no extra dependency).

STATUS: written from the public Bot API documentation and tested only against a mocked API. It has
not been run against Telegram (the build sandbox cannot reach it).

Feature-flagged: the channel and the bot exist only when ``telegram_enabled`` is on (see
``fin_bootstrap``). The bot token is part of every request URL, so it is kept out of errors and
logs (httpx's request logging is switched to WARNING, exceptions are re-raised without the URL,
and any echo of the token is masked).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from fin_application.errors import ChannelConfigError, ChannelError
from fin_application.ports import AlertMessage

logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs contain the bot token
log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_TEXT = 4000  # Telegram's limit is 4096
CHANNEL = "telegram"


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

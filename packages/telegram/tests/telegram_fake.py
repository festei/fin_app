"""A mocked Telegram Bot API, shared by the client and bot tests (the real one is not reachable
from the build sandbox, so none of this has run against Telegram).

Not a ``conftest.py`` on purpose: see ``packages/persistence/tests/db_fixtures.py``.
"""

import json

import httpx

from fin_application.ports import AlertMessage
from fin_telegram import TelegramClient

TOKEN, CHAT = "123456:SECRET-TOKEN", "42"


class FakeTelegram:
    """A mock Bot API: records every call, answers sendMessage/getUpdates."""

    def __init__(self, updates=None, status=200, body=None):
        self.calls, self.updates, self.status, self.body = [], updates or [], status, body
        self.http = httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        self.calls.append((request.url.path, method, json.loads(request.content)))
        if self.body is not None:
            return httpx.Response(self.status, json=self.body)
        result = self.updates if method == "getUpdates" else {}
        return httpx.Response(200, json={"ok": True, "result": result})

    def client(self) -> TelegramClient:
        return TelegramClient(TOKEN, CHAT, client=self.http)

    def sent_texts(self):
        return [p["text"] for _, m, p in self.calls if m == "sendMessage"]


def message() -> AlertMessage:
    return AlertMessage(1, "AAA", "buy", "high", 80.0, "AAA: BUY (conviction 80)", "body line")


def message() -> AlertMessage:
    return AlertMessage(1, "AAA", "buy", "high", 80.0, "AAA: BUY (conviction 80)", "body line")

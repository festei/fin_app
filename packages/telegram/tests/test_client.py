"""The Telegram client and channel against a MOCKED Bot API (the real one is not reachable from
the build sandbox, so none of this has run against Telegram)."""

from __future__ import annotations

import logging

import httpx
import pytest
from telegram_fake import CHAT, TOKEN, FakeTelegram, message

from fin_application.errors import ChannelConfigError, ChannelError
from fin_telegram import TelegramChannel, TelegramClient

# --- client and channel -------------------------------------------------------------------------


def test_send_message_posts_to_the_bot_api_for_the_configured_chat():
    fake = FakeTelegram()
    TelegramChannel(fake.client()).send(message())
    path, method, payload = fake.calls[0]
    assert path == f"/bot{TOKEN}/sendMessage" and method == "sendMessage"
    assert payload["chat_id"] == CHAT and payload["text"] == "AAA: BUY (conviction 80)\n\nbody line"


def test_long_messages_are_cut_to_telegrams_limit():
    fake = FakeTelegram()
    fake.client().send_message("x" * 10_000)
    assert len(fake.calls[0][2]["text"]) == 4000


API_ERRORS = [
    (401, {"ok": False, "description": "Unauthorized"}),
    (400, {"ok": False, "description": "chat not found"}),
]


@pytest.mark.parametrize("status, body", API_ERRORS)
def test_api_errors_become_channel_errors_without_the_token(status, body):
    fake = FakeTelegram(status=status, body=body)
    with pytest.raises(ChannelError) as exc:
        fake.client().send_message("hi")
    assert str(status) in str(exc.value) and body["description"] in str(exc.value)
    assert TOKEN not in str(exc.value)


def test_a_token_echoed_by_the_api_is_masked():
    fake = FakeTelegram(status=400, body={"ok": False, "description": f"bad request {TOKEN}"})
    with pytest.raises(ChannelError) as exc:
        fake.client().send_message("hi")
    assert TOKEN not in str(exc.value) and "***" in str(exc.value)


def test_transport_and_non_json_failures_do_not_leak_the_token():
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    down = TelegramClient(TOKEN, CHAT, client=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(ChannelError) as exc:
        down.send_message("hi")
    assert TOKEN not in str(exc.value) and exc.value.__suppress_context__

    html = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(502, text="<html>")))
    with pytest.raises(ChannelError, match="not JSON") as exc:
        TelegramClient(TOKEN, CHAT, client=html).send_message("hi")
    assert TOKEN not in str(exc.value)


def test_the_token_never_reaches_the_logs(caplog):
    fake = FakeTelegram(status=401, body={"ok": False, "description": "Unauthorized"})
    with caplog.at_level(logging.DEBUG), pytest.raises(ChannelError):
        fake.client().send_message("hi")
    assert TOKEN not in caplog.text
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING  # request URLs off


def test_client_needs_both_token_and_chat():
    with pytest.raises(ChannelConfigError):
        TelegramClient("", CHAT)
    with pytest.raises(ChannelConfigError):
        TelegramClient(TOKEN, "")

"""Telegram channel and bot against a MOCKED Bot API (the real one is not reachable from the build
sandbox, so none of this has run against Telegram). Also the feature-flag guarantees."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from fin_core.alerts.base import AlertMessage, ChannelConfigError, ChannelError
from fin_core.alerts.channels import build_channels
from fin_core.alerts.pipeline import process_signals
from fin_core.alerts.policy import AlertPolicy
from fin_core.alerts.telegram import TelegramBot, TelegramChannel, TelegramClient
from fin_core.db import signals_repo
from fin_core.db.session import session_scope
from fin_core.features import FeatureFlags
from fin_core.signals import score_universe

TOKEN, CHAT = "123456:SECRET-TOKEN", "42"
NOW = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)
POLICY = AlertPolicy.load()


class FakeTelegram:
    """A mock Bot API: records every call, answers sendMessage/getUpdates."""

    def __init__(self, updates=None, status=200, body=None):
        self.calls, self.updates, self.status, self.body = [], updates or [], status, body
        self.http = httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        import json

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


# --- feature flag -------------------------------------------------------------------------------


def test_flag_off_registers_only_the_feed_needs_no_token_and_makes_no_calls(
    raw_engine, seed_company
):
    fake = FakeTelegram()
    channels = build_channels(FeatureFlags({}), env={}, http_client=fake.http)
    assert [c.name for c in channels] == ["in_app"]
    seed_company(raw_engine, "AAA", price=25.0)
    ids = [sid for sid, _, _ in score_universe(raw_engine, date(2025, 3, 10)).signals]
    process_signals(raw_engine, ids, POLICY, channels, NOW)  # a full alert, delivered
    assert fake.calls == []  # zero Telegram HTTP calls
    assert FeatureFlags.load().is_enabled("telegram_enabled") is False  # the shipped default


def test_flag_on_without_credentials_fails_at_startup_without_leaking_values():
    flags = FeatureFlags({"telegram_enabled": True})
    with pytest.raises(ChannelConfigError, match="TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"):
        build_channels(flags, env={})
    with pytest.raises(ChannelConfigError) as exc:
        build_channels(flags, env={"TELEGRAM_BOT_TOKEN": TOKEN})  # chat id missing
    assert TOKEN not in str(exc.value)


def test_flag_on_with_credentials_registers_telegram_after_the_feed():
    env = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT}
    channels = build_channels(FeatureFlags({"telegram_enabled": True}), env=env)
    assert [c.name for c in channels] == ["in_app", "telegram"]


def test_the_environment_variable_turns_it_on(monkeypatch):
    monkeypatch.setenv("FEATURE_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    assert [c.name for c in build_channels()] == ["in_app", "telegram"]
    monkeypatch.setenv("FEATURE_TELEGRAM_ENABLED", "false")
    assert [c.name for c in build_channels()] == ["in_app"]


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


# --- the bot ------------------------------------------------------------------------------------


@pytest.fixture
def bot_env(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)  # conviction 78.5
    seed_company(raw_engine, "BBB", price=12.0)  # conviction 92.8
    seed_company(raw_engine, "NEW", price=25.0)  # never scored
    score_universe(raw_engine, date(2025, 3, 10), tickers=["AAA", "BBB"])
    fake = FakeTelegram()
    bot = TelegramBot(fake.client(), raw_engine, now=lambda: NOW)
    return bot, fake, raw_engine


def say(bot, text, chat=CHAT, update_id=1):
    return bot.handle_update(
        {"update_id": update_id, "message": {"chat": {"id": int(chat)}, "text": text}}
    )


def test_other_chats_and_plain_text_are_ignored_silently(bot_env):
    bot, fake, _ = bot_env
    assert say(bot, "/top", chat="999") is None  # a stranger gets no answer, not even "denied"
    assert say(bot, "hello there") is None
    assert bot.handle_update({"update_id": 1}) is None and fake.calls == []


def test_help_and_unknown_commands(bot_env):
    bot, _, _ = bot_env
    assert "/watch T" in say(bot, "/help") and "/watch T" in say(bot, "/start")
    assert say(bot, "/dance").startswith("Unknown command.")


def test_watch_unwatch_and_watchlist(bot_env):
    bot, _, engine = bot_env
    assert say(bot, "/watch aaa") == "Now watching AAA."
    assert say(bot, "/watch AAA") == "AAA is already watched."
    assert say(bot, "/watch@MyBot BBB") == "Now watching BBB."  # command with the bot's @name
    assert say(bot, "/watchlist") == "Watching: AAA, BBB"
    assert say(bot, "/unwatch AAA") == "Stopped watching AAA."
    assert say(bot, "/unwatch AAA") == "AAA was not watched."
    assert "Unknown ticker ZZZ" in say(bot, "/watch zzz")
    assert say(bot, "/watch") == "Usage: /watch TICKER"
    with session_scope(engine) as s:
        assert signals_repo.watchlist_tickers(s) == ["BBB"]
    assert say(bot, "/unwatch BBB") and say(bot, "/watchlist") == "The watchlist is empty."


def test_top_ranks_by_conviction_with_the_philosophy_breakdown(bot_env):
    bot, _, _ = bot_env
    assert say(bot, "/top") == (
        "1. BBB 93  (graham 86, lynch 100)\n2. AAA 78  (graham 57, lynch 100)"
    )
    assert say(bot, "/top 1").startswith("1. BBB 93") and "AAA" not in say(bot, "/top 1")
    assert say(bot, "/top x") == "Usage: /top [n]"


def test_why_explains_the_latest_score(bot_env):
    bot, _, _ = bot_env
    text = say(bot, "/why AAA")
    assert text.startswith("AAA: conviction 78.5 (as of 2025-03-10)")
    assert "- graham 57 (4/7 criteria; failing: P/B below limit" in text
    assert "- lynch 100 (3/3 criteria)" in text
    assert "- buffett: not evaluated" in text
    assert say(bot, "/why NEW") == "No score yet for NEW."
    assert say(bot, "/why zzz") == "Unknown ticker ZZZ."
    assert say(bot, "/why") == "Usage: /why TICKER"


def test_mute_and_unmute_set_and_clear_the_channel_state(bot_env):
    bot, _, engine = bot_env
    assert "muted until 2025-03-10 14:00 UTC" in say(bot, "/mute 2")
    with session_scope(engine) as s:
        assert signals_repo.muted_until(s, "telegram").replace(tzinfo=UTC) == NOW + timedelta(
            hours=2
        )
    assert "until 2025-03-11 12:00 UTC" in say(bot, "/mute")  # default 24 hours
    assert say(bot, "/mute abc") == "Usage: /mute [hours]"
    assert say(bot, "/mute 0").startswith("Choose between")
    assert say(bot, "/mute 100000").startswith("Choose between")
    assert say(bot, "/unmute") == "Telegram alerts resumed."
    with session_scope(engine) as s:
        assert signals_repo.muted_until(s, "telegram") is None


def test_a_muted_telegram_skips_delivery_but_the_feed_still_gets_it(bot_env, seed_company):
    bot, fake, engine = bot_env
    say(bot, "/mute 2")
    seed_company(engine, "CCC", price=25.0)
    ids = [sid for sid, _, _ in score_universe(engine, date(2025, 3, 10), tickers=["CCC"]).signals]
    channels = build_channels(
        FeatureFlags({"telegram_enabled": True}),
        env={"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT},
        http_client=fake.http,
    )
    process_signals(engine, ids, POLICY, channels, NOW)
    assert [t for t in fake.sent_texts() if t.startswith("CCC")] == []  # nothing pushed
    with session_scope(engine) as s:
        from fin_core.db.models import AlertDelivery

        rows = {d.channel: d.status for d in s.query(AlertDelivery)}
    assert rows == {"in_app": "sent", "telegram": "skipped"}


def test_a_failing_command_gets_a_generic_reply_not_a_crash(bot_env, monkeypatch):
    bot, _, _ = bot_env
    monkeypatch.setattr(bot, "_top", lambda args: 1 / 0)
    assert say(bot, "/top") == "Something went wrong handling that command."


def test_poll_once_answers_commands_and_returns_the_next_offset(bot_env):
    bot, fake, _ = bot_env
    fake.updates = [
        {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/watch AAA"}},
        {"update_id": 11, "message": {"chat": {"id": 999}, "text": "/watch BBB"}},  # stranger
        {"update_id": 12, "message": {"chat": {"id": 42}, "text": "no command"}},
    ]
    assert bot.poll_once(None, timeout=1) == 13
    assert fake.sent_texts() == ["Now watching AAA."]  # one reply; nothing for the stranger
    assert fake.calls[0][1] == "getUpdates" and "offset" not in fake.calls[0][2]
    fake.updates = []
    assert bot.poll_once(13, timeout=1) == 13
    assert fake.calls[-1][2]["offset"] == 13  # acknowledges what it has already handled


def test_the_offset_is_persisted_per_update_before_its_command_runs(bot_env, monkeypatch):
    bot, fake, engine = bot_env
    assert bot.load_offset() is None
    seen = []

    def spy(update):
        seen.append(bot.load_offset())  # what a crash inside the command would leave behind
        return "ok"

    monkeypatch.setattr(bot, "handle_update", spy)
    fake.updates = [
        {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/watch AAA"}},
        {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/watch BBB"}},
    ]
    assert bot.poll_once(None, timeout=1) == 12
    assert seen == [11, 12]  # already acknowledged when each command started
    assert bot.load_offset() == 12


def test_a_failing_reply_never_replays_a_command(bot_env, monkeypatch):
    bot, fake, engine = bot_env
    fake.updates = [{"update_id": 5, "message": {"chat": {"id": 42}, "text": "/watch AAA"}}]

    def broken(text):
        raise ChannelError("telegram is down")

    monkeypatch.setattr(bot.client, "send_message", broken)
    assert bot.poll_once(None, timeout=1) == 6  # no exception escapes
    assert bot.load_offset() == 6
    with session_scope(engine) as s:
        assert signals_repo.watchlist_tickers(s) == ["AAA"]  # the command ran exactly once
    # a restart resumes from the stored offset instead of asking for the update again
    fake.updates = []
    bot.poll_once(bot.load_offset(), timeout=1)
    assert fake.calls[-1][2]["offset"] == 6


def test_updates_without_an_id_are_skipped(bot_env):
    bot, fake, _ = bot_env
    fake.updates = [{"message": {"chat": {"id": 42}, "text": "/watch AAA"}}]
    assert bot.poll_once(7, timeout=1) == 7 and fake.sent_texts() == []

"""Which channels exist is decided by the feature flag, and wrong configuration fails at startup."""

from dataclasses import replace

import httpx
import pytest

from fin_application.errors import ChannelConfigError
from fin_bootstrap import Container, Settings
from fin_bootstrap.channels import build_channels
from fin_config import FeatureFlags
from fin_telegram import TelegramBot

TOKEN, CHAT = "123456:SECRET-TOKEN", "42"

ON = FeatureFlags({"telegram_enabled": True})
CREDS = Settings(telegram_bot_token=TOKEN, telegram_chat_id=CHAT)


def test_flag_off_registers_only_the_feed_needs_no_token_and_makes_no_calls():
    calls = []
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))
    )
    channels = build_channels(FeatureFlags({}), Settings(), http_client=http)
    assert [c.name for c in channels] == ["in_app"]
    assert calls == []  # zero Telegram HTTP calls
    assert FeatureFlags.load().is_enabled("telegram_enabled") is False  # the shipped default


def test_flag_on_without_credentials_fails_at_startup_without_leaking_values():
    with pytest.raises(ChannelConfigError, match="TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"):
        build_channels(ON, Settings())
    with pytest.raises(ChannelConfigError) as exc:
        build_channels(ON, Settings(telegram_bot_token=TOKEN))  # chat id missing
    assert TOKEN not in str(exc.value)


def test_flag_on_with_credentials_registers_telegram_after_the_feed():
    assert [c.name for c in build_channels(ON, CREDS)] == ["in_app", "telegram"]


def test_the_environment_variable_turns_it_on(monkeypatch):
    monkeypatch.setenv("FEATURE_TELEGRAM_ENABLED", "true")
    container = Container(
        Settings.from_environ({"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT})
    )
    assert [c.name for c in container.channels()] == ["in_app", "telegram"]
    monkeypatch.setenv("FEATURE_TELEGRAM_ENABLED", "false")
    assert [c.name for c in container.channels()] == ["in_app"]


def test_the_bot_exists_only_when_telegram_is_on_and_shares_the_channels_client():
    off = Container(Settings(), flags=FeatureFlags({}))
    assert off.telegram_bot() is None
    on = Container(replace(CREDS, database_url="sqlite://"), flags=ON)  # the engine stays lazy
    channels = on.channels()
    bot = on.telegram_bot(channels)
    assert isinstance(bot, TelegramBot) and bot.client is channels[1].client


def test_a_misconfigured_container_fails_when_the_channels_are_built_not_before():
    container = Container(Settings(), flags=ON)  # nothing is built yet
    with pytest.raises(ChannelConfigError):
        container.channels()

"""Which alert channels are active: chosen by feature flags, validated at startup."""

from __future__ import annotations

import httpx

from fin_application.alerts import InAppChannel
from fin_application.errors import ChannelConfigError
from fin_application.ports import AlertChannel
from fin_bootstrap.settings import Settings
from fin_config import FeatureFlags
from fin_telegram import TelegramChannel, TelegramClient


def telegram_client(settings: Settings, http_client: httpx.Client | None = None) -> TelegramClient:
    token, chat = settings.telegram_bot_token, settings.telegram_chat_id
    if not token or not chat:
        raise ChannelConfigError(
            "telegram_enabled is on but TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not both set"
        )
    return TelegramClient(token, chat, client=http_client)


def build_channels(
    flags: FeatureFlags, settings: Settings, http_client: httpx.Client | None = None
) -> list[AlertChannel]:
    """The in-app feed is always on. Telegram exists only when its flag is on; then missing
    credentials are a startup error (``ChannelConfigError``), not a failure at the first alert.
    With the flag off nothing about Telegram is created: no client, no token needed, no calls."""
    channels: list[AlertChannel] = [InAppChannel()]
    if flags.is_enabled("telegram_enabled"):
        channels.append(TelegramChannel(telegram_client(settings, http_client)))
    return channels

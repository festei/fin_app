"""Which alert channels are active: chosen by feature flags, validated at startup."""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

from fin_core.alerts.base import IN_APP, AlertChannel, AlertMessage, ChannelConfigError
from fin_core.alerts.telegram import TelegramChannel, TelegramClient
from fin_core.features import FeatureFlags


class InAppChannel:
    """The alert row itself is the in-app feed (API/UI read it); nothing is pushed anywhere."""

    name = IN_APP

    def send(self, message: AlertMessage) -> None:
        return None


def telegram_client(
    env: Mapping[str, str] | None = None, http_client: httpx.Client | None = None
) -> TelegramClient:
    env = os.environ if env is None else env
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise ChannelConfigError(
            "telegram_enabled is on but TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not both set"
        )
    return TelegramClient(token, chat, client=http_client)


def build_channels(
    flags: FeatureFlags | None = None,
    env: Mapping[str, str] | None = None,
    http_client: httpx.Client | None = None,
) -> list[AlertChannel]:
    """The in-app feed is always on. Telegram exists only when its flag is on; then missing
    credentials are a startup error (``ChannelConfigError``), not a failure at the first alert.
    With the flag off nothing about Telegram is created: no client, no token needed, no calls."""
    flags = flags or FeatureFlags.load()
    channels: list[AlertChannel] = [InAppChannel()]
    if flags.is_enabled("telegram_enabled"):
        channels.append(TelegramChannel(telegram_client(env, http_client)))
    return channels

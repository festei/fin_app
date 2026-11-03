"""The always-on worker: Telegram command bot (only when the flag is on), delivery retries, and a
single-instance guarantee.

Not built yet: intraday price polling and incremental re-scoring (the next stage). Scoring itself
runs as a scheduled Prefect flow (``fin_flows.scoring``); this process handles what must be
always on.

* Exactly one instance may be active: on Postgres a session-level advisory lock is held for the
  life of the process and re-verified every round (it is lost if the database restarts); a second
  instance exits instead of double-sending, and a worker that loses leadership stops itself.
* Channel configuration is validated at startup (``ChannelConfigError``): Telegram switched on
  without credentials stops the worker loudly instead of failing at the first alert.
* Without a database it only logs a heartbeat (bare dev runs).
* ``SIGTERM`` stops it cleanly (the bot's long poll is short, so shutdown is quick).

Everything it does is a use case from ``fin_bootstrap.Container``; this module only runs them on
a timer.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from fin_application.errors import ChannelConfigError, InstanceAlreadyRunning
from fin_application.ports import AlertChannel, SingleInstanceGuard
from fin_bootstrap import Container, FeatureFlags, Settings

log = logging.getLogger("fin_worker")

BOT_POLL_TIMEOUT = 10  # seconds of long polling per round; also bounds shutdown latency
BOT_ERROR_BACKOFF = 5.0

WorkerAlreadyRunningError = InstanceAlreadyRunning  # the name this error has always had here


@dataclass
class WorkerContext:
    container: Container
    channels: list[AlertChannel]
    bot: object | None = None  # the Telegram bot, when the channel is on
    lock: SingleInstanceGuard | None = field(default=None, repr=False)
    exit_code: int = 0  # non-zero when the worker stopped itself (e.g. lost leadership)

    def deliver(self) -> int:
        """Send every delivery that is due (new, or failed and past its backoff)."""
        return self.container.deliver_due_alerts.execute(channels=self.channels)


def build_context(
    flags: FeatureFlags | None = None,
    env: Mapping[str, str] | None = None,
    http_client=None,
) -> WorkerContext | None:
    """Wire the worker from configuration, or ``None`` when no database is configured.
    Raises ``ChannelConfigError`` if a switched-on channel lacks its credentials. ``env``
    overrides the Telegram credentials only (the database always comes from the process
    environment)."""
    settings = Settings.from_environ()
    if not settings.database_configured:
        return None
    if env is not None:
        settings = replace(
            settings,
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID") or None,
        )
    container = Container(settings, flags=flags, http_client=http_client)
    channels = container.channels()
    return WorkerContext(container, channels, bot=container.telegram_bot(channels))


async def _pause(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _bot_loop(bot, stop: asyncio.Event) -> None:
    offset: int | None = None
    try:  # resume where the last run stopped, so handled commands are never replayed
        offset = await asyncio.to_thread(bot.load_offset)
    except Exception as exc:
        log.warning("could not load the telegram offset (%s); starting fresh", type(exc).__name__)
    while not stop.is_set():
        try:
            offset = await asyncio.to_thread(bot.poll_once, offset, BOT_POLL_TIMEOUT)
        except Exception as exc:  # network/API trouble must not kill the worker
            log.warning("telegram poll failed (%s); retrying", type(exc).__name__)
            await _pause(stop, BOT_ERROR_BACKOFF)


async def run(
    stop: asyncio.Event, interval: float = 60.0, ctx: WorkerContext | None = None
) -> None:
    names = [c.name for c in ctx.channels] if ctx else []
    features = ctx.container.flags.as_dict() if ctx else {}
    log.info("worker started; features=%s channels=%s", features, names)
    bot_task = asyncio.create_task(_bot_loop(ctx.bot, stop)) if ctx and ctx.bot else None
    try:
        while not stop.is_set():
            if ctx is not None and ctx.lock is not None:
                try:
                    await asyncio.to_thread(ctx.lock.ensure)
                except InstanceAlreadyRunning as exc:
                    log.error("%s; stopping so only one instance sends alerts", exc)
                    ctx.exit_code = 3
                    stop.set()
                    break
                except Exception as exc:  # the database is down: keep running, try next round
                    log.warning("could not verify the leader lock (%s)", type(exc).__name__)
            if ctx is None:
                log.info("heartbeat")
            else:
                try:
                    sent = await asyncio.to_thread(ctx.deliver)
                    if sent:
                        log.info("re-sent %d failed deliveries", sent)
                except Exception as exc:
                    log.warning("delivery retry failed (%s)", type(exc).__name__)
            await _pause(stop, interval)
    finally:
        if bot_task is not None:
            await asyncio.gather(bot_task, return_exceptions=True)
    log.info("worker stopped cleanly")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        ctx = build_context()
    except ChannelConfigError as exc:
        log.error("invalid alert channel configuration: %s", exc)
        raise SystemExit(2) from None
    if ctx is None:
        log.warning("no database configured: heartbeat only")
    else:
        ctx.lock = ctx.container.leader_lock
        try:
            ctx.lock.acquire()
        except InstanceAlreadyRunning as exc:
            log.error("%s; exiting", exc)
            raise SystemExit(3) from None

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(stop, ctx=ctx)

    try:
        asyncio.run(_main())
    finally:
        if ctx is not None:
            if ctx.lock is not None:
                ctx.lock.release()
            ctx.container.dispose()
    if ctx is not None and ctx.exit_code:
        raise SystemExit(ctx.exit_code)


if __name__ == "__main__":
    main()

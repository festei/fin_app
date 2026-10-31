"""The always-on worker: Telegram command bot (only when the flag is on), delivery retries, and a
single-instance guarantee.

Not built yet: intraday price polling and incremental re-scoring (the next stage). Scoring itself
runs as a scheduled Prefect flow (``fin_flows.scoring``); this process handles what must be
always on.

* Exactly one instance may be active: on Postgres a session-level advisory lock is held for the
  life of the process; a second instance exits instead of double-sending.
* Channel configuration is validated at startup (``ChannelConfigError``): Telegram switched on
  without credentials stops the worker loudly instead of failing at the first alert.
* Without a database it only logs a heartbeat (bare dev runs).
* ``SIGTERM`` stops it cleanly (the bot's long poll is short, so shutdown is quick).
"""

from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass, field

from sqlalchemy import Connection, Engine, text

from fin_core.alerts.base import AlertChannel, ChannelConfigError
from fin_core.alerts.channels import build_channels
from fin_core.alerts.pipeline import retry_failed
from fin_core.alerts.policy import AlertPolicy
from fin_core.alerts.telegram import TelegramBot, TelegramChannel
from fin_core.db.session import database_configured, make_engine
from fin_core.features import FeatureFlags

log = logging.getLogger("fin_worker")

LEADER_LOCK_KEY = 7_301_001  # arbitrary, fixed: identifies "the fin-app worker" in pg_locks
BOT_POLL_TIMEOUT = 10  # seconds of long polling per round; also bounds shutdown latency
BOT_ERROR_BACKOFF = 5.0


class WorkerAlreadyRunningError(RuntimeError):
    """Another worker instance holds the leader lock."""


@dataclass
class WorkerContext:
    engine: Engine
    channels: list[AlertChannel]
    policy: AlertPolicy
    bot: TelegramBot | None = None
    lock: Connection | None = field(default=None, repr=False)


def build_context(flags: FeatureFlags | None = None, env=None, http_client=None):
    """Wire the worker from configuration, or ``None`` when no database is configured.
    Raises ``ChannelConfigError`` if a switched-on channel lacks its credentials."""
    if not database_configured():
        return None
    engine = make_engine()
    channels = build_channels(flags or FeatureFlags.load(), env, http_client)
    bot = next(
        (TelegramBot(c.client, engine) for c in channels if isinstance(c, TelegramChannel)), None
    )
    return WorkerContext(engine=engine, channels=channels, policy=AlertPolicy.load(), bot=bot)


def acquire_leader_lock(engine: Engine) -> Connection | None:
    """Take the single-instance lock. Postgres only; other databases (SQLite in dev/tests) have
    no cross-process advisory locks, so this is a no-op returning ``None``."""
    if engine.dialect.name != "postgresql":
        return None
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LEADER_LOCK_KEY}).scalar()
    if not got:
        conn.close()
        raise WorkerAlreadyRunningError("another worker instance holds the leader lock")
    return conn


def release_leader_lock(conn: Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LEADER_LOCK_KEY})
    finally:
        conn.close()


async def _pause(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _bot_loop(bot: TelegramBot, stop: asyncio.Event) -> None:
    offset: int | None = None
    while not stop.is_set():
        try:
            offset = await asyncio.to_thread(bot.poll_once, offset, BOT_POLL_TIMEOUT)
        except Exception as exc:  # network/API trouble must not kill the worker
            log.warning("telegram poll failed (%s); retrying", type(exc).__name__)
            await _pause(stop, BOT_ERROR_BACKOFF)


async def run(
    stop: asyncio.Event, interval: float = 60.0, ctx: WorkerContext | None = None
) -> None:
    flags = FeatureFlags.load()
    names = [c.name for c in ctx.channels] if ctx else []
    log.info("worker started; features=%s channels=%s", flags.as_dict(), names)
    bot_task = asyncio.create_task(_bot_loop(ctx.bot, stop)) if ctx and ctx.bot else None
    try:
        while not stop.is_set():
            if ctx is None:
                log.info("heartbeat")
            else:
                try:
                    sent = await asyncio.to_thread(
                        retry_failed, ctx.engine, ctx.channels, ctx.policy
                    )
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
        try:
            ctx.lock = acquire_leader_lock(ctx.engine)
        except WorkerAlreadyRunningError as exc:
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
            release_leader_lock(ctx.lock)
            ctx.engine.dispose()


if __name__ == "__main__":
    main()

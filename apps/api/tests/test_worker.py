import asyncio

from fin_api.worker import run


async def test_worker_stops_cleanly_on_event():
    stop = asyncio.Event()
    task = asyncio.create_task(run(stop, interval=0.05))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)


# --- alert channels, the Telegram bot and the single-instance lock ---------------------------

import os  # noqa: E402

import pytest  # noqa: E402

from fin_api import worker  # noqa: E402
from fin_core.alerts.base import ChannelConfigError  # noqa: E402
from fin_core.alerts.channels import InAppChannel  # noqa: E402
from fin_core.alerts.policy import AlertPolicy  # noqa: E402
from fin_core.alerts.telegram import TelegramBot  # noqa: E402
from fin_core.db import migrate  # noqa: E402
from fin_core.db.session import make_engine  # noqa: E402
from fin_core.features import FeatureFlags  # noqa: E402

CREDS = {"TELEGRAM_BOT_TOKEN": "123:SECRET", "TELEGRAM_CHAT_ID": "42"}


class FakeBot:
    def __init__(self, fail_first=False):
        self.offsets, self.fail_first = [], fail_first

    def poll_once(self, offset, timeout):
        self.offsets.append(offset)
        if self.fail_first and len(self.offsets) == 1:
            raise RuntimeError("network down")
        return (offset or 0) + 1


@pytest.fixture
def sqlite_url(monkeypatch, tmp_path):
    for k in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    url = f"sqlite:///{tmp_path / 'worker.db'}"
    migrate.upgrade(url)
    monkeypatch.setenv("DATABASE_URL", url)
    return url


async def run_for(ctx, seconds=0.3, interval=0.05):
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop, interval=interval, ctx=ctx))
    await asyncio.sleep(seconds)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_worker_polls_the_bot_retries_deliveries_and_stops_cleanly(sqlite_url, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "retry_failed", lambda *a, **k: calls.append(1) or 0)
    bot = FakeBot()
    ctx = worker.WorkerContext(make_engine(sqlite_url), [InAppChannel()], AlertPolicy.load(), bot)
    await run_for(ctx)
    assert len(bot.offsets) >= 2 and bot.offsets[:3] == [None, 1, 2]  # offsets move forward
    assert len(calls) >= 2  # delivery retries run on the interval


async def test_a_failing_bot_poll_does_not_kill_the_worker(sqlite_url, monkeypatch):
    monkeypatch.setattr(worker, "BOT_ERROR_BACKOFF", 0.01)
    monkeypatch.setattr(worker, "retry_failed", lambda *a, **k: 0)
    bot = FakeBot(fail_first=True)
    ctx = worker.WorkerContext(make_engine(sqlite_url), [InAppChannel()], AlertPolicy.load(), bot)
    await run_for(ctx)
    assert len(bot.offsets) >= 2  # it polled again after the failure


async def test_a_failing_retry_does_not_kill_the_worker(sqlite_url, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(worker, "retry_failed", boom)
    ctx = worker.WorkerContext(make_engine(sqlite_url), [InAppChannel()], AlertPolicy.load())
    await run_for(ctx)  # completes without raising


def test_no_database_means_no_context(monkeypatch):
    for k in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    assert worker.build_context() is None


def test_flag_off_builds_only_the_feed_and_no_bot(sqlite_url):
    ctx = worker.build_context(FeatureFlags({}), env={})
    assert [c.name for c in ctx.channels] == ["in_app"] and ctx.bot is None


def test_flag_on_with_credentials_builds_the_bot(sqlite_url):
    ctx = worker.build_context(FeatureFlags({"telegram_enabled": True}), env=CREDS)
    assert [c.name for c in ctx.channels] == ["in_app", "telegram"]
    assert isinstance(ctx.bot, TelegramBot)


def test_flag_on_without_credentials_stops_the_worker_at_startup(sqlite_url):
    with pytest.raises(ChannelConfigError):
        worker.build_context(FeatureFlags({"telegram_enabled": True}), env={})


def test_main_exits_with_status_2_on_a_bad_channel_configuration(sqlite_url, monkeypatch):
    monkeypatch.setenv("FEATURE_TELEGRAM_ENABLED", "true")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 2


def test_the_leader_lock_is_a_noop_on_sqlite(sqlite_url):
    assert worker.acquire_leader_lock(make_engine(sqlite_url)) is None
    worker.release_leader_lock(None)  # nothing to release


@pytest.mark.postgres
def test_only_one_worker_can_hold_the_leader_lock_on_postgres():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    first_engine, second_engine = make_engine(url), make_engine(url)
    first = worker.acquire_leader_lock(first_engine)
    try:
        with pytest.raises(worker.WorkerAlreadyRunningError):
            worker.acquire_leader_lock(second_engine)
    finally:
        worker.release_leader_lock(first)
    second = worker.acquire_leader_lock(second_engine)  # free again after the release
    worker.release_leader_lock(second)
    first_engine.dispose()
    second_engine.dispose()

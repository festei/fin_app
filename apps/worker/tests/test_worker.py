import asyncio

from fin_worker.worker import run


async def test_worker_stops_cleanly_on_event():
    stop = asyncio.Event()
    task = asyncio.create_task(run(stop, interval=0.05))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)


# --- alert channels, the Telegram bot and the single-instance lock ---------------------------

import os  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from fin_application.alerts import InAppChannel  # noqa: E402
from fin_application.errors import ChannelConfigError, InstanceAlreadyRunning  # noqa: E402
from fin_bootstrap import Container, Settings  # noqa: E402
from fin_config import FeatureFlags  # noqa: E402
from fin_persistence import make_engine, migrate  # noqa: E402
from fin_persistence.leader_lock import PgAdvisoryLeaderLock  # noqa: E402
from fin_telegram import TelegramBot  # noqa: E402
from fin_worker import worker  # noqa: E402

CREDS = {"TELEGRAM_BOT_TOKEN": "123:SECRET", "TELEGRAM_CHAT_ID": "42"}


class FakeBot:
    def __init__(self, fail_first=False, persisted=None):
        self.offsets, self.fail_first, self.persisted = [], fail_first, persisted

    def load_offset(self):
        return self.persisted

    def poll_once(self, offset, timeout):
        self.offsets.append(offset)
        if self.fail_first and len(self.offsets) == 1:
            raise RuntimeError("network down")
        return (offset or 0) + 1


def make_ctx(url, bot=None, lock=None):
    return worker.WorkerContext(
        Container(Settings(database_url=url)), [InAppChannel()], bot=bot, lock=lock
    )


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
    monkeypatch.setattr(worker.WorkerContext, "deliver", lambda self: calls.append(1) or 0)
    bot = FakeBot()
    ctx = make_ctx(sqlite_url, bot=bot)
    await run_for(ctx)
    assert len(bot.offsets) >= 2 and bot.offsets[:3] == [None, 1, 2]  # offsets move forward
    assert len(calls) >= 2  # delivery retries run on the interval


async def test_a_failing_bot_poll_does_not_kill_the_worker(sqlite_url, monkeypatch):
    monkeypatch.setattr(worker, "BOT_ERROR_BACKOFF", 0.01)
    monkeypatch.setattr(worker.WorkerContext, "deliver", lambda self: 0)
    bot = FakeBot(fail_first=True)
    ctx = make_ctx(sqlite_url, bot=bot)
    await run_for(ctx)
    assert len(bot.offsets) >= 2  # it polled again after the failure


async def test_a_failing_retry_does_not_kill_the_worker(sqlite_url, monkeypatch):
    def boom(self):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(worker.WorkerContext, "deliver", boom)
    ctx = make_ctx(sqlite_url)
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
    lock = PgAdvisoryLeaderLock(make_engine(sqlite_url))
    lock.acquire()
    assert lock.active is False and lock.ensure() is True
    lock.release()  # nothing to release


async def test_a_worker_that_loses_leadership_stops_itself_with_status_3(sqlite_url, monkeypatch):
    monkeypatch.setattr(worker.WorkerContext, "deliver", lambda self: 0)

    class Usurped:
        def ensure(self):
            raise InstanceAlreadyRunning("another worker instance holds the leader lock")

    ctx = make_ctx(sqlite_url, lock=Usurped())
    stop = asyncio.Event()
    await asyncio.wait_for(worker.run(stop, interval=0.05, ctx=ctx), timeout=2)
    assert stop.is_set() and ctx.exit_code == 3  # it stopped on its own


async def test_a_database_outage_while_checking_the_lock_does_not_stop_the_worker(
    sqlite_url, monkeypatch
):
    monkeypatch.setattr(worker.WorkerContext, "deliver", lambda self: 0)

    class Flaky:
        calls = 0

        def ensure(self):
            Flaky.calls += 1
            raise ConnectionError("db down")

    ctx = make_ctx(sqlite_url, lock=Flaky())
    await run_for(ctx)
    assert Flaky.calls >= 2 and ctx.exit_code == 0  # kept running and kept checking


def _postgres_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


@pytest.mark.postgres
def test_only_one_worker_can_hold_the_leader_lock_on_postgres():
    url = _postgres_url()
    e1, e2 = make_engine(url), make_engine(url)
    first, second = PgAdvisoryLeaderLock(e1), PgAdvisoryLeaderLock(e2)
    first.acquire()
    try:
        with pytest.raises(InstanceAlreadyRunning):
            second.acquire()
        assert first.ensure() is True  # still held
    finally:
        first.release()
    second.acquire()  # free again after the release
    second.release()
    e1.dispose()
    e2.dispose()


@pytest.mark.postgres
def test_a_lost_lock_is_noticed_and_reacquired_or_the_worker_gives_up():
    url = _postgres_url()
    e1, e2, admin = make_engine(url), make_engine(url), make_engine(url)
    first, rival = PgAdvisoryLeaderLock(e1), PgAdvisoryLeaderLock(e2)
    first.acquire()

    def kill_first_connection():  # what a database restart does to the lock's connection
        pid = first._conn.execute(text("SELECT pg_backend_pid()")).scalar()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})

    try:
        kill_first_connection()
        assert first.ensure() is False  # lost, nobody else took it: re-acquired
        assert first.ensure() is True  # and held again
        kill_first_connection()
        rival.acquire()  # another instance starts in the gap
        with pytest.raises(InstanceAlreadyRunning):
            first.ensure()  # the original must give up
    finally:
        first.release()
        rival.release()
        for e in (e1, e2, admin):
            e.dispose()


def test_importing_the_entry_module_does_not_start_the_worker(monkeypatch):
    import importlib
    import runpy
    import sys

    calls = []
    monkeypatch.setattr("fin_worker.worker.main", lambda: calls.append("main"))
    sys.modules.pop("fin_worker.__main__", None)
    importlib.import_module("fin_worker.__main__")
    assert calls == []  # importing is not running
    sys.modules.pop("fin_worker.__main__", None)
    runpy.run_module("fin_worker", run_name="__main__")
    assert calls == ["main"]  # `python -m fin_worker` still does

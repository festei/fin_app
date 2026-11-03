"""Delivery guarantees that only a real database can prove: no transaction is held open while a
channel sends, and concurrent senders deliver exactly once."""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from fin_application.ports import AlertMessage
from fin_bootstrap import Container, Settings
from fin_domain.market_data import SecurityUpdate
from fin_domain.shared import Ticker
from fin_domain.signals import Alert, ScoreEntry, Signal
from fin_persistence import migrate

NOW = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)
AAA = Ticker("AAA")


class Chan:
    name = "chan"

    def __init__(self, on_send=None):
        self.on_send, self.sent = on_send, []

    def send(self, message: AlertMessage) -> None:
        if self.on_send:
            self.on_send(message)
        self.sent.append(message.alert_id)


def pending_alert(container) -> int:
    """One stored alert with a never-tried delivery on ``chan``."""
    with container.uow_factory() as uow:
        uow.securities.upsert([SecurityUpdate(AAA)])
        score = uow.scores.add(ScoreEntry(AAA, NOW, 80.0, False, False, "x", computed_at=NOW))
        signal = uow.signals.add(AAA, score.id, Signal("AAA", "buy", 80.0, NOW, ""))
        alert = Alert(AAA, "buy", "high", 80.0, "t", "b", NOW, signal.id)
        alert.open_deliveries_for(["chan"], muted=set(), now=NOW)
        return uow.alerts.add(alert).id


def deliveries(container):
    with container.engine.connect() as conn:
        return [
            tuple(r) for r in conn.execute(text("select status, attempts from alert_deliveries"))
        ]


@pytest.fixture
def sqlite_container(tmp_path):
    url = f"sqlite:///{tmp_path / 'delivery.db'}"
    migrate.upgrade(url)
    c = Container(Settings(database_url=url))
    yield c
    c.dispose()


@pytest.fixture
def postgres_container():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    c = Container(Settings(database_url=url))
    with c.engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    migrate.upgrade(url)
    yield c
    c.dispose()


def test_nothing_is_held_open_while_a_channel_sends(sqlite_container):
    """SQLite allows one writer: if the delivery pass kept a transaction open across the send, a
    write from another connection inside ``send`` would fail with "database is locked"."""
    c = sqlite_container
    pending_alert(c)
    seen = {}

    def write_meanwhile(message):
        with c.uow_factory() as other:  # a second connection, written to and committed
            other.watchlist.add(AAA)
        seen["delivery"] = deliveries(c)

    chan = Chan(on_send=write_meanwhile)
    assert c.deliver_due_alerts.execute(NOW, channels=[chan]) == 1
    assert seen["delivery"] == [("pending", 1)]  # claimed and committed before the send
    assert deliveries(c) == [("sent", 1)]


@pytest.mark.postgres
def test_concurrent_senders_deliver_exactly_once(postgres_container):
    c = postgres_container
    pending_alert(c)
    chan = Chan(on_send=lambda m: threading.Event().wait(0.2))  # widen the race window
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        c.deliver_due_alerts.execute(NOW, channels=[chan])

    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(chan.sent) == 1 and deliveries(c) == [("sent", 1)]


def test_a_retry_after_the_backoff_goes_through_the_real_database(sqlite_container):
    c = sqlite_container
    pending_alert(c)

    class Down(Chan):
        def send(self, message):
            raise RuntimeError("secret in the message")

    assert c.deliver_due_alerts.execute(NOW, channels=[Down()]) == 0
    assert deliveries(c) == [("failed", 1)]
    chan = Chan()
    assert c.deliver_due_alerts.execute(NOW + timedelta(minutes=1), channels=[chan]) == 0  # backoff
    assert c.deliver_due_alerts.execute(NOW + timedelta(minutes=3), channels=[chan]) == 1
    assert deliveries(c) == [("sent", 2)]

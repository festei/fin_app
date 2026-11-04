"""What the unit of work does with database errors.

A constraint violation, a missing table and a real outage must not read the same: only an outage
is ``StorageUnavailable`` (the API answers 503); everything else is ``StorageError`` (500). The
original error, which can hold SQL, parameters and connection details, goes to the log with its
traceback and never into the message callers see.
"""

from __future__ import annotations

import logging

import pytest
from db_fixtures import migrated_engine  # noqa: F401 - pytest fixture
from sqlalchemy import text
from sqlalchemy.exc import DataError, InterfaceError, OperationalError, ProgrammingError

from fin_application.errors import StorageError, StorageUnavailable
from fin_persistence import migrate
from fin_persistence.session import make_engine, ping
from fin_persistence.storage_errors import translate_storage_error
from fin_persistence.tables import Security
from fin_persistence.unit_of_work import SqlUnitOfWork


@pytest.fixture
def engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'storage.db'}"
    migrate.upgrade(url)
    eng = make_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def unreachable(tmp_path):
    """A SQLite database in a directory that does not exist: opening it fails like an outage."""
    eng = make_engine(f"sqlite:///{tmp_path / 'no-such-dir' / 'x.db'}")
    yield eng
    eng.dispose()


def logged_errors(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


def logged_problems(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def traceback_text(record):
    return "".join(logging.Formatter().formatException(record.exc_info).splitlines())


def test_a_constraint_violation_is_a_storage_error_not_an_outage(engine, caplog):
    caplog.set_level(logging.ERROR)
    with pytest.raises(StorageError) as raised:
        with SqlUnitOfWork(engine) as uow:
            uow.session.add_all([Security(ticker="DUP"), Security(ticker="DUP")])
    assert not isinstance(raised.value, StorageUnavailable)
    message = str(raised.value)
    assert message.startswith("IntegrityError: ") and "securities.ticker" in message
    assert "INSERT" not in message and "parameters" not in message  # no SQL, no values
    assert isinstance(raised.value.__cause__, Exception)  # the original stays chained

    (record,) = logged_errors(caplog)  # and the original is logged with its traceback
    assert record.exc_info is not None
    assert record.levelno == logging.ERROR
    assert "UNIQUE constraint failed" in traceback_text(record)


def test_a_missing_table_is_a_storage_error_on_postgres_where_the_driver_says_so(migrated_engine):
    """SQLite reports a missing table as an OperationalError (so: unavailable); Postgres, the
    database production runs on, as a ProgrammingError, so a skipped migration reads as a
    deployment problem and not as an outage."""
    with pytest.raises((StorageError, StorageUnavailable)) as raised:
        with SqlUnitOfWork(migrated_engine) as uow:
            uow.session.execute(text("SELECT * FROM no_such_table"))
    assert "SELECT" not in str(raised.value)  # never the statement
    expected = StorageError if migrated_engine.dialect.name == "postgresql" else StorageUnavailable
    assert type(raised.value) is expected


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            OperationalError("SELECT 1", {}, Exception("could not connect to server")),
            StorageUnavailable,
        ),
        (
            OperationalError("UPDATE x", {"a": 1}, Exception("deadlock detected")),
            StorageUnavailable,
        ),
        (
            InterfaceError("SELECT 1", {}, Exception("connection already closed")),
            StorageUnavailable,
        ),
        (
            ProgrammingError("SELECT * FROM t", {}, Exception('relation "t" does not exist')),
            StorageError,
        ),
        (DataError("INSERT INTO t", {"v": "secret"}, Exception("value too long")), StorageError),
    ],
)
def test_the_two_kinds_by_error_class_and_the_message_never_holds_sql_or_parameters(
    original, expected
):
    translated = translate_storage_error(original)
    assert type(translated) is expected
    assert not any(word in str(translated) for word in ("SELECT", "INSERT", "UPDATE", "secret"))


def test_an_unreachable_database_is_one_warning_line_without_a_traceback(unreachable, caplog):
    """An outage fails every probe and request the same way: one line each, not a traceback."""
    caplog.set_level(logging.WARNING)
    with pytest.raises(StorageUnavailable) as raised:
        with SqlUnitOfWork(unreachable) as uow:
            uow.session.execute(text("SELECT 1"))
    assert str(raised.value) == "database unavailable (OperationalError)"
    (record,) = logged_problems(caplog)
    assert record.levelno == logging.WARNING and record.exc_info is None
    assert "unable to open database file" in record.getMessage()  # the driver's own reason
    assert "unable to open database file" not in str(raised.value)


def test_the_ping_is_one_warning_line_and_returns_only_the_kind(unreachable, caplog):
    caplog.set_level(logging.WARNING)
    with pytest.raises(StorageUnavailable) as raised:
        ping(unreachable)
    assert str(raised.value) == "database unavailable (OperationalError)"
    (record,) = logged_problems(caplog)
    assert record.levelno == logging.WARNING and record.exc_info is None


def test_other_exceptions_pass_through_untouched_and_the_transaction_rolls_back(engine):
    with pytest.raises(ValueError, match="boom"):
        with SqlUnitOfWork(engine) as uow:
            uow.session.add(Security(ticker="GONE"))
            uow.session.flush()
            raise ValueError("boom")
    with SqlUnitOfWork(engine) as uow:
        assert uow.session.query(Security).count() == 0

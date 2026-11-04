from datetime import UTC, datetime, timedelta, timezone

import pytest

from fin_domain.shared import (
    STALE_AFTER,
    DomainEvent,
    InvariantViolation,
    JobRun,
    NotFound,
    RunStatus,
    Ticker,
    derive_status,
    ensure_utc,
    summarise_failures,
)

NOW = datetime(2026, 3, 2, 21, 30, tzinfo=UTC)


# --- Ticker -------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["aapl", " AAPL ", "brk-b", "BF.B"])
def test_ticker_is_normalised(raw):
    assert str(Ticker(raw)) == raw.strip().upper()


@pytest.mark.parametrize("raw", ["", "  ", "-A", "A B", "WAYTOOLONGTICKER123", "A/B", "AAPL;"])
def test_ticker_rejects_what_is_not_a_symbol(raw):
    with pytest.raises(InvariantViolation):
        Ticker(raw)


def test_tickers_with_the_same_symbol_are_equal_and_hashable():
    assert Ticker("ibm") == Ticker("IBM")
    assert len({Ticker("ibm"), Ticker("IBM")}) == 1
    assert sorted([Ticker("MSFT"), Ticker("AAPL")]) == [Ticker("AAPL"), Ticker("MSFT")]


# --- time and events ----------------------------------------------------------------------------


def test_naive_datetimes_are_rejected_not_guessed():
    with pytest.raises(InvariantViolation):
        ensure_utc(datetime(2026, 1, 1))  # noqa: DTZ001 - the point of the test


def test_aware_datetimes_are_converted_to_utc():
    plus_two = timezone(timedelta(hours=2))
    assert ensure_utc(datetime(2026, 1, 1, 12, tzinfo=plus_two)) == datetime(
        2026, 1, 1, 10, tzinfo=UTC
    )


def test_events_need_an_aware_timestamp():
    assert DomainEvent(NOW).occurred_at == NOW
    with pytest.raises(InvariantViolation):
        DomainEvent(datetime(2026, 1, 1))  # noqa: DTZ001


def test_not_found_names_what_is_missing():
    err = NotFound("security", "ZZZ")
    assert (err.entity, err.key) == ("security", "ZZZ")
    assert "ZZZ" in str(err)


# --- status derivation (moved from ingest.run_status) ---------------------------------------------


@pytest.mark.parametrize(
    ("failures", "total", "aborted", "expected"),
    [
        (0, 10, False, RunStatus.OK),
        (3, 10, False, RunStatus.PARTIAL),
        (10, 10, False, RunStatus.FAILED),  # every item failed
        (11, 10, False, RunStatus.FAILED),
        (0, 10, True, RunStatus.FAILED),  # a rate-limit abort is a failure even with no item failed
        (0, 0, False, RunStatus.OK),  # nothing to do is fine
        (1, 0, False, RunStatus.FAILED),
    ],
)
def test_derive_status(failures, total, aborted, expected):
    assert derive_status(failures, total, aborted) is expected


def test_failure_summary_is_cut_to_the_stored_length():
    assert summarise_failures({}) is None
    assert summarise_failures({"AAPL": "boom", "MSFT": "bang"}) == "AAPL: boom; MSFT: bang"
    assert len(summarise_failures({"X": "y" * 900})) == 500


# --- JobRun -------------------------------------------------------------------------------------


def test_a_new_run_is_running():
    run = JobRun.start("prices", NOW)
    assert run.is_running
    assert (run.rows, run.error, run.finished_at, run.id) == (0, None, None, None)


@pytest.mark.parametrize("source", ["", "x" * 33])
def test_source_must_fit_the_record(source):
    with pytest.raises(InvariantViolation):
        JobRun.start(source, NOW)


def test_finishing_records_status_rows_and_failures():
    run = JobRun.start("prices", NOW)
    later = NOW + timedelta(minutes=3)
    run.finish(RunStatus.PARTIAL, 120, {"AAPL": "timeout"}, later)
    assert (run.status, run.rows, run.error, run.finished_at) == (
        RunStatus.PARTIAL,
        120,
        "AAPL: timeout",
        later,
    )


def test_a_finished_run_never_changes_again():
    run = JobRun.start("prices", NOW)
    run.finish(RunStatus.OK, 1, {}, NOW)
    with pytest.raises(InvariantViolation):
        run.finish(RunStatus.FAILED, 1, {}, NOW)
    with pytest.raises(InvariantViolation):
        run.fail_stale(NOW)


def test_a_run_cannot_finish_as_running_or_with_negative_rows():
    run = JobRun.start("prices", NOW)
    with pytest.raises(InvariantViolation):
        run.finish(RunStatus.RUNNING, 0, {}, NOW)
    with pytest.raises(InvariantViolation):
        run.finish(RunStatus.OK, -1, {}, NOW)


def test_staleness_needs_a_running_run_older_than_the_limit():
    run = JobRun.start("scoring", NOW)
    assert not run.is_stale(NOW + STALE_AFTER)  # exactly at the limit is not yet stale
    assert run.is_stale(NOW + STALE_AFTER + timedelta(seconds=1))
    run.finish(RunStatus.OK, 0, {}, NOW)
    assert not run.is_stale(NOW + timedelta(days=30))


def test_a_stale_run_is_failed_with_a_reason():
    run = JobRun.start("scoring", NOW)
    later = NOW + timedelta(hours=7)
    run.fail_stale(later)
    assert run.status is RunStatus.FAILED
    assert run.error.startswith("stale:")
    assert run.finished_at == later


def test_a_run_started_with_a_naive_time_is_rejected():
    with pytest.raises(InvariantViolation):
        JobRun.start("prices", datetime(2026, 3, 2))  # noqa: DTZ001

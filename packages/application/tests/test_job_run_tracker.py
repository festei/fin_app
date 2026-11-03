from datetime import timedelta

import pytest

from fin_application.job_runs import JobRunTracker
from fin_domain.shared import STALE_AFTER, InvariantViolation, NotFound, RunStatus
from fin_testkit import FakeClock, InMemoryStore, in_memory_uow_factory


@pytest.fixture
def world():
    store, clock = InMemoryStore(), FakeClock()
    return store, clock, JobRunTracker(in_memory_uow_factory(store), clock)


def test_begin_records_a_running_run(world):
    store, clock, tracker = world
    run_id = tracker.begin("prices")
    run = store.job_runs[run_id]
    assert (run.source, run.status, run.started_at) == ("prices", RunStatus.RUNNING, clock.now())


def test_finish_records_the_outcome(world):
    store, clock, tracker = world
    run_id = tracker.begin("prices")
    clock.advance(timedelta(minutes=2))
    tracker.finish(run_id, RunStatus.PARTIAL, 40, {"AAPL": "timeout"})
    run = store.job_runs[run_id]
    assert (run.status, run.rows, run.error) == (RunStatus.PARTIAL, 40, "AAPL: timeout")
    assert run.finished_at == clock.now()


def test_finishing_twice_is_refused(world):
    _, _, tracker = world
    run_id = tracker.begin("prices")
    tracker.finish(run_id, RunStatus.OK, 1, {})
    with pytest.raises(InvariantViolation):
        tracker.finish(run_id, RunStatus.FAILED, 1, {})


def test_finishing_an_unknown_run_is_not_found(world):
    _, _, tracker = world
    with pytest.raises(NotFound):
        tracker.finish(999, RunStatus.OK, 0, {})


def test_beginning_a_run_fails_runs_whose_process_died(world):
    store, clock, tracker = world
    dead = tracker.begin("prices")
    clock.advance(STALE_AFTER + timedelta(minutes=1))
    fresh = tracker.begin("scoring")
    assert store.job_runs[dead].status is RunStatus.FAILED
    assert store.job_runs[dead].error.startswith("stale:")
    assert store.job_runs[fresh].status is RunStatus.RUNNING


def test_a_recent_running_run_is_left_alone(world):
    store, clock, tracker = world
    other = tracker.begin("prices")
    clock.advance(timedelta(hours=1))
    assert tracker.fail_stale() == 0
    assert store.job_runs[other].status is RunStatus.RUNNING


def test_a_failure_inside_a_transaction_changes_nothing(world):
    store, _, tracker = world
    run_id = tracker.begin("prices")
    with pytest.raises(InvariantViolation):
        tracker.finish(run_id, RunStatus.RUNNING, 0, {})  # illegal: the unit of work rolls back
    assert store.job_runs[run_id].status is RunStatus.RUNNING

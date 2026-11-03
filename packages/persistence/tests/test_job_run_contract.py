"""One contract for ``UnitOfWork`` + ``JobRunRepository``, run against the in-memory fake and the
SQL adapter, so the fake cannot drift from the real thing."""

from datetime import UTC, datetime, timedelta

import pytest
from db_fixtures import uow_factory  # noqa: F401 - pytest fixture

from fin_domain.shared import JobRun, RunStatus

T0 = datetime(2026, 3, 2, 21, 30, tzinfo=UTC)


def add_run(uow_factory, source="prices", started=T0) -> JobRun:
    with uow_factory() as uow:
        return uow.job_runs.add(JobRun.start(source, started))


def test_add_assigns_an_id_and_the_run_can_be_read_back(uow_factory):
    run = add_run(uow_factory)
    assert run.id is not None
    with uow_factory() as uow:
        assert uow.job_runs.get(run.id) == run


def test_ids_are_distinct(uow_factory):
    assert add_run(uow_factory).id != add_run(uow_factory).id


def test_an_unknown_run_is_none(uow_factory):
    with uow_factory() as uow:
        assert uow.job_runs.get(424242) is None


def test_changes_are_stored_only_by_save(uow_factory):
    run = add_run(uow_factory)
    with uow_factory() as uow:
        loaded = uow.job_runs.get(run.id)
        loaded.finish(RunStatus.OK, 5, {}, T0 + timedelta(minutes=1))  # not saved
    with uow_factory() as uow:
        assert uow.job_runs.get(run.id).status is RunStatus.RUNNING
    with uow_factory() as uow:
        loaded = uow.job_runs.get(run.id)
        loaded.finish(RunStatus.PARTIAL, 5, {"AAPL": "boom"}, T0 + timedelta(minutes=1))
        uow.job_runs.save(loaded)
    with uow_factory() as uow:
        stored = uow.job_runs.get(run.id)
    assert (stored.status, stored.rows, stored.error) == (RunStatus.PARTIAL, 5, "AAPL: boom")
    assert stored.finished_at == T0 + timedelta(minutes=1)


def test_saving_a_run_that_was_never_added_fails(uow_factory):
    ghost = JobRun.start("prices", T0)
    ghost.id = 777
    with uow_factory() as uow, pytest.raises(LookupError):
        uow.job_runs.save(ghost)


def test_times_come_back_as_aware_utc(uow_factory):
    run = add_run(uow_factory)
    with uow_factory() as uow:
        stored = uow.job_runs.get(run.id)
    assert stored.started_at == T0
    assert stored.started_at.utcoffset() == timedelta(0)


def test_find_running_started_before_only_returns_running_runs_that_are_old_enough(uow_factory):
    old = add_run(uow_factory, "prices", T0)
    recent = add_run(uow_factory, "yields", T0 + timedelta(hours=5))
    done = add_run(uow_factory, "scoring", T0)
    with uow_factory() as uow:
        finished = uow.job_runs.get(done.id)
        finished.finish(RunStatus.OK, 1, {}, T0 + timedelta(minutes=1))
        uow.job_runs.save(finished)
    with uow_factory() as uow:
        found = uow.job_runs.find_running_started_before(T0 + timedelta(hours=1))
    assert [r.id for r in found] == [old.id]
    assert recent.id not in [r.id for r in found]


def test_the_block_commits_on_success_and_rolls_back_on_error(uow_factory):
    with uow_factory() as uow:
        kept = uow.job_runs.add(JobRun.start("prices", T0))
    with pytest.raises(RuntimeError), uow_factory() as uow:
        lost = uow.job_runs.add(JobRun.start("yields", T0))
        raise RuntimeError("boom")
    with uow_factory() as uow:
        assert uow.job_runs.get(kept.id) is not None
        assert uow.job_runs.get(lost.id) is None

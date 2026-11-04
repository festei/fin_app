import pytest
from sqlalchemy import text

from fin_bootstrap import Container, DatabaseNotConfigured, Settings
from fin_domain.shared import RunStatus
from fin_persistence import make_engine, migrate
from fin_testkit import FakeClock


@pytest.fixture
def sqlite_url(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    migrate.upgrade(url)
    return url


# --- Settings -----------------------------------------------------------------------------------


def test_database_url_wins():
    s = Settings.from_environ({"DATABASE_URL": "sqlite:///x.db", "POSTGRES_USER": "u"})
    assert s.database_url == "sqlite:///x.db" and s.database_configured


def test_the_url_is_built_from_the_postgres_parts_with_quoting():
    s = Settings.from_environ(
        {"POSTGRES_USER": "fin", "POSTGRES_DB": "fin", "POSTGRES_PASSWORD": "p@ss/w:rd"}
    )
    assert s.database_url == "postgresql+psycopg://fin:p%40ss%2Fw%3Ard@localhost:5432/fin"


def test_user_and_db_are_both_required():
    assert not Settings.from_environ({"POSTGRES_USER": "fin"}).database_configured
    assert not Settings.from_environ({}).database_configured


# --- Container ----------------------------------------------------------------------------------


def test_a_container_without_a_database_says_so():
    container = Container(Settings())
    with pytest.raises(DatabaseNotConfigured):
        container.engine  # noqa: B018
    container.dispose()


def test_the_job_run_tracker_works_end_to_end_on_a_real_database(sqlite_url):
    clock = FakeClock()
    container = Container(Settings(database_url=sqlite_url), clock=clock)
    tracker = container.job_runs
    run_id = tracker.begin("prices")
    tracker.finish(run_id, RunStatus.OK, 12, {})
    with make_engine(sqlite_url).connect() as conn:
        row = conn.execute(
            text("select source, status, rows from ingestion_runs where id = :i"), {"i": run_id}
        ).one()
    assert tuple(row) == ("prices", "ok", 12)
    container.dispose()


def test_dispose_can_be_called_twice_and_the_container_reconnects(sqlite_url):
    container = Container(Settings(database_url=sqlite_url))
    first = container.engine
    container.dispose()
    container.dispose()
    assert container.engine is not first
    container.dispose()

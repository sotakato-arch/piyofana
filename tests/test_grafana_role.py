import psycopg
import pytest
from psycopg import errors

from collector.normalize import normalize
from collector.store import migrate, upsert_events
from tests.conftest import by_id

RO_PW = "ro-test-password"


@pytest.fixture
def ro(db, db_url, records):
    upsert_events(db, [normalize(by_id(records, "fx-006"))])
    migrate(db, grafana_ro_password=RO_PW)
    schema = db.execute("SELECT current_schema()").fetchone()[0]
    conninfo = psycopg.conninfo.make_conninfo(db_url, user="grafana_ro", password=RO_PW, options=f"-c search_path={schema}")
    with psycopg.connect(conninfo) as conn:
        yield conn


def test_ro_can_read_views(ro):
    for view in ("v_daily", "v_sleep_sessions", "v_growth", "collector_runs"):
        ro.execute(f"SELECT * FROM {view}").fetchall()


def test_ro_can_read_safe_event_columns(ro):
    assert ro.execute("SELECT event_id, ts, type, category, value_num FROM events").fetchone()[0] == "fx-006"


@pytest.mark.parametrize("col", ["memo", "raw", "*"])
def test_ro_cannot_read_private_columns(ro, col):
    with pytest.raises(errors.InsufficientPrivilege):
        ro.execute(f"SELECT {col} FROM events")


def test_ro_cannot_write(ro):
    with pytest.raises(errors.InsufficientPrivilege):
        ro.execute("DELETE FROM events")


def test_migrate_role_idempotent(db):
    migrate(db, grafana_ro_password=RO_PW)
    migrate(db, grafana_ro_password=RO_PW)
    migrate(db)


def test_ro_password_rotates(db, db_url):
    migrate(db, grafana_ro_password="first")
    migrate(db, grafana_ro_password="second")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(psycopg.conninfo.make_conninfo(db_url, user="grafana_ro", password="first"))

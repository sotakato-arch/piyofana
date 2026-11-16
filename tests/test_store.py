import dataclasses
from datetime import date

from collector.normalize import normalize
from collector.store import RunRecord, migrate, record_run, upsert_events
from tests.conftest import by_id


def events(records):
    return [normalize(r) for r in records]


def count(conn, table="events"):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_migrate_idempotent(db):
    migrate(db)
    migrate(db)
    assert count(db) == 0


def test_upsert_inserts_then_noop(db, records):
    evs = events(records)
    assert upsert_events(db, evs) == len(evs)
    assert upsert_events(db, evs) == 0
    assert count(db) == len(evs)


def test_upsert_updates_changed_value(db, records):
    ev = normalize(by_id(records, "fx-004"))
    upsert_events(db, [ev])
    first_seen = db.execute("SELECT first_seen FROM events WHERE event_id='fx-004'").fetchone()[0]
    assert upsert_events(db, [dataclasses.replace(ev, value_num=60.0)]) == 0
    row = db.execute("SELECT value_num, first_seen FROM events WHERE event_id='fx-004'").fetchone()
    assert float(row[0]) == 60.0
    assert row[1] == first_seen


def test_upsert_duplicate_ids_in_one_batch(db, records):
    ev = normalize(by_id(records, "fx-004"))
    assert upsert_events(db, [ev, dataclasses.replace(ev, value_num=60.0)]) == 1
    assert float(db.execute("SELECT value_num FROM events").fetchone()[0]) == 60.0


def test_upsert_empty(db):
    assert upsert_events(db, []) == 0


def test_float_seconds_roundtrip(db, records):
    upsert_events(db, [normalize(by_id(records, "fx-001"))])
    left = db.execute("SELECT left_sec FROM events").fetchone()[0]
    assert abs(float(left) - 573.97624194622) < 1e-9


def test_raw_is_jsonb(db, records):
    upsert_events(db, [normalize(by_id(records, "fx-009"))])
    assert db.execute("SELECT raw->'details'->>'color' FROM events").fetchone()[0] == "yellow"


def test_upsert_commits(db, db_url, records):
    import psycopg

    upsert_events(db, events(records))
    schema = db.execute("SELECT current_schema()").fetchone()[0]
    with psycopg.connect(db_url, options=f"-c search_path={schema}") as other:
        assert other.execute("SELECT count(*) FROM events").fetchone()[0] == len(records)


def test_record_run(db):
    run = RunRecord("ok", 200, None, 31, 3, 0, date(2027, 2, 13))
    record_run(db, run)
    row = db.execute(
        "SELECT status, http_status, error_code, records_seen, records_new, records_invalid, feed_expires_on"
        " FROM collector_runs"
    ).fetchone()
    assert row == ("ok", 200, None, 31, 3, 0, date(2027, 2, 13))

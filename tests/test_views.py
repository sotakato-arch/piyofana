"""Derived views, exercised with crafted events. Times are written in JST."""

from datetime import date, datetime, timedelta, timezone

from collector.normalize import normalize
from collector.store import upsert_events

JST = timezone(timedelta(hours=9))
_n = 0


def ev(jst: str, type_: str, **extra) -> dict:
    """A feed record at a JST wall-clock time ('2026-11-15 01:00')."""
    global _n
    _n += 1
    ts = datetime.fromisoformat(jst).replace(tzinfo=JST).astimezone(timezone.utc)
    return {"event_id": f"v-{_n}", "datetime": ts.isoformat().replace("+00:00", "Z"), "type": type_, **extra}


def ml(v):
    return {"value": {"value": v, "unit": "ml"}}


def load(db, *records):
    upsert_events(db, [normalize(r) for r in records])


def daily(db, day: str, col: str):
    row = db.execute(f"SELECT {col} FROM v_daily WHERE day = %s", (date.fromisoformat(day),)).fetchone()
    return None if row is None else row[0]


def test_daily_total_ml_excludes_pumping(db):
    load(db, ev("2026-11-15 08:00", "Formula", **ml(40)), ev("2026-11-15 09:00", "ExpressedBreastMilk", **ml(30)),
         ev("2026-11-15 10:00", "Pumping", **ml(30)))
    assert daily(db, "2026-11-15", "total_ml") == 70.0


def test_daily_drink_counts_as_intake(db):
    load(db, ev("2026-11-15 08:00", "Drink", **ml(25)))
    assert daily(db, "2026-11-15", "total_ml") == 25.0


def test_daily_jst_bucketing(db):
    # 23:59 JST and 00:00 JST are 14:59Z and 15:00Z: different JST days.
    load(db, ev("2026-11-15 23:59", "Pee"), ev("2026-11-16 00:00", "Pee"))
    assert daily(db, "2026-11-15", "pee_count") == 1
    assert daily(db, "2026-11-16", "pee_count") == 1


def test_daily_breast_sec_single_side(db):
    load(db, ev("2026-11-15 08:00", "BreastFeeding", leftTime=492.3))
    assert abs(daily(db, "2026-11-15", "breast_sec") - 492.3) < 1e-9


def test_daily_counts(db):
    load(db, ev("2026-11-15 08:00", "BreastFeeding", leftTime=60), ev("2026-11-15 09:00", "Formula", **ml(40)),
         ev("2026-11-15 10:00", "Pumping", **ml(30)), ev("2026-11-15 11:00", "Poop"), ev("2026-11-15 12:00", "Poop"))
    assert daily(db, "2026-11-15", "feed_count") == 2
    assert daily(db, "2026-11-15", "poop_count") == 2
    assert daily(db, "2026-11-15", "pee_count") == 0


def test_daily_longest_gap(db):
    load(db, ev("2026-11-15 01:00", "Formula", **ml(40)), ev("2026-11-15 03:30", "BreastFeeding", leftTime=60),
         ev("2026-11-15 04:00", "ExpressedBreastMilk", **ml(30)))
    assert daily(db, "2026-11-15", "longest_feed_gap_min") == 150.0


def test_daily_temperature_max(db):
    t = lambda v: {"value": {"value": v, "unit": "celsius"}}  # noqa: E731
    load(db, ev("2026-11-15 08:00", "Temperature", **t(36.8)), ev("2026-11-15 20:00", "Temperature", **t(37.6)))
    assert daily(db, "2026-11-15", "temperature_max") == 37.6


def test_daily_partial_flag(db):
    today = datetime.now(JST).date()
    load(db, ev(f"{today} 00:30", "Pee"), ev("2020-01-01 12:00", "Pee"))
    assert daily(db, str(today), "partial") is True
    assert daily(db, "2020-01-01", "partial") is False


def sessions(db):
    return db.execute("SELECT hours, properly_paired, sleep_end IS NULL FROM v_sleep_sessions ORDER BY sleep_start").fetchall()


def test_sleep_paired(db):
    load(db, ev("2026-11-15 13:00", "Sleep"), ev("2026-11-15 14:30", "WakeUp"))
    assert sessions(db) == [(1.5, True, False)]


def test_sleep_missed_wakeup(db):
    load(db, ev("2026-11-15 13:00", "Sleep"), ev("2026-11-15 15:00", "Sleep"), ev("2026-11-15 16:00", "WakeUp"))
    rows = sessions(db)
    assert [r[1] for r in rows] == [False, True]
    assert daily(db, "2026-11-15", "sleep_hours") == 1.0  # the unpaired session contributes nothing


def test_sleep_open(db):
    load(db, ev("2026-11-15 13:00", "Sleep"))
    assert sessions(db) == [(None, False, True)]
    assert daily(db, "2026-11-15", "sleep_hours") is None


def test_sleep_orphan_wakeup(db):
    load(db, ev("2026-11-15 07:00", "WakeUp"))
    assert sessions(db) == []


def test_sleep_crosses_midnight(db):
    load(db, ev("2026-11-15 20:00", "Sleep"), ev("2026-11-16 06:00", "WakeUp"))
    assert daily(db, "2026-11-15", "sleep_hours") == 4.0
    assert daily(db, "2026-11-16", "sleep_hours") == 6.0


def test_no_sleep_logged_is_null_not_zero(db):
    load(db, ev("2026-11-15 08:00", "Pee"))
    assert daily(db, "2026-11-15", "sleep_hours") is None


def test_views_empty_db(db):
    for view in ("v_daily", "v_sleep_sessions", "v_growth"):
        assert db.execute(f"SELECT count(*) FROM {view}").fetchone()[0] == 0


def test_growth_metrics(db):
    load(db, ev("2026-11-15 08:00", "Weight", value={"value": 4.2, "unit": "kg"}),
         ev("2026-11-15 08:05", "Height", value={"value": 55.0, "unit": "cm"}))
    rows = db.execute("SELECT metric, value, unit FROM v_growth ORDER BY metric").fetchall()
    assert rows == [("height", 55.0, "cm"), ("weight", 4.2, "kg")]


def test_growth_foot_expanded_object_shape(db):
    load(db, ev("2026-11-15 08:00", "Foot", value={"left": {"value": 9.5, "unit": "cm"}, "right": {"value": 9.6, "unit": "cm"}}))
    rows = db.execute("SELECT metric, value, unit FROM v_growth ORDER BY metric").fetchall()
    assert rows == [("foot_left", 9.5, "cm"), ("foot_right", 9.6, "cm")]


def test_growth_foot_bare_number_shape(db):
    load(db, ev("2026-11-15 08:00", "Foot", value={"left": 9.5, "right": 9.6}))
    rows = db.execute("SELECT metric, value, unit FROM v_growth ORDER BY metric").fetchall()
    assert rows == [("foot_left", 9.5, "cm"), ("foot_right", 9.6, "cm")]

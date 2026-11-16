import copy
from datetime import UTC, datetime

import pytest

from collector.normalize import InvalidRecord, normalize
from tests.conftest import by_id


def test_breastfeeding_both_sides(records):
    e = normalize(by_id(records, "fx-001"))
    assert e.left_sec == 573.97624194622
    assert e.right_sec == 543.53102600574
    assert e.last_side == "left"
    assert e.category == "feeding"


def test_breastfeeding_left_only_no_last(records):
    e = normalize(by_id(records, "fx-002"))
    assert e.left_sec == 492.34350097179
    assert e.right_sec is None
    assert e.last_side is None


def test_breastfeeding_right_only_with_last(records):
    e = normalize(by_id(records, "fx-003"))
    assert e.left_sec is None
    assert e.right_sec == 660.0
    assert e.last_side == "left"


def test_value_record(records):
    e = normalize(by_id(records, "fx-004"))
    assert e.value_num == 40.0
    assert e.value_unit == "ml"


def test_foot_left_right(records):
    rec = by_id(records, "fx-010")
    e = normalize(rec)
    assert e.category == "growth"
    assert e.left_sec is None
    assert e.value_num is None
    assert e.raw["value"]["left"] == rec["value"]["left"]


def test_poop_without_details(records):
    e = normalize(by_id(records, "fx-008"))
    assert (e.poop_amount, e.poop_hardness, e.poop_color) == (None, None, None)
    assert e.category == "diaper"


def test_poop_with_details(records):
    e = normalize(by_id(records, "fx-009"))
    assert (e.poop_amount, e.poop_hardness, e.poop_color) == ("normal", "soft", "yellow")


def test_memo_captured(records):
    assert normalize(by_id(records, "fx-006")).memo == "test memo"
    assert normalize(by_id(records, "fx-004")).memo is None


def test_unknown_type_is_other(records):
    e = normalize(by_id(records, "fx-012"))
    assert e.type == "FutureThing"
    assert e.category == "other"


def test_ts_is_utc_aware(records):
    assert normalize(by_id(records, "fx-001")).ts == datetime(2026, 11, 14, 22, 0, tzinfo=UTC)


@pytest.mark.parametrize("field", ["event_id", "datetime", "type"])
def test_missing_required_raises(records, field):
    rec = copy.deepcopy(by_id(records, "fx-004"))
    del rec[field]
    with pytest.raises(InvalidRecord):
        normalize(rec)


def test_unparseable_datetime_raises(records):
    rec = copy.deepcopy(by_id(records, "fx-004"))
    rec["datetime"] = "nope"
    with pytest.raises(InvalidRecord):
        normalize(rec)


def test_raw_is_unmodified_copy(records):
    rec = copy.deepcopy(by_id(records, "fx-009"))
    e = normalize(rec)
    assert e.raw == rec
    rec["details"]["color"] = "green"
    assert e.raw["details"]["color"] == "yellow"

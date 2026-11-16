"""Turn a raw PiyoLog feed record into a flat, typed Event.

The published schema is incomplete, so everything except event_id, datetime
and type is treated as optional. Unknown types are kept, not rejected: a new
record kind in the app must never stop ingestion.
"""

import copy
from dataclasses import dataclass
from datetime import UTC, datetime

CATEGORY: dict[str, str] = {
    **dict.fromkeys(["BreastFeeding", "Formula", "ExpressedBreastMilk", "Pumping", "Drink"], "feeding"),
    **dict.fromkeys(["Sleep", "WakeUp"], "sleep"),
    **dict.fromkeys(["Pee", "Poop"], "diaper"),
    **dict.fromkeys(["Height", "Weight", "Head", "Chest", "Foot"], "growth"),
    **dict.fromkeys(
        ["Temperature", "Cough", "Vomiting", "Rash", "Injury", "Medicine", "Hospital", "Vaccine"], "health"
    ),
    **dict.fromkeys(["Bath", "Walking"], "care"),
}


class InvalidRecord(ValueError):
    """A record missing a required field or carrying an unparseable one."""


@dataclass(frozen=True)
class Event:
    event_id: str
    ts: datetime
    type: str
    category: str
    value_num: float | None
    value_unit: str | None
    left_sec: float | None
    right_sec: float | None
    last_side: str | None
    poop_amount: str | None
    poop_hardness: str | None
    poop_color: str | None
    memo: str | None
    raw: dict


def _float(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _str(v) -> str | None:
    return v if isinstance(v, str) else None


def _parse_ts(value) -> datetime:
    if not isinstance(value, str):
        raise InvalidRecord("datetime is not a string")
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InvalidRecord("datetime is not ISO 8601") from None
    if ts.tzinfo is None:
        raise InvalidRecord("datetime has no timezone")
    return ts.astimezone(UTC)


def normalize(record: dict) -> Event:
    for field in ("event_id", "datetime", "type"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise InvalidRecord(f"missing or empty {field}")

    value = record.get("value")
    # Only the flat {"value": N, "unit": ...} shape maps to value_num. Foot's
    # {"left": ..., "right": ...} stays in raw and is expanded by v_growth.
    flat = isinstance(value, dict) and "value" in value
    details = record.get("details") if isinstance(record.get("details"), dict) else {}

    return Event(
        event_id=record["event_id"],
        ts=_parse_ts(record["datetime"]),
        type=record["type"],
        category=CATEGORY.get(record["type"], "other"),
        value_num=_float(value.get("value")) if flat else None,
        value_unit=_str(value.get("unit")) if flat else None,
        left_sec=_float(record.get("leftTime")),
        right_sec=_float(record.get("rightTime")),
        last_side=_str(record.get("last")),
        poop_amount=_str(details.get("amount")),
        poop_hardness=_str(details.get("hardness")),
        poop_color=_str(details.get("color")),
        memo=_str(record.get("memo")),
        raw=copy.deepcopy(record),
    )

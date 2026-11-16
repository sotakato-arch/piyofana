"""Postgres access: migrations, idempotent event upsert, run heartbeat."""

from collections.abc import Sequence
from dataclasses import astuple, dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from collector.normalize import Event

SQL_DIR = Path(__file__).parent / "sql"

_COLUMNS = (
    "event_id, ts, type, category, value_num, value_unit, left_sec, right_sec,"
    " last_side, poop_amount, poop_hardness, poop_color, memo, raw"
)
_UPDATABLE = [c.strip() for c in _COLUMNS.split(",") if c.strip() != "event_id"]

# xmax is 0 only for a freshly inserted tuple, so RETURNING it distinguishes
# inserts from conflict-updates in a single statement.
_UPSERT = (
    f"INSERT INTO events ({_COLUMNS}) VALUES ({', '.join(['%s'] * 14)})"
    " ON CONFLICT (event_id) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in _UPDATABLE)
    + " RETURNING (xmax = 0)"
)


@dataclass(frozen=True)
class RunRecord:
    status: Literal["ok", "rate_limited", "error"]
    http_status: int | None
    error_code: str | None
    records_seen: int
    records_new: int
    records_invalid: int
    feed_expires_on: date | None


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, connect_timeout=10)


def migrate(conn: psycopg.Connection, *, grafana_ro_password: str | None = None) -> None:
    """Apply every sql/*.sql in filename order. Each file must be idempotent.

    When a password is given, (re)set the grafana_ro login's password, so
    rotating GRAFANA_RO_PASSWORD in .env takes effect on the next poll.
    """
    with conn.transaction():
        for path in sorted(SQL_DIR.glob("*.sql")):
            conn.execute(path.read_text())
        if grafana_ro_password:
            conn.execute(sql.SQL("ALTER ROLE grafana_ro PASSWORD {}").format(sql.Literal(grafana_ro_password)))


def upsert_events(conn: psycopg.Connection, events: Sequence[Event]) -> int:
    """Insert or update events; return how many were new."""
    # Postgres refuses an upsert batch that touches the same row twice, and a
    # response can repeat an event_id. Last occurrence wins.
    unique = {e.event_id: e for e in events}
    if not unique:
        return 0
    new = 0
    with conn.transaction(), conn.cursor() as cur:
        for e in unique.values():
            row = astuple(e)
            cur.execute(_UPSERT, (*row[:-1], Jsonb(e.raw)))
            new += cur.fetchone()[0]
    return new


def record_run(conn: psycopg.Connection, run: RunRecord) -> None:
    with conn.transaction():
        conn.execute(
            "INSERT INTO collector_runs (status, http_status, error_code, records_seen,"
            " records_new, records_invalid, feed_expires_on) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            astuple(run),
        )

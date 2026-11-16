import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def feed_body() -> bytes:
    return (FIXTURES / "feed_24h.json").read_bytes()


@pytest.fixture
def feed(feed_body) -> dict:
    return json.loads(feed_body)


@pytest.fixture
def records(feed) -> list[dict]:
    return feed["records"]


def by_id(records: list[dict], event_id: str) -> dict:
    return next(r for r in records if r["event_id"] == event_id)


@pytest.fixture
def db_url() -> str:
    import os

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL unset; run via docker compose -f docker-compose.test.yml")
    return url


@pytest.fixture
def db(db_url):
    """A migrated connection inside a throwaway schema, dropped afterwards."""
    import uuid

    import psycopg

    from collector.store import migrate

    schema = f"t_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(db_url, autocommit=True)
    admin.execute(f"CREATE SCHEMA {schema}")
    conn = psycopg.connect(db_url, options=f"-c search_path={schema}")
    try:
        migrate(conn)
        yield conn
    finally:
        conn.close()
        admin.execute(f"DROP SCHEMA {schema} CASCADE")
        admin.close()

import gzip
import logging
import threading
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from collector.config import Config
from collector.run import exit_code, loop, main, run_once

URL = "https://feed.piyolog.com/v1/feed/24h/fakeid/fakesecret"
NOW = datetime(2026, 11, 15, 20, 10, tzinfo=UTC)


def schema_url(db, db_url):
    schema = db.execute("SELECT current_schema()").fetchone()[0]
    return f"{db_url}?options={quote(f'-c search_path={schema}')}"


def cfg(tmp_path, database_url):
    return Config(feed_url=URL, database_url=database_url, raw_dir=tmp_path / "raw")


def client(*responses):
    queue = list(responses)
    return httpx.Client(transport=httpx.MockTransport(lambda req: queue.pop(0)))


def ok(body):
    return httpx.Response(200, content=body)


def runs(db):
    return db.execute(
        "SELECT status, error_code, records_seen, records_new, records_invalid FROM collector_runs ORDER BY ran_at"
    ).fetchall()


def test_run_once_happy(db, db_url, tmp_path, feed_body):
    run = run_once(cfg(tmp_path, schema_url(db, db_url)), client=client(ok(feed_body)), now=lambda: NOW)
    assert (run.status, run.records_seen, run.records_new, run.records_invalid) == ("ok", 12, 12, 0)
    assert runs(db) == [("ok", None, 12, 12, 0)]
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 12
    assert len(list((tmp_path / "raw").glob("*.json.gz"))) == 1


def test_run_once_second_pass_no_new(db, db_url, tmp_path, feed_body):
    c = cfg(tmp_path, schema_url(db, db_url))
    run_once(c, client=client(ok(feed_body)), now=lambda: NOW)
    assert run_once(c, client=client(ok(feed_body)), now=lambda: NOW).records_new == 0
    raw = next((tmp_path / "raw").glob("*.json.gz"))
    assert len(gzip.open(raw).read().splitlines()) == 2


def test_run_once_skips_invalid_record(db, db_url, tmp_path, feed):
    import json

    feed["records"].append({"datetime": "2026-11-15T09:00:00.000Z", "type": "Pee"})
    run = run_once(cfg(tmp_path, schema_url(db, db_url)), client=client(ok(json.dumps(feed).encode())), now=lambda: NOW)
    assert (run.status, run.records_seen, run.records_new, run.records_invalid) == ("ok", 13, 12, 1)


def test_run_once_empty_window_is_ok(db, db_url, tmp_path):
    body = b'{"schema_version":1,"generated_at":"2026-11-15T20:00:00Z","range":{"from":"2026-11-14T20:00:00Z","to":"2026-11-15T20:00:00Z"},"records":[]}'
    run = run_once(cfg(tmp_path, schema_url(db, db_url)), client=client(ok(body)), now=lambda: NOW)
    assert (run.status, run.records_seen, run.records_new) == ("ok", 0, 0)
    assert runs(db) == [("ok", None, 0, 0, 0)]


def test_run_once_404_records_error(db, db_url, tmp_path):
    resp = httpx.Response(404, json={"error": {"code": "feed_unavailable"}})
    run = run_once(cfg(tmp_path, schema_url(db, db_url)), client=client(resp), now=lambda: NOW)
    assert (run.status, run.http_status, run.error_code) == ("error", 404, "feed_unavailable")
    assert runs(db) == [("error", "feed_unavailable", 0, 0, 0)]
    assert not (tmp_path / "raw").exists()


def test_run_once_429_is_rate_limited(db, db_url, tmp_path):
    resp = httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}})
    run = run_once(cfg(tmp_path, schema_url(db, db_url)), client=client(resp), now=lambda: NOW)
    assert run.status == "rate_limited"
    assert exit_code(run) == 0


def test_run_once_db_down_still_archives(tmp_path, feed_body, caplog):
    down = "postgresql://piyolog:hunter2@testdb:1/piyolog"
    run = run_once(cfg(tmp_path, down), client=client(ok(feed_body)), now=lambda: NOW)
    assert (run.status, run.error_code) == ("error", "db")
    assert len(list((tmp_path / "raw").glob("*.json.gz"))) == 1
    assert "hunter2" not in caplog.text


def test_run_once_never_logs_url(db, db_url, tmp_path, feed_body, caplog):
    caplog.set_level(logging.DEBUG)
    c = cfg(tmp_path, schema_url(db, db_url))
    for resp in [ok(feed_body), httpx.Response(404, json={"error": {"code": "feed_unavailable"}}), ok(b"junk")]:
        run_once(c, client=client(resp), now=lambda: NOW)
    assert "fakesecret" not in caplog.text and "fakeid" not in caplog.text
    assert "run status=ok" in caplog.text


def test_main_once_exit_codes(db, db_url, tmp_path, feed_body, monkeypatch):
    env = {"PIYOLOG_FEED_URL": URL, "DATABASE_URL": schema_url(db, db_url), "RAW_DIR": str(tmp_path)}
    assert main(["--once"], env=env, transport=httpx.MockTransport(lambda r: ok(feed_body))) == 0
    gone = httpx.Response(404, json={"error": {"code": "feed_unavailable"}})
    assert main(["--once"], env=env, transport=httpx.MockTransport(lambda r: gone)) == 1


def test_main_bad_config_exits_2():
    assert main(["--once"], env={}) == 2


def test_loop_stops_when_asked(db, db_url, tmp_path, feed_body):
    stop = threading.Event()
    calls = []

    def once():
        calls.append(1)
        stop.set()

    loop(once, interval_s=600, stop=stop)
    assert calls == [1]


def test_feed_error_code_survives_db_outage(tmp_path):
    gone = httpx.Response(404, json={"error": {"code": "feed_unavailable"}})
    run = run_once(cfg(tmp_path, "postgresql://u:p@testdb:1/x"), client=client(gone), now=lambda: NOW)
    assert (run.status, run.error_code, exit_code(run)) == ("error", "feed_unavailable", 1)

"""One collection pass, the poll loop around it, and the CLI entry point.

Order matters: the raw response is archived before anything touches the
database, so a Postgres outage loses nothing the next poll can't recover.
A run never raises; every outcome becomes a RunRecord and, when the
database is reachable, a heartbeat row.
"""

import logging
import os
import signal
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import httpx
import psycopg

from collector.archive import write_raw
from collector.config import Config, ConfigError, load_config
from collector.feed import FeedError, fetch_feed, redact
from collector.normalize import InvalidRecord, normalize
from collector.store import RunRecord, connect, migrate, record_run, upsert_events

log = logging.getLogger("collector")


def _store(cfg: Config, events, run: RunRecord) -> RunRecord:
    try:
        with connect(cfg.database_url) as conn:
            migrate(conn, grafana_ro_password=cfg.grafana_ro_password)
            new = upsert_events(conn, events)
            run = RunRecord(**{**run.__dict__, "records_new": new})
            record_run(conn, run)
        return run
    except psycopg.Error as e:
        # psycopg messages carry host/port, never the password; log the class only anyway.
        log.error("database unavailable: %s", type(e).__name__)
        # Keep a feed error code if there is one: a dead feed must not look like a DB problem.
        return RunRecord(**{**run.__dict__, "status": "error", "error_code": run.error_code or "db", "records_new": 0})


def run_once(cfg: Config, *, client: httpx.Client, now: Callable[[], datetime]) -> RunRecord:
    try:
        response = fetch_feed(cfg.feed_url, client=client)
    except FeedError as e:
        run = RunRecord(
            status="rate_limited" if e.kind == "rate_limited" else "error",
            http_status=e.http_status,
            error_code=e.code or e.kind,
            records_seen=0,
            records_new=0,
            records_invalid=0,
            feed_expires_on=cfg.feed_expires_on,
        )
        run = _store(cfg, [], run)
        _log(cfg, run)
        return run

    write_raw(response.body, now(), cfg.raw_dir)

    events, invalid = [], 0
    for record in response.records:
        try:
            events.append(normalize(record))
        except InvalidRecord as e:
            invalid += 1
            log.warning("skipping invalid record: %s", e)

    run = RunRecord("ok", 200, None, len(response.records), 0, invalid, cfg.feed_expires_on)
    run = _store(cfg, events, run)
    _log(cfg, run)
    return run


def _log(cfg: Config, run: RunRecord) -> None:
    line = (
        f"run status={run.status} seen={run.records_seen} new={run.records_new}"
        f" invalid={run.records_invalid} http={run.http_status} error={run.error_code}"
    )
    log.info(redact(line, cfg.feed_url))


def exit_code(run: RunRecord) -> int:
    """--once exit status: 1 only for failures a human must act on."""
    if run.status != "error":
        return 0
    transient = run.error_code == "network" or (run.http_status or 0) >= 500
    return 0 if transient else 1


def loop(once: Callable[[], object], *, interval_s: int, stop: threading.Event) -> None:
    while not stop.is_set():
        once()
        stop.wait(interval_s)


def main(
    argv: list[str],
    *,
    env: Mapping[str, str] = os.environ,
    transport: httpx.BaseTransport | None = None,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        cfg = load_config(env)
    except ConfigError as e:
        log.error("configuration error: %s", e)
        return 2

    with httpx.Client(timeout=30, transport=transport) as client:
        def once() -> RunRecord:
            return run_once(cfg, client=client, now=lambda: datetime.now(UTC))

        if "--once" in argv:
            return exit_code(once())

        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        log.info("collector started: %r", cfg)
        loop(once, interval_s=cfg.interval_s, stop=stop)
        log.info("collector stopped")
        return 0

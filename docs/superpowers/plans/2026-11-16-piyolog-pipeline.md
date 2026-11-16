# PiyoLog Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Continuously capture the PiyoLog 24h feed into Postgres on the home-lab host and visualize it in Grafana Cloud through PDC.

**Architecture:** One `docker compose` project: `postgres` (no published port), `collector` (Python poll loop every 600 s: fetch → raw archive → normalize → UPSERT → heartbeat), and later `pdc-agent`. Derivation lives in SQL views. Grafana Cloud reads views through a read-only role over PDC.

**Tech Stack:** Python 3.12, `httpx`, `psycopg[binary]` 3, `pytest`, `uv` (inside images only), `postgres:17-alpine`, `grafana/pdc-agent`, gcx.

**Spec:** `docs/superpowers/specs/2026-11-16-piyolog-grafana-design.md`

**Milestone 1 = Tasks 1–6:** real records ingesting on the host. The operator rotates the feed URL after Task 6 verification.

## Global Constraints

- Every record field except `event_id`, `datetime`, `type` is optional. `leftTime`/`rightTime` are floats.
- The feed URL never appears in logs, exception text, argv, or any file in the repo. `.env` on the host is mode `600`.
- Poll interval 600 s; never more than one request per minute per URL.
- Day boundaries are JST (`Asia/Tokyo`); storage is `timestamptz` UTC.
- Postgres publishes no host port. The collector never touches any other compose stack's containers, volumes, or networks.
- Nothing is installed on the host outside Docker. No sudo.
- Repo is public: run the secret/host-identifier scan (Task 1 `scripts/scan.sh`) before every commit.
- **Commit hold (operator, 2026-11-16):** until lifted, each "Commit" step is `git add` only. When lifted, commits are signed and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Tests run in Docker: `docker compose -f docker-compose.test.yml run --rm test` (alias `make test` is not provided; use the full command).

**Spec deltas** (apply to the spec in Task 1):
1. `feed_expires_on` comes from env var `FEED_EXPIRES_ON` (ISO date) instead of `config.yaml`, which avoids a YAML dependency.
2. Runtime dependencies are `httpx` and `psycopg[binary]`.
3. In loop mode a 404/413 is recorded as `status=error` and the loop continues, because exiting would make `restart: unless-stopped` crash-loop. The heartbeat alert plus a "last run errored" alert surface it. `--once` mode keeps the spec's exit codes.

## Review Focus

1. **Duplicate `event_id` within one response.** Postgres rejects a multi-row `INSERT … ON CONFLICT DO UPDATE` that touches the same row twice. Expected: dedupe the batch (last wins) and succeed. → Task 3 test `test_upsert_duplicate_ids_in_one_batch`.
2. **One malformed record** (missing `event_id`/`datetime`/`type`, or unparseable datetime). Expected: skip that record, ingest the rest, and count it in `records_invalid`. → Task 5 test `test_run_once_skips_invalid_record`.
3. **`Pumping` is output, not intake.** Expected: `v_daily.total_ml` sums only `Formula`, `ExpressedBreastMilk`, `Drink`. → Task 7 test `test_daily_total_ml_excludes_pumping`.
4. **Postgres unavailable at poll time.** Expected: the raw archive is still written, the loop does not crash, and the next successful poll backfills from the 24h window. → Task 5 test `test_run_once_db_down_still_archives`.
5. **Empty window** (`records: []`, e.g. a quiet night). Expected: `status=ok`, `records_seen=0`, never an error. → Task 5 test `test_run_once_empty_window_is_ok`.

---

### Task 1: Scaffold + normalize

**Files:**
- Create: `pyproject.toml`, `Dockerfile`, `.dockerignore`, `docker-compose.test.yml`, `collector/__init__.py`, `collector/normalize.py`, `scripts/scan.sh`
- Create: `tests/conftest.py`, `tests/fixtures/feed_24h.json`, `tests/test_normalize.py`
- Modify: `.gitignore` (allowlist additions), spec (Spec deltas above)

**Interfaces:**
- Produces:
  - `collector.normalize.InvalidRecord(ValueError)`
  - `collector.normalize.CATEGORY: dict[str, str]`: `BreastFeeding, Formula, ExpressedBreastMilk, Pumping, Drink → feeding`; `Sleep, WakeUp → sleep`; `Pee, Poop → diaper`; `Height, Weight, Head, Chest, Foot → growth`; `Temperature, Cough, Vomiting, Rash, Injury, Medicine, Hospital, Vaccine → health`; `Bath, Walking → care`; anything else → `other`.
  - `@dataclass(frozen=True) collector.normalize.Event` with fields `event_id: str, ts: datetime (tz-aware UTC), type: str, category: str, value_num: float | None, value_unit: str | None, left_sec: float | None, right_sec: float | None, last_side: str | None, poop_amount: str | None, poop_hardness: str | None, poop_color: str | None, memo: str | None, raw: dict`
  - `collector.normalize.normalize(record: dict) -> Event`
  - `tests/fixtures/feed_24h.json`: a synthetic response with the real payload's shape. It covers `BreastFeeding` with both sides, left only, right only (with and without `last`), `Formula`, `ExpressedBreastMilk`, `Pumping` with `memo`, `Pee`, `Poop` without `details`, one `Poop` with `details`, one `Foot` with `value.left`/`value.right`, one `Weight`, and one unknown type `FutureThing`. All ids are fake (`fx-001`…).

- [ ] **Step 1: Scaffold.** `pyproject.toml`: project `piyofana`, `requires-python = ">=3.12"`, deps `httpx>=0.27`, `psycopg[binary]>=3.2`, dev group `pytest>=8`. `Dockerfile` stages: `base` (`python:3.12-slim`, `uv` copied from `ghcr.io/astral-sh/uv:latest`, copy `pyproject.toml`+`uv.lock`), `dev` (`uv sync --frozen`, copy `collector/` and `tests/`, `ENTRYPOINT ["uv","run","pytest"]`), `runtime` (last/default: `uv sync --frozen --no-dev`, copy `collector/`, non-root user `app` uid 1000, `ENTRYPOINT ["python","-m","collector"]`). Generate `uv.lock` with `docker run --rm -v "$PWD":/w -w /w ghcr.io/astral-sh/uv:latest lock`. `docker-compose.test.yml`: service `testdb` (`postgres:17-alpine`, password `test`, tmpfs data, healthcheck `pg_isready`), service `test` (build `.` target `dev`, `TEST_DATABASE_URL=postgresql://postgres:test@testdb/postgres`, default args `-q`, depends_on testdb healthy; extra args after `run --rm test` are passed to pytest). Add to `.gitignore` allowlist: `!.dockerignore`, `!docker-compose.test.yml`, `!collector/sql/*.sql`, `!scripts/*.sh`. `scripts/scan.sh` exits 1 if `git ls-files -co --exclude-standard | xargs grep -lE` matches the feed-URL regex `feed\.piyolog\.com/v1/feed/[^ ]+/[0-9a-f]{8}`, `ts\.net`, `100\.[0-9]+\.[0-9]+\.[0-9]+`, `192\.168\.`, or any regex in `docs/local/scan-patterns.txt` (gitignored, one regex per line; create it in this step with the hostname, tailnet name, SSH user and Grafana stack slug from `docs/local/host-notes.md`). Nothing host-specific is hard-coded in the script.
- [ ] **Step 2: Write failing tests** in `tests/test_normalize.py`:
  - `test_breastfeeding_both_sides`: `left_sec == 573.97624194622`, `right_sec == 543.53102600574`, `last_side == "left"`, `category == "feeding"`
  - `test_breastfeeding_left_only_no_last`: `right_sec is None`, `last_side is None`
  - `test_breastfeeding_right_only_with_last`: `left_sec is None`, `last_side == "left"` (preserved verbatim even though it looks odd)
  - `test_value_record`: Formula → `value_num == 40.0`, `value_unit == "ml"`
  - `test_foot_left_right`: `Foot` → `left_sec is None`, `value_num is None`, and `raw["value"]["left"]` preserved. Foot is growth data read from `raw` in `v_growth`.
  - `test_poop_without_details`: all `poop_*` are None
  - `test_poop_with_details`: `poop_amount == "normal"` etc.
  - `test_memo_captured`: Pumping → `memo` is the fixture string
  - `test_unknown_type_is_other`: `FutureThing` → `category == "other"`, no exception
  - `test_ts_is_utc_aware`: `"2026-11-14T22:00:00.000Z"` → `datetime(2026,11,14,22,0,tzinfo=UTC)`
  - `test_missing_required_raises`: each of `event_id`, `datetime`, `type` removed → `InvalidRecord`; `datetime: "nope"` → `InvalidRecord`
  - `test_raw_is_unmodified_copy`: `event.raw == record` and mutating `record` afterwards does not change `event.raw`
- [ ] **Step 3: Run** `docker compose -f docker-compose.test.yml run --rm test tests/test_normalize.py` → FAIL (import error).
- [ ] **Step 4: Implement `normalize`.** Read `value` only when it is a dict containing `value`. `Foot`'s `value.left/right` stay in `raw` only. Deep-copy `raw`.
- [ ] **Step 5: Run** the same command → all pass. Run `scripts/scan.sh` → exit 0.
- [ ] **Step 6: Apply the Spec deltas** to the spec's Operations and Data model sections.
- [ ] **Step 7: Commit** `feat: project scaffold and defensive record normalization`.

### Task 2: Feed client with redaction

**Files:**
- Create: `collector/feed.py`, `tests/test_feed.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) FeedResponse: generated_at: datetime, range_from: datetime, range_to: datetime, records: list[dict], body: bytes`
  - `class FeedError(Exception)` with attributes `kind: Literal["unavailable","too_large","rate_limited","server","network","bad_payload"]`, `http_status: int | None`, `code: str | None`. `str(err)` never contains the URL.
  - `redact(text: str, url: str) -> str`: replaces the URL, and separately its last two path segments, with `[REDACTED]`
  - `fetch_feed(url: str, *, client: httpx.Client, sleep: Callable[[float], None] = time.sleep, retries: int = 2) -> FeedResponse`

- [ ] **Step 1: Write failing tests** using `httpx.MockTransport`, with `URL = "https://feed.piyolog.com/v1/feed/24h/fakeid/fakesecret"`:
  - `test_ok_parses_envelope`: 200 + fixture → `len(records)`, `generated_at` tz-aware, `body` equals the response bytes
  - `test_404_unavailable`: body `{"error":{"code":"feed_unavailable"}}` → `FeedError(kind="unavailable", http_status=404, code="feed_unavailable")`, and transport called once
  - `test_413_too_large`, `test_429_rate_limited`: called once, no retry
  - `test_5xx_retries_twice_then_raises`: 3 calls, `sleep` called with `[2, 4]`, `kind="server"`
  - `test_5xx_then_ok`: 500 then 200 → returns response
  - `test_network_error`: transport raises `httpx.ConnectError(f"boom {URL}")` → `kind="network"`, `"fakesecret" not in str(err)` and `"fakeid" not in str(err)`, and `err.__cause__ is None` (use `raise … from None` so tracebacks don't carry the URL)
  - `test_bad_payload`: 200 with `not json`, or JSON missing `records` → `kind="bad_payload"`
  - `test_no_url_in_logs`: run every error case under `caplog` at DEBUG → `"fakesecret" not in caplog.text`
  - `test_redact`: `redact(f"GET {URL} failed", URL) == "GET [REDACTED] failed"`, and the bare secret segment alone is also redacted
- [ ] **Step 2: Run** `… run --rm test tests/test_feed.py` → FAIL.
- [ ] **Step 3: Implement.** Error `code` comes from `body["error"]["code"]` when present, otherwise None. Set `client` timeout to 30 s in the caller (Task 5). The `httpx` logger must not log URLs at INFO: Task 5 sets `logging.getLogger("httpx").setLevel(logging.WARNING)`.
- [ ] **Step 4: Run** → pass.
- [ ] **Step 5: Commit** `feat: feed client with error classification and URL redaction`.

### Task 3: Schema and store

**Files:**
- Create: `collector/sql/001_schema.sql`, `collector/store.py`, `tests/test_store.py`
- Modify: `tests/conftest.py` (fixture `db`: connects to `TEST_DATABASE_URL`, creates a fresh schema `t_<uuid>`, sets `search_path`, runs `migrate`, drops the schema on teardown; skips with a clear message if the env var is unset)

**Interfaces:**
- Consumes: `Event` (Task 1)
- Produces:
  - `collector/sql/001_schema.sql`: the spec's `events` and `collector_runs` DDL verbatim, using `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, plus a `collector_runs.records_invalid int` column
  - `@dataclass(frozen=True) RunRecord: status: Literal["ok","rate_limited","error"], http_status: int | None, error_code: str | None, records_seen: int, records_new: int, records_invalid: int, feed_expires_on: date | None`
  - `connect(dsn: str) -> psycopg.Connection` (autocommit off)
  - `migrate(conn) -> None`: executes every `collector/sql/*.sql` in filename order inside one transaction; idempotent
  - `upsert_events(conn, events: Sequence[Event]) -> int`: returns the number of *newly inserted* rows. Detect them with `RETURNING (xmax = 0)`. On conflict, update every column except `event_id` and `first_seen`.
  - `record_run(conn, run: RunRecord) -> None`

- [ ] **Step 1: Write failing tests:**
  - `test_migrate_idempotent`: `migrate` twice → no error
  - `test_upsert_inserts_then_noop`: fixture events → returns N; same again → returns 0, row count still N
  - `test_upsert_updates_changed_value`: re-upsert one event with `value_num` 40→60 → returns 0, the stored value is 60, `first_seen` unchanged
  - `test_upsert_duplicate_ids_in_one_batch`: two Events with the same id, values 40 then 60 → returns 1, stored 60 *(Review Focus 1)*
  - `test_upsert_empty`: `[]` → 0 with no query issued
  - `test_float_seconds_roundtrip`: `left_sec` 573.97624194622 stored and read back equal within 1e-9
  - `test_raw_is_jsonb`: `raw->>'type'` queryable
  - `test_record_run`: inserts a row whose fields match
- [ ] **Step 2: Run** `… run --rm test tests/test_store.py` → FAIL.
- [ ] **Step 3: Implement.** Dedupe the batch with a dict keyed on `event_id`, last wins, then use `executemany` or a single `INSERT … VALUES` with `psycopg.types.json.Jsonb(raw)`.
- [ ] **Step 4: Run** → pass.
- [ ] **Step 5: Commit** `feat: postgres schema, idempotent upsert, run heartbeat`.

### Task 4: Raw archive

**Files:**
- Create: `collector/archive.py`, `tests/test_archive.py`

**Interfaces:**
- Produces: `write_raw(body: bytes, fetched_at: datetime, root: Path) -> Path`. Appends one gzip member containing `body` + `b"\n"` to `root/YYYY-MM-DD.json.gz`, where the date is `fetched_at` in JST. Creates `root` if missing, with file mode `0o600`.

- [ ] **Step 1: Write failing tests:**
  - `test_appends_members`: two writes on the same JST date → one file; `gzip.open(...).read().splitlines()` gives both bodies in order
  - `test_jst_date_boundary`: `2026-11-15T14:59:59Z` → `2026-11-15.json.gz`; `2026-11-15T15:00:00Z` → `2026-11-16.json.gz`
  - `test_file_mode_600`
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (`gzip.open(path, "ab")`). **Step 4: Run** → pass.
- [ ] **Step 5: Commit** `feat: append-only gzipped raw response archive`.

### Task 5: Collector orchestration and loop

**Files:**
- Create: `collector/config.py`, `collector/run.py`, `collector/__main__.py`, `tests/test_config.py`, `tests/test_run.py`

**Interfaces:**
- Consumes: `fetch_feed`, `FeedError`, `redact` (Task 2); `normalize`, `InvalidRecord` (Task 1); `connect`, `migrate`, `upsert_events`, `record_run`, `RunRecord` (Task 3); `write_raw` (Task 4)
- Produces:
  - `@dataclass(frozen=True) Config: feed_url: str, database_url: str, raw_dir: Path, interval_s: int = 600, feed_expires_on: date | None = None`. `Config.__repr__` hides `feed_url` and the password in `database_url`.
  - `class ConfigError(Exception)`; `load_config(env: Mapping[str, str]) -> Config`. Reads `PIYOLOG_FEED_URL` (required), `DATABASE_URL` (required), `RAW_DIR` (default `/data/raw`), `POLL_INTERVAL_S` (min 60), `FEED_EXPIRES_ON` (optional ISO date).
  - `run_once(cfg: Config, *, client: httpx.Client, now: Callable[[], datetime]) -> RunRecord`. Order: fetch → `write_raw` → normalize each record (skip `InvalidRecord`, counting it) → connect/migrate/upsert/`record_run`. A `FeedError` produces `RunRecord(status="rate_limited" if kind=="rate_limited" else "error", …)`, and `record_run` is still attempted. A DB failure is logged (redacted) and returns `status="error"` without raising.
  - `main(argv: list[str]) -> int`. `--once` runs a single pass and returns 0 for ok/rate_limited/server, 1 otherwise. With no flag it loops forever with `interval_s` sleeps, stops cleanly on SIGTERM/SIGINT within 1 s (use `threading.Event.wait`), and never exits on a run failure.

- [ ] **Step 1: Write failing tests** (`run_once` with MockTransport + `db` fixture + `tmp_path`):
  - `test_config_requires_feed_url`: the `ConfigError` message names the variable and does not include any value
  - `test_config_repr_hides_secrets`
  - `test_config_interval_floor`: `POLL_INTERVAL_S=10` → `ConfigError`
  - `test_run_once_happy`: `status="ok"`, `records_new == N`, one `collector_runs` row, one raw file
  - `test_run_once_second_pass_no_new`: `records_new == 0`
  - `test_run_once_skips_invalid_record` *(RF 2)*: the fixture plus one record missing `event_id` → `records_invalid == 1`, other records stored
  - `test_run_once_empty_window_is_ok` *(RF 5)*
  - `test_run_once_404_records_error`: `status="error"`, `error_code="feed_unavailable"`, and the heartbeat row exists
  - `test_run_once_db_down_still_archives` *(RF 4)*: `database_url` pointing at an unused port → returns `status="error"`, the raw file exists, no exception
  - `test_run_once_never_logs_url`: all of the above under `caplog`, and the secret is absent
  - `test_main_once_exit_codes`: ok → 0, 404 → 1
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Logs use one line per run, for example `run status=ok seen=31 new=3 invalid=0`. **Step 4: Run** the full suite → pass.
- [ ] **Step 5: Commit** `feat: collector run loop with heartbeat and graceful failure`.

### Task 6: Compose stack and deploy (Milestone 1)

**Files:**
- Create: `docker-compose.yml`, `.env.example`, `scripts/deploy.sh`

**Interfaces:**
- `docker-compose.yml`: project name `piyofana`, network `piyofana`.
  - `postgres`: `postgres:17-alpine`, `POSTGRES_DB=piyolog`, `POSTGRES_USER=piyolog`, `POSTGRES_PASSWORD=${POSTGRES_PASSWORD}`, volume `./data/pgdata:/var/lib/postgresql/data`, healthcheck `pg_isready -U piyolog`, `restart: unless-stopped`, **no `ports:`**
  - `collector`: build `.`, `env_file: .env`, `DATABASE_URL=postgresql://piyolog:${POSTGRES_PASSWORD}@postgres/piyolog`, volume `./data/raw:/data/raw`, `user: "1000:1000"`, depends_on postgres healthy, `restart: unless-stopped`
- `.env.example` (names only): `PIYOLOG_FEED_URL=`, `POSTGRES_PASSWORD=`, `FEED_EXPIRES_ON=`, `POLL_INTERVAL_S=600`
- `scripts/deploy.sh HOST`: `git ls-files -co --exclude-standard | rsync -a --files-from=- ./ HOST:~/piyofana/`, then `ssh HOST 'cd ~/piyofana && docker compose up -d --build'`. It never copies `.env` or `data/`.

- [ ] **Step 1:** Write the files. `docker compose config -q` locally → exit 0.
- [ ] **Step 2: Local smoke test.** Local `.env` with `PIYOLOG_FEED_URL=http://invalid.test/v1/feed/24h/x/y`, `POSTGRES_PASSWORD=localonly`, then `docker compose up -d` → collector logs `run status=error` (network), and `docker compose exec postgres psql -U piyolog -c 'select status from collector_runs'` returns 1 row. Then `docker compose down -v` and remove local `data/`.
- [ ] **Step 3: Prepare the host.** `ssh HOST 'mkdir -p ~/piyofana/data/raw && umask 077 && touch ~/piyofana/.env'`. The `POSTGRES_PASSWORD` is generated on the host with `openssl rand -hex 24` appended to `.env`, so no human is needed and it never crosses the wire. `PIYOLOG_FEED_URL` and `FEED_EXPIRES_ON`: the operator has authorized using the current (to-be-rotated) URL for this milestone, so append it on the host; `FEED_EXPIRES_ON` = feed creation date + 90 days, which the operator confirms. Verify `stat -c %a ~/piyofana/.env` → `600`.
- [ ] **Step 4: Deploy.** `scripts/deploy.sh <user>@<host>` (both from `docs/local/host-notes.md`) → `docker compose ps` shows both services `running`, postgres `healthy`. Confirm nothing is published: `docker compose port postgres 5432` → empty. Confirm co-resident stacks are untouched: `docker ps` shows the same pre-existing containers and uptimes as before (names in `docs/local/host-notes.md`).
- [ ] **Step 5: Verify real ingest.** Within 1 minute of start: `psql -c "select type, count(*) from events group by 1 order by 1"` shows the real types, and `select status, records_seen, records_new from collector_runs order by ran_at desc limit 1` → `ok`. After 10 minutes there are two runs, and the second has `records_new` ≈ 0. `zcat ~/piyofana/data/raw/*.json.gz | wc -l` ≥ 2. `docker compose logs collector | grep -c feed.piyolog` → 0.
- [ ] **Step 6: Reboot-survival check (operator-approved only).** Ask the operator before rebooting. Otherwise verify `docker compose restart` restores both services.
- [ ] **Step 7: Hand off to the operator for URL rotation.** They create the new feed, replace the line in `~/piyofana/.env` themselves, update `FEED_EXPIRES_ON`, and run `docker compose up -d collector`. Next run: `ok` with the new URL. Then update the spec's "Known exposure" to "rotated <date>".
- [ ] **Step 8: Commit** `feat: compose stack and deploy script`.

### Task 7: Derived views

**Files:**
- Create: `collector/sql/002_views.sql`, `tests/test_views.py`

**Interfaces:**
- Consumes: `events` table (Task 3), migrate (Task 3; new file picked up automatically)
- Produces:
  - `v_sleep_sessions(sleep_start timestamptz, sleep_end timestamptz, hours float, properly_paired bool)`: the spec's SQL verbatim
  - `v_daily(day date, total_ml float, breast_sec float, feed_count int, pee_count int, poop_count int, longest_feed_gap_min float, temperature_max float, sleep_hours float, partial bool)`. `day` = JST date. `total_ml` sums `value_num` for `Formula, ExpressedBreastMilk, Drink` only. `breast_sec` = `coalesce(left_sec,0)+coalesce(right_sec,0)`. Feed events = `BreastFeeding, Formula, ExpressedBreastMilk`. `longest_feed_gap_min` = max gap between consecutive feed events whose *later* event falls on that day. `partial = day = (now() AT TIME ZONE 'Asia/Tokyo')::date`. Days with no events produce no row.
  - `v_growth(ts timestamptz, day date, metric text, value float, unit text)`: rows for `Height, Weight, Head, Chest` from `value_num`/`value_unit`, plus `Foot` expanded into `foot_left`/`foot_right` from `raw->'value'`
  - Sleep clipping algorithm for `v_daily.sleep_hours` (not determined by the signature):

```sql
-- per JST day d: sum over paired sessions of
--   greatest(0, extract(epoch from least(sleep_end, day_end(d)) - greatest(sleep_start, day_start(d))))/3600
-- day_start(d) = (d::timestamp AT TIME ZONE 'Asia/Tokyo'), day_end(d) = day_start(d + 1)
-- only properly_paired = true sessions; open/unpaired contribute 0
```

- [ ] **Step 1: Write failing tests** (insert crafted events via `upsert_events`):
  - `test_daily_total_ml_excludes_pumping` *(RF 3)*: Formula 40 + EBM 30 + Pumping 30 → `total_ml == 70`
  - `test_daily_jst_bucketing`: an event at `14:59Z` and one at `15:00Z` land on different days
  - `test_daily_breast_sec_single_side`: left only 492.3 → `breast_sec == 492.3`
  - `test_daily_longest_gap`: feeds at 01:00, 03:30, 04:00 JST → 150.0
  - `test_sleep_paired`, `test_sleep_missed_wakeup` (Sleep, Sleep, WakeUp → first `properly_paired=false`), `test_sleep_open` (`sleep_end is None`), `test_sleep_orphan_wakeup` (0 sessions)
  - `test_sleep_crosses_midnight`: 20:00→06:00 JST → day 1 has 4.0 h, day 2 has 6.0 h
  - `test_views_empty_db`: every view returns 0 rows without error (the empty sleep panels depend on this)
  - `test_growth_foot_expanded`: one Foot → two rows `foot_left`, `foot_right`
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** with `CREATE OR REPLACE VIEW`. **Step 4: Run** → pass.
- [ ] **Step 5:** Redeploy (`scripts/deploy.sh`) and run `psql -c 'select * from v_daily order by day desc limit 3'` on the host → plausible real numbers.
- [ ] **Step 6: Commit** `feat: sleep, daily and growth views`.

### Task 8: Read-only role, PDC agent, Grafana datasource

**Files:**
- Create: `collector/sql/003_grafana_ro.sql`, `tests/test_grafana_role.py`
- Modify: `collector/store.py` (`migrate` also sets the role password), `docker-compose.yml` (add `pdc-agent`), `.env.example`

**Interfaces:**
- Produces:
  - Role `grafana_ro` (LOGIN). `GRANT SELECT` on `v_daily, v_sleep_sessions, v_growth, collector_runs`. Column-level `GRANT SELECT (event_id, ts, type, category, value_num, value_unit, left_sec, right_sec, last_side, poop_amount, poop_hardness, poop_color) ON events`. **No `memo`, no `raw`.** The SQL file uses `DO $$ … IF NOT EXISTS … CREATE ROLE … $$`.
  - `migrate(conn, *, grafana_ro_password: str | None = None)`. When a password is given, it runs `ALTER ROLE grafana_ro PASSWORD <literal>` via `psycopg.sql.Literal`. The collector passes env `GRAFANA_RO_PASSWORD`.
  - compose `pdc-agent`: `grafana/pdc-agent:latest`, command `-token ${GCLOUD_PDC_SIGNING_TOKEN} -cluster ${GCLOUD_PDC_CLUSTER} -gcloud-hosted-grafana-id ${GCLOUD_HOSTED_GRAFANA_ID} -ssh-flag='-o PermitRemoteOpen=postgres:5432'`, network `piyofana`, `restart: unless-stopped`
  - `.env.example` adds `GRAFANA_RO_PASSWORD=`, `GCLOUD_PDC_SIGNING_TOKEN=`, `GCLOUD_PDC_CLUSTER=`, `GCLOUD_HOSTED_GRAFANA_ID=`

- [ ] **Step 1: Write failing tests:** `test_ro_can_read_views`, `test_ro_cannot_read_memo` (`select memo from events` as `grafana_ro` → `InsufficientPrivilege`), `test_ro_cannot_read_raw`, `test_ro_cannot_write` (insert → `InsufficientPrivilege`), `test_migrate_role_idempotent`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass.
- [ ] **Step 5: Operator console step.** In the personal stack, Connections → Private data source connect → create network `piyofana` → generate a signing token. The operator appends the three `GCLOUD_*` values to the host `.env` themselves. `GRAFANA_RO_PASSWORD` is generated on the host with `openssl rand -hex 24`.
- [ ] **Step 6: Deploy** → `docker compose logs pdc-agent` shows a connected tunnel. In the console, the PDC network shows the agent as connected.
- [ ] **Step 7: Create the datasource with gcx** (`--context` set to the personal stack from host-notes). Use `gcx datasources schemas get --type grafana-postgresql-datasource` to build the manifest: host `postgres:5432`, db `piyolog`, user `grafana_ro`, `sslmode=disable` (the PDC tunnel is SSH-encrypted; the hop stays on the compose network), password via `secure: {password: {fromEnv: GRAFANA_RO_PASSWORD}}`, and the PDC network selected per the schema's secure-socks-proxy fields. **Verify these field names from the schema output; do not guess.** Run with `--dry-run` first, then apply. `gcx datasources health <uid>` → exit 0.
- [ ] **Step 8: Commit** `feat: read-only grafana role and PDC agent`.

### Task 9: Dashboards and alerts

**Files:**
- Create: `dashboards/today.json`, `dashboards/trends.json`, `dashboards/growth.json`

**Interfaces:**
- Consumes: the datasource uid from Task 8, and the views from Task 7
- Use the `gcx:create-dashboard` skill (snapshot feedback loop) to build the dashboards. All dashboards use timezone `Asia/Tokyo`.
  - **Today:** stats for "time since last feed" (`now() - max(ts)` over feed types), "time since last poop", "intake today (ml)", "breast time today (min)"; a state-timeline of today's events by `category` from `events`; a "pipeline" stat for minutes since the last `collector_runs.ran_at`.
  - **Trends:** bars from `v_daily` for `total_ml`, `feed_count`, `pee_count`/`poop_count`, `longest_feed_gap_min`, `sleep_hours`, over `$__timeFilter(day)`.
  - **Growth:** time series from `v_growth` per `metric`.
  - **Empty state:** every panel sets `noValue` to a human message. Sleep panels use "No sleep logged yet", never `0`.
- Alerts (via `gcx alert`, folder `piyolog`, evaluated every 5 min):
  - `piyolog-stale`: `SELECT extract(epoch from now()-max(ran_at))/60 FROM collector_runs` > 30
  - `piyolog-last-run-error`: latest `collector_runs.status = 'error'` for 2 consecutive evaluations
  - `piyolog-feed-expiring`: `(SELECT feed_expires_on FROM collector_runs ORDER BY ran_at DESC LIMIT 1) - current_date` < 14
  - The contact point is the operator's choice. Ask before creating one; never post to a shared or team channel.

- [ ] **Step 1:** Build the dashboards with gcx and pull them to `dashboards/*.json`. Snapshot each dashboard; verify the sleep panels render the empty-state text.
- [ ] **Step 2:** Create the alert rules with `--dry-run`, then apply. Verify the stale rule by stopping the collector (`docker compose stop collector`); the rule fires within 35 min, then restart. **Confirm with the operator before stopping the collector.**
- [ ] **Step 3:** Run `scripts/scan.sh`, which must pass because dashboard JSON can carry datasource uids and stack URLs. Strip any stack URL from the JSON. Datasource uids are acceptable.
- [ ] **Step 4: Commit** `feat: Today/Trends/Growth dashboards and pipeline alerts`.

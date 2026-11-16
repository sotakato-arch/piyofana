# PiyoLog → Postgres → Grafana Cloud

**Date:** 2026-11-16
**Status:** Approved design, not yet implemented

## Problem

The PiyoLog data feed exposes only a sliding window of recent records (24h on the
free tier). It is not a history API. Any record that falls out of the window before
being captured is lost permanently. To get dashboards covering weeks or months, we
must capture the window continuously and keep our own durable copy.

## Constraints

Established from the vendor docs and from validating a live response on 2026-11-16.

- **Endpoint:** `GET https://feed.piyolog.com/v1/feed/{period}/{feed_id}/{secret}`
- **The URL is the credential.** No headers, no API key. Anyone holding it can read
  the records. Treat it exactly as a password.
- **Free tier:** 1 feed, `24h` period only, expires after 90 days. On expiry a new
  feed must be created and it gets a *different* URL.
- **Cache:** 24h feeds regenerate at most once per minute. Poll no more than ~1/min.
- **Volume:** 31 records in the validated 24h window — roughly 11k rows/year.
- **Errors:** 404 `feed_unavailable`, 413 `feed_response_too_large`,
  429 `rate_limit_exceeded`, 500 `internal_error`.

### Payload facts verified against a live response

The published schema is incomplete. These were confirmed empirically and the
implementation must honour them, not the docs:

| Field | Reality |
|---|---|
| `leftTime` / `rightTime` | **Floats**, e.g. `573.97624194622`. Not integers. |
| `leftTime` / `rightTime` | **Independently optional** — single-side feeds occur. |
| `last` | **Optional** (absent on 1 of 7 `BreastFeeding` records). |
| `memo` | **Undocumented**, free-text string. Observed on `Pumping`. |
| `Poop.details` | **Optional** — absent on all observed `Poop` records. |
| `value` | Consistently `{"value": N, "unit": "ml"}`. |
| `event_id` | Stable 32-char id, unique within a window. |
| ordering | Records arrive ascending by `datetime`. |
| `range` | Exactly `generated_at - 24h`. |

**Rule for the implementation: every field except `event_id`, `datetime` and `type`
is optional.** Parse defensively; never assume a type-specific field is present.

## Architecture

Everything that holds data runs on an always-on Linux host on the operator's private
network, reached over a mesh VPN.
Grafana Cloud holds only dashboards and alerts, reaching the database through a
Private Data source Connect agent that dials outbound — no inbound ports.

```
home-lab host — docker compose, all restart: unless-stopped, nothing published

  collector  ── every 10 min ──> GET feed (full 24h window each poll)
        │                           parse defensively → normalize → UPSERT
        ▼
  postgres  ← SYSTEM OF RECORD        (no published port; compose network only)
        │  events          one row per event_id
        │  collector_runs  heartbeat + health
        │  views           sleep sessions, daily rollups, growth
        ▼
  pdc-agent ── outbound SSH ──> Grafana Cloud
                                  └─ Postgres datasource (via PDC)
                                  └─ dashboards: Today / Trends / Growth
                                  └─ alerts: staleness, feed expiry

  ./data/pgdata/            database volume
  ./data/raw/YYYY-MM-DD.json.gz   untouched responses, rebuild insurance
```

### Why Postgres and not Loki

Loki was the earlier choice and was rejected after checking its limits. It enforces
`reject_old_samples_max_age` (default one week) and accepts out-of-order writes only
~1 hour behind a stream's newest entry. PiyoLog users routinely enter records hours
after the fact, so those records would be silently dropped and daily totals would be
quietly wrong. Postgres has no such constraint, and `UPSERT` makes a late-arriving
record simply correct the affected day on the next poll.

### Why the raw archive as well

`raw/YYYY-MM-DD.json.gz` stores responses untouched. Normalization logic will have
bugs; re-deriving from the original payload is the only way to recover from one
without re-collecting data that no longer exists upstream. Gzipped, this is a few MB
per year. It is insurance, not a query path.

## Host environment

The target is an always-on Linux host, verified on 2026-11-16. Host specifics are kept
out of this document deliberately; see "Operator notes" below. The deployment is shaped
by these constraints:

| Constraint | Consequence |
|---|---|
| **No passwordless sudo** | No package installs, no writing under `/etc`, no system-level units |
| **User lingering disabled** | `systemctl --user` units would not survive logout or reboot |
| Docker daemon runs as a system service, enabled at boot; service user is in the `docker` group | Containers with `restart: unless-stopped` provide boot persistence with no sudo and no lingering |
| Other compose stacks already run on the host | Ours is an independent project and must not share or touch another stack's database, volumes, or network |
| Host timezone is `Asia/Tokyo` | Matches the JST handling below |
| Ample RAM and disk headroom for a small Postgres, the collector, and the agent | No resource tuning required at this scale |

**Therefore: no systemd.** The stack is a single `docker compose` project owned by the
service user. Scheduling is a loop inside the collector container (collect, sleep 600,
repeat) with signal handling for clean shutdown, rather than an external timer. The
collect function stays pure and unit-testable; the loop is the only untested part and
is deliberately trivial.

The PDC agent runs as `grafana/pdc-agent:latest` in the same compose project, joined to
the same network, and is restricted with `-ssh-flag` / `PermitRemoteOpen` so it can
reach only `postgres:5432` and nothing else on the home network.

The Grafana target is the operator's personal Grafana Cloud stack (Grafana 13.4.0),
chosen deliberately over any employer-owned or customer-facing stack: this is personal
health data about a child and does not belong where colleagues, admins, or corporate
retention policy reach it. PDC itself has no CLI coverage in `gcx`, so creating the PDC
network and attaching it to the datasource is a one-time console step.

## Data model

```sql
CREATE TABLE events (
    event_id      text PRIMARY KEY,
    ts            timestamptz NOT NULL,
    type          text        NOT NULL,
    category      text        NOT NULL,   -- feeding|sleep|diaper|growth|health|care|other
    value_num     numeric,                -- value.value
    value_unit    text,                   -- value.unit
    left_sec      numeric,                -- float, nullable
    right_sec     numeric,                -- float, nullable
    last_side     text,                   -- nullable
    poop_amount   text,
    poop_hardness text,
    poop_color    text,
    memo          text,                   -- see Privacy
    raw           jsonb       NOT NULL,
    first_seen    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX events_ts_idx      ON events (ts DESC);
CREATE INDEX events_type_ts_idx ON events (type, ts DESC);

CREATE TABLE collector_runs (
    ran_at          timestamptz PRIMARY KEY DEFAULT now(),
    status          text NOT NULL,        -- ok|rate_limited|error
    http_status     int,
    error_code      text,
    records_seen    int,
    records_new     int,
    feed_expires_on date
);
```

`ts` is `timestamptz` (stored UTC). All presentation is JST via
`AT TIME ZONE 'Asia/Tokyo'`; dashboards set timezone `Asia/Tokyo`. A "day" means a
JST day — bucketing by UTC would cut every day at 09:00 local.

Ingest is `INSERT ... ON CONFLICT (event_id) DO UPDATE`, making every poll idempotent
and overlapping windows harmless.

### Derivation in SQL, not Python

Because the store is relational, derivation is views rather than code. Sleep pairing
is a window function:

```sql
CREATE VIEW v_sleep_sessions AS
WITH s AS (
    SELECT ts, type,
           LEAD(ts)   OVER (ORDER BY ts) AS next_ts,
           LEAD(type) OVER (ORDER BY ts) AS next_type
    FROM events WHERE type IN ('Sleep','WakeUp')
)
SELECT ts AS sleep_start,
       next_ts AS sleep_end,
       EXTRACT(EPOCH FROM next_ts - ts)/3600.0 AS hours,
       next_type = 'WakeUp' AS properly_paired
FROM s WHERE type = 'Sleep';
```

This handles the awkward cases without special-casing: a missed `WakeUp` yields
`properly_paired = false`, a still-open sleep yields `sleep_end IS NULL`, and an
orphan `WakeUp` is simply never selected. No duration is ever invented.

`v_daily` rolls up per JST date: total ml, breastfeeding seconds, feed/pee/poop
counts, longest feed gap, max temperature, and sleep hours computed by clipping each
session to the day boundary (so a 20:00→06:00 night splits across two dates rather
than being double-counted). `v_growth` emits measurements long-format
(`date, metric, value, unit`) so new metrics need no schema change.

## Operations

| Condition | Behaviour |
|---|---|
| `404 feed_unavailable` | Record `status=error` and keep polling (a non-zero exit would make `restart: unless-stopped` crash-loop). Feed expired/revoked — needs a human; surfaced by the last-run-error alert. `--once` mode exits non-zero. |
| `429 rate_limit_exceeded` | Record `status=rate_limited`, exit 0. Next run is 10 min away. |
| `413` | Record and fail. Should not occur at 24h / 1 child. |
| `5xx` | Retry twice with backoff, then record and exit 0. |
| Any error path | The feed URL is redacted from all output. Enforced by a unit test. |

**Staleness alerting uses a heartbeat, not activity.** Every run writes to
`collector_runs` regardless of outcome. A Grafana alert fires when
`now() - max(ran_at) > 30 min`. Alerting on "no baby events for N hours" would be
ambiguous because quiet stretches are normal; a missing heartbeat is unambiguous.

**Feed expiry** is tracked as a non-secret `FEED_EXPIRES_ON` ISO date in `.env` (no YAML dependency),
written to each heartbeat row, with an alert at 14 days remaining. Without this, the
90-day expiry would surface as a dashboard that silently stopped weeks earlier.

## Security and privacy

- The feed URL lives only in `./.env` on the host, mode `600`, owned by the service
  user and loaded via compose `env_file`. Never in the repo, never in logs, never in
  argv. (`/etc` would need sudo, which this host does not grant passwordlessly.)
- `.gitignore` excludes `.env` from the first commit. `.env.example` carries names
  only, no values.
- The repository is **public** and contains code only. No records are committed and no
  host identifiers are published; the database and raw archive stay on the host. Every
  commit is checked for records, credentials, and infrastructure details before push.
- Grafana Cloud reaches the database only through the PDC agent's outbound SSH
  tunnel, restricted by `PermitRemoteOpen` to `postgres:5432`. The database publishes
  no host port, so it is unreachable even from the LAN. No inbound exposure.
- The PDC signing token (`pdc-signing:write`) is stored alongside the feed URL in the
  same mode-`600` `.env`.
- `memo` is free text and may contain anything. It is stored but **not displayed on
  any dashboard by default**, and is excluded from the Grafana datasource's
  permitted columns unless explicitly wanted.

### Known exposure

The feed URL was pasted into a chat transcript during design on 2026-11-16 and must
be rotated: delete the feed in-app and create a new one. Free tier allows only one
feed, so this is delete-then-create. Blast radius is read-only record access, no
account access.

## Scope

**In:** collector, schema, views, three dashboards (Today / Trends / Growth),
heartbeat and expiry alerts, PDC connection.

**Out (deliberately):** multi-child support (one feed on free tier), WHO growth
percentile overlays, automated dashboard provisioning (manual import avoids another
credential), any write path back to PiyoLog.

**Sleep, decided 2026-11-16:** `Sleep`/`WakeUp` are not currently logged — the
validated window contained none — but the owner intends to start. `v_sleep_sessions`
and the sleep panels are therefore built in v1 and will render empty until records
begin arriving. Panels must degrade gracefully on an empty result rather than showing
an error or a misleading zero.

## Dependencies

Runtime: `httpx` (feed client) and `psycopg[binary]` 3 (Postgres). Test: `pytest`. Locked with `uv`; everything, including tests, runs in Docker.

## Testing

Built test-first. Fixtures are synthetic, modelled on the real payload's shape
including its optional-field quirks — no real records in the repo.

- `test_normalize` — missing `last`, missing `leftTime`, missing `rightTime`, float
  seconds, absent `Poop.details`, unknown/future `type`, `memo` present and absent.
- `test_ingest` — idempotency: the same window ingested twice yields no new rows;
  a record re-sent with changed fields updates in place.
- `test_derive` — sleep crossing midnight, missed `WakeUp`, orphan `WakeUp`, open
  session, JST day bucketing at the UTC boundary.
- `test_redaction` — the feed URL never appears in log output or exception text on
  any error path.

## Operator notes

Host-specific details — hostname, VPN address, OS and kernel versions, co-resident
services, resource figures, and which ports are already in use — are intentionally
absent from this repository because it is public. They live in `docs/local/`, which is
gitignored. Publishing them would make the host enumerable to no design benefit; the
constraints above are the only part the implementation actually needs.

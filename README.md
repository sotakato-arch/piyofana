# piyofana 🐣

Keep your [PiyoLog (ぴよログ)](https://www.piyolog.com/) baby-tracking history forever and explore it in Grafana.

PiyoLog's [data feed](https://www.piyolog.com/app/piyolog/data_feed/ja/) only exposes a sliding 24-hour window. piyofana polls that feed from a small Docker Compose stack on a home server, stores every record in Postgres, and lets Grafana Cloud query it through Private Data Source Connect (PDC). Nothing on the home network is exposed to the internet.

```
PiyoLog app ──▶ PiyoLog data feed (24h JSON, secret URL)
                        │ HTTPS GET every 10 min
┌───────────────────────▼──────── home server · docker compose ─┐
│  collector (Python) ──upsert──▶ Postgres 17 (no published port) │
│        │                              ▲                         │
│        ▼                              │ postgres:5432 only      │
│  raw archive (gzip JSON/day)      PDC agent ── outbound SSH ──┐ │
└───────────────────────────────────────────────────────────────┼─┘
                                                                ▼
              Grafana Cloud: PDC gateway ─▶ PostgreSQL datasource (read-only role)
                                                 ─▶ dashboard · alerts
```

## What's inside

| Path | What it is |
|------|-----------|
| `collector/` | Python collector: fetch → archive raw JSON → normalize → idempotent upsert (keyed on PiyoLog `event_id`) → heartbeat row |
| `collector/sql/` | Schema, derived views (`v_daily`, `v_sleep_sessions`, `v_growth`) bucketed by JST day, and a read-only `grafana_ro` role that cannot read memos |
| `docker-compose.yml` | `postgres`, `collector`, and an optional `pdc-agent` (profile `pdc`) restricted to `postgres:5432` |
| `dashboards/` | Grafana dashboard + folder manifests (Japanese UI) |
| `alerts/` | Grafana alert rule: fewer than 6 pee/poop logs in 24 hours |
| `tests/` | pytest suite (runs in Docker against a throwaway Postgres) |
| `docs/superpowers/` | Design spec and implementation plan |

## Run it

1. Copy `.env.example` to `.env` (mode 600) and fill in your PiyoLog feed URL, a Postgres password and a password for the read-only Grafana role. The feed URL is a credential; never commit it.
2. `docker compose up -d --build`
3. For Grafana Cloud, create a PDC network and signing token, add the three `GCLOUD_PDC_*` values plus `COMPOSE_PROFILES=pdc` to `.env`, and restart.
4. Add a PostgreSQL datasource (host `postgres:5432`, database `piyolog`, user `grafana_ro`, PDC enabled), then push the manifests:
   `gcx resources push -p dashboards && gcx resources push -p alerts`

Tests: `docker compose -f docker-compose.test.yml run --rm --build test`

## Privacy notes

- The feed URL, database passwords and PDC token live only in `.env` on the host.
- Logs redact the feed URL; Postgres publishes no port; the PDC agent can only reach `postgres:5432`.
- Grafana connects as `grafana_ro`, which has column-level grants that exclude free-text memos and raw payloads.
- `scripts/scan.sh` runs before every deploy and fails on feed URLs, tailnet hostnames and private IPs.

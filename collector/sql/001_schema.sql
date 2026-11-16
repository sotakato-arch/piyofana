-- System of record. One row per PiyoLog event_id; re-polls UPSERT in place.
CREATE TABLE IF NOT EXISTS events (
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
    memo          text,                   -- free text; never exposed to Grafana
    raw           jsonb       NOT NULL,
    first_seen    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_ts_idx      ON events (ts DESC);
CREATE INDEX IF NOT EXISTS events_type_ts_idx ON events (type, ts DESC);

-- Heartbeat: one row per collector run, whatever the outcome.
CREATE TABLE IF NOT EXISTS collector_runs (
    ran_at          timestamptz PRIMARY KEY DEFAULT clock_timestamp(),
    status          text NOT NULL,        -- ok|rate_limited|error
    http_status     int,
    error_code      text,
    records_seen    int,
    records_new     int,
    records_invalid int,
    feed_expires_on date
);

-- Read-only login for Grafana. It sees the views, the heartbeat table, and
-- every events column except the free-text memo and the raw payload.
-- The password is set by migrate() from GRAFANA_RO_PASSWORD, never here.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN
        CREATE ROLE grafana_ro LOGIN;
    END IF;
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO grafana_ro', current_schema());
END
$$;

REVOKE ALL ON events FROM grafana_ro;
GRANT SELECT (event_id, ts, type, category, value_num, value_unit, left_sec, right_sec,
              last_side, poop_amount, poop_hardness, poop_color)
    ON events TO grafana_ro;
GRANT SELECT ON v_daily, v_sleep_sessions, v_growth, collector_runs TO grafana_ro;

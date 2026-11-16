-- Derived views. A "day" is a JST calendar day; storage stays UTC.

-- Sleep→WakeUp pairing by window function. A missed WakeUp gives
-- properly_paired = false, an open sleep gives sleep_end IS NULL, and an
-- orphan WakeUp is never selected. No duration is ever invented.
CREATE OR REPLACE VIEW v_sleep_sessions AS
WITH s AS (
    SELECT ts, type,
           LEAD(ts)   OVER (ORDER BY ts) AS next_ts,
           LEAD(type) OVER (ORDER BY ts) AS next_type
    FROM events WHERE type IN ('Sleep', 'WakeUp')
)
SELECT ts AS sleep_start,
       next_ts AS sleep_end,
       (EXTRACT(EPOCH FROM next_ts - ts) / 3600.0)::float8 AS hours,
       coalesce(next_type = 'WakeUp', false) AS properly_paired
FROM s WHERE type = 'Sleep';

CREATE OR REPLACE VIEW v_daily AS
WITH e AS (
    SELECT *, (ts AT TIME ZONE 'Asia/Tokyo')::date AS day FROM events
),
base AS (
    SELECT day,
           coalesce(sum(value_num) FILTER (WHERE type IN ('Formula', 'ExpressedBreastMilk', 'Drink')), 0)::float8 AS total_ml,
           coalesce(sum(coalesce(left_sec, 0) + coalesce(right_sec, 0)) FILTER (WHERE type = 'BreastFeeding'), 0)::float8 AS breast_sec,
           (count(*) FILTER (WHERE type IN ('BreastFeeding', 'Formula', 'ExpressedBreastMilk')))::int AS feed_count,
           (count(*) FILTER (WHERE type = 'Pee'))::int AS pee_count,
           (count(*) FILTER (WHERE type = 'Poop'))::int AS poop_count,
           (max(value_num) FILTER (WHERE type = 'Temperature'))::float8 AS temperature_max
    FROM e GROUP BY day
),
-- A gap belongs to the day of its later feed.
gaps AS (
    SELECT day, max(EXTRACT(EPOCH FROM gap) / 60.0)::float8 AS longest_feed_gap_min
    FROM (
        SELECT day, ts - LAG(ts) OVER (ORDER BY ts) AS gap
        FROM e WHERE type IN ('BreastFeeding', 'Formula', 'ExpressedBreastMilk')
    ) f
    GROUP BY day
),
-- Each paired session is clipped to the JST day boundary, so a night splits
-- across two days. Days with no paired sleep get NULL, never a misleading 0.
sleep AS (
    SELECT b.day,
           (sum(EXTRACT(EPOCH FROM least(s.sleep_end, b.day_end) - greatest(s.sleep_start, b.day_start))) / 3600.0)::float8 AS sleep_hours
    FROM (
        SELECT day,
               (day::timestamp AT TIME ZONE 'Asia/Tokyo') AS day_start,
               ((day + 1)::timestamp AT TIME ZONE 'Asia/Tokyo') AS day_end
        FROM base
    ) b
    JOIN v_sleep_sessions s
      ON s.properly_paired AND s.sleep_start < b.day_end AND s.sleep_end > b.day_start
    GROUP BY b.day
)
SELECT base.day,
       base.total_ml,
       base.breast_sec,
       base.feed_count,
       base.pee_count,
       base.poop_count,
       gaps.longest_feed_gap_min,
       base.temperature_max,
       sleep.sleep_hours,
       base.day = (now() AT TIME ZONE 'Asia/Tokyo')::date AS partial
FROM base
LEFT JOIN gaps USING (day)
LEFT JOIN sleep USING (day);

-- Growth measurements, long format. Foot is expanded from raw; its exact
-- shape is unconfirmed, so both a bare number and {value, unit} are accepted.
CREATE OR REPLACE VIEW v_growth AS
SELECT ts,
       (ts AT TIME ZONE 'Asia/Tokyo')::date AS day,
       lower(type) AS metric,
       value_num::float8 AS value,
       value_unit AS unit
FROM events
WHERE type IN ('Height', 'Weight', 'Head', 'Chest') AND value_num IS NOT NULL
UNION ALL
SELECT ts,
       (ts AT TIME ZONE 'Asia/Tokyo')::date,
       'foot_' || side,
       CASE jsonb_typeof(v) WHEN 'number' THEN (v #>> '{}')::float8
                            WHEN 'object' THEN (v ->> 'value')::float8 END,
       CASE jsonb_typeof(v) WHEN 'object' THEN coalesce(v ->> 'unit', 'cm') ELSE 'cm' END
FROM events
CROSS JOIN LATERAL (VALUES ('left', raw -> 'value' -> 'left'), ('right', raw -> 'value' -> 'right')) AS f(side, v)
WHERE type = 'Foot' AND jsonb_typeof(v) IN ('number', 'object');

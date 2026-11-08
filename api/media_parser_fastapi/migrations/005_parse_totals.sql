-- Apply before deploying the API, with parse traffic paused during backfill.
-- Re-running preserves any larger live counter instead of adding history again.
CREATE TABLE IF NOT EXISTS app_parse_totals (
  appid VARCHAR(64) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
  total_parses BIGINT UNSIGNED NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

INSERT INTO app_parse_totals (appid, total_parses)
SELECT u.appid, COUNT(*) FROM parse_requests p JOIN users u ON u.id=p.user_id
WHERE p.status='succeeded' GROUP BY u.appid
ON DUPLICATE KEY UPDATE total_parses=GREATEST(total_parses, VALUES(total_parses));

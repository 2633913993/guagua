-- MySQL 8.0 / InnoDB. Select your existing application schema before running.
-- Additive, repeatable migration: no DROP, TRUNCATE, or modification of user/quota data.
CREATE TABLE IF NOT EXISTS download_tasks (
  task_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
  owner_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  -- NULL on completion. The unique key permits only one active task per owner.
  active_owner_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  result_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  expires_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_download_active_owner (active_owner_key),
  KEY ix_download_owner_created (owner_key, created_at),
  KEY ix_download_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Optional scheduled housekeeping (not executed by this migration):
-- DELETE FROM download_tasks WHERE expires_at < UTC_TIMESTAMP(6) LIMIT 1000;
-- COS objects are cleaned independently through the downloads/ lifecycle rule.

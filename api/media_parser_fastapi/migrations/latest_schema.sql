-- Full current schema, MySQL 8.0. Select the application schema first.
-- Repeatable CREATE TABLE IF NOT EXISTS; existing tables and data are preserved.
-- Existing installations apply missing migrations through 005_parse_totals.sql.
-- Pause parse traffic during the totals backfill at the end of this script.
-- IF NOT EXISTS does not repair incompatible pre-existing column definitions.

-- MySQL 8.0 / InnoDB. Run once against an empty, dedicated application schema.
CREATE TABLE IF NOT EXISTS users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  appid VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
  openid VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  created_at DATETIME(6) NOT NULL,
  last_seen_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_users_identity (appid, openid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS daily_quotas (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL,
  quota_date DATE NOT NULL,
  initial_quota INT NOT NULL,
  remaining INT NOT NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_quota_day (user_id, quota_date),
  FOREIGN KEY (user_id) REFERENCES users(id),
  CHECK (initial_quota >= 0 AND remaining >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS parse_requests (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL,
  request_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
  payload_hash CHAR(64) COLLATE ascii_bin NOT NULL,
  quota_date DATE NOT NULL,
  status VARCHAR(16) NOT NULL,
  result_json JSON NULL,
  error_code VARCHAR(64) NULL,
  started_at DATETIME(6) NOT NULL,
  finished_at DATETIME(6) NULL,
  UNIQUE KEY uq_parse_request (user_id, request_id),
  FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS quota_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL,
  quota_date DATE NOT NULL,
  event_type VARCHAR(32) NOT NULL,
  amount INT NOT NULL,
  balance_after INT NOT NULL,
  business_key VARCHAR(100) COLLATE utf8mb4_bin NOT NULL,
  created_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_log_business (user_id, business_key),
  FOREIGN KEY (user_id) REFERENCES users(id),
  CHECK (balance_after >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS share_invites (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  invite_code VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
  inviter_id BIGINT UNSIGNED NOT NULL,
  invitee_id BIGINT UNSIGNED NULL,
  campaign_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  reward_date DATE NULL,
  created_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_invite_code (invite_code),
  -- At most one effective inviter per invitee per campaign. NULL = unclaimed.
  UNIQUE KEY uq_invite_relation (invitee_id, campaign_id),
  FOREIGN KEY (inviter_id) REFERENCES users(id),
  FOREIGN KEY (invitee_id) REFERENCES users(id),
  CHECK (invitee_id IS NULL OR inviter_id <> invitee_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Existing installations: apply once after 001_quotas.sql.
-- New installations: apply 001 followed by 002. No existing data is changed.
CREATE TABLE IF NOT EXISTS share_links (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL,
  invite_token VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
  created_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_share_link_user (user_id),
  UNIQUE KEY uq_share_link_token (invite_token),
  FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS share_visits (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  inviter_id BIGINT UNSIGNED NOT NULL,
  invitee_id BIGINT UNSIGNED NOT NULL,
  source VARCHAR(32) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  last_seen_at DATETIME(6) NOT NULL,
  visit_count INT UNSIGNED NOT NULL DEFAULT 1,
  reward_amount INT NOT NULL DEFAULT 0,
  UNIQUE KEY uq_share_visit (inviter_id, invitee_id),
  KEY ix_share_visit_list (inviter_id, id),
  FOREIGN KEY (inviter_id) REFERENCES users(id),
  FOREIGN KEY (invitee_id) REFERENCES users(id),
  CHECK (inviter_id <> invitee_id),
  CHECK (reward_amount >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

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


-- Fixed rewards are granted atomically with the balance and quota log.
CREATE TABLE IF NOT EXISTS ad_reward_sessions (
  session_id CHAR(43) COLLATE ascii_bin NOT NULL PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL,
  quota_date DATE NOT NULL,
  ad_unit_id VARCHAR(128) COLLATE ascii_bin NOT NULL,
  created_at DATETIME(6) NOT NULL,
  expires_at DATETIME(6) NOT NULL,
  completed_at DATETIME(6) NULL,
  KEY ix_ad_owner_pending (user_id, quota_date, completed_at, expires_at),
  FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;


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

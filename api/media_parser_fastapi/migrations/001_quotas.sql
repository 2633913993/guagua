-- MySQL 8.0 / InnoDB. Run once against an empty, dedicated application schema.
CREATE TABLE users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  appid VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
  openid VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  created_at DATETIME(6) NOT NULL,
  last_seen_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_users_identity (appid, openid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE daily_quotas (
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

CREATE TABLE parse_requests (
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

CREATE TABLE quota_logs (
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

CREATE TABLE share_invites (
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

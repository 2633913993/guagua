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

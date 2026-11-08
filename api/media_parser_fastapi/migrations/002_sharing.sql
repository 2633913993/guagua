-- Existing installations: apply once after 001_quotas.sql.
-- New installations: apply 001 followed by 002. No existing data is changed.
CREATE TABLE share_links (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL,
  invite_token VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
  created_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_share_link_user (user_id),
  UNIQUE KEY uq_share_link_token (invite_token),
  FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE share_visits (
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

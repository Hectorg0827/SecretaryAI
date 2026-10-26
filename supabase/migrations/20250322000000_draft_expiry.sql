-- Migration: add expires_at to drafts table for approval expiry (Tier 2-13)
-- Drafts pending beyond this timestamp are auto-archived by the nightly task.
-- Existing drafts backfilled from creation; new drafts default to 30 days.

ALTER TABLE drafts ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
UPDATE drafts SET expires_at = coalesce(created_at, now()) + interval '30 days'
  WHERE expires_at IS NULL;
ALTER TABLE drafts ALTER COLUMN expires_at SET DEFAULT (now() + interval '30 days');

-- Index to speed up the nightly expiry query
CREATE INDEX IF NOT EXISTS idx_drafts_expires_at
  ON drafts (expires_at)
  WHERE status = 'pending';

-- timestamptz + interval is not IMMUTABLE: a generated column would fail on PostgreSQL.

-- Migration: add expires_at to drafts table for approval expiry (Tier 2-13)
-- Drafts pending beyond this timestamp are auto-archived by the nightly task.
-- Default: 30 days from creation.

ALTER TABLE drafts
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ
    GENERATED ALWAYS AS (created_at + INTERVAL '30 days') STORED;

-- Index to speed up the nightly expiry query
CREATE INDEX IF NOT EXISTS idx_drafts_expires_at
  ON drafts (expires_at)
  WHERE status = 'pending';

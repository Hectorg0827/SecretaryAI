-- Migration: add expires_at to drafts table for approval expiry.
-- Pending drafts past this timestamp are archived by the nightly task.
--
-- This is a plain column the application sets (default: 48 hours after
-- creation). It was previously declared as a GENERATED column, which Postgres
-- rejects ("generation expression is not immutable" — timestamptz + interval
-- depends on the session time zone), so the column never existed and every
-- draft insert failed.

ALTER TABLE drafts
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ DEFAULT (now() + INTERVAL '48 hours');

CREATE INDEX IF NOT EXISTS idx_drafts_expires_at
  ON drafts (expires_at)
  WHERE status = 'pending';

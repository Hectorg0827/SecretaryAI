-- Migration: add file_hash to ingested_files for deduplication (Tier 2-11)
-- SHA-256 of filename + row_count + first-row fingerprint.
-- Unique per company to prevent the same file being ingested twice.

ALTER TABLE ingested_files
  ADD COLUMN IF NOT EXISTS file_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_ingested_files_hash
  ON ingested_files (company_id, file_hash)
  WHERE file_hash IS NOT NULL;

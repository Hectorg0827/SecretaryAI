-- Phase 7: Computer-use job queue + result cache
-- ────────────────────────────────────────────────

-- computer_use_jobs: durable queue for CU tasks + result store.
--
-- Design decisions:
--   - max_attempts=2 (CU is expensive; fewer retries than browser jobs)
--   - expires_at: completed results expire after 1 hour; router ignores stale
--   - screenshot_ref: stores the screenshot key/path for audit trail attachment
--   - Index on (company_id, capability, completed_at) WHERE completed lets the
--     router check for a cached result with a single indexed scan

CREATE TABLE IF NOT EXISTS computer_use_jobs (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid        NOT NULL,
    capability      text        NOT NULL,
    parameters      jsonb       NOT NULL DEFAULT '{}',
    status          text        NOT NULL DEFAULT 'pending',
                                -- pending | running | completed | failed | cancelled
    priority        int         NOT NULL DEFAULT 5,
    correlation_id  text,
    attempt_count   int         NOT NULL DEFAULT 0,
    max_attempts    int         NOT NULL DEFAULT 2,
    result          jsonb,
    confidence_pct  int,
    screenshot_ref  text,       -- storage key for screenshot (audit evidence)
    error_message   text,
    worker_id       text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    completed_at    timestamptz,
    expires_at      timestamptz,  -- router ignores results older than this

    CONSTRAINT cu_status_check CHECK (
        status IN ('pending', 'running', 'completed', 'failed', 'cancelled')
    ),
    CONSTRAINT cu_priority_check CHECK (priority BETWEEN 1 AND 10),
    CONSTRAINT cu_confidence_check CHECK (
        confidence_pct IS NULL OR confidence_pct BETWEEN 0 AND 100
    )
);

-- Fast cache-hit check: most-recent completed result for (company, capability)
CREATE INDEX IF NOT EXISTS idx_cu_jobs_cache
    ON computer_use_jobs (company_id, capability, completed_at DESC)
    WHERE status = 'completed';

-- Worker claim queue
CREATE INDEX IF NOT EXISTS idx_cu_jobs_pending
    ON computer_use_jobs (company_id, priority, created_at)
    WHERE status = 'pending';

-- Retry-eligible failed jobs
CREATE INDEX IF NOT EXISTS idx_cu_jobs_failed_retry
    ON computer_use_jobs (company_id, created_at)
    WHERE status = 'failed';

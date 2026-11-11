-- Phase 6: Browser automation job queue + hardened ingestion queue
-- ─────────────────────────────────────────────────────────────────

-- ── browser_automation_jobs ──────────────────────────────────────────────────
-- DB-backed job queue for browser automation tasks.
-- Replaces inline fire-and-forget calls with a durable, retriable queue.

CREATE TABLE IF NOT EXISTS browser_automation_jobs (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id       uuid        NOT NULL,
    capability       text        NOT NULL,          -- e.g. 'accounts_receivable'
    parameters       jsonb       NOT NULL DEFAULT '{}',
    status           text        NOT NULL DEFAULT 'pending',
                                 -- pending | running | completed | failed | cancelled
    priority         int         NOT NULL DEFAULT 5, -- 1=highest, 10=lowest
    correlation_id   text,
    attempt_count    int         NOT NULL DEFAULT 0,
    max_attempts     int         NOT NULL DEFAULT 3,
    result           jsonb,
    confidence_pct   int,
    error_message    text,
    worker_id        text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    completed_at     timestamptz,
    next_retry_at    timestamptz,

    CONSTRAINT ba_jobs_status_check CHECK (
        status IN ('pending', 'running', 'completed', 'failed', 'cancelled')
    ),
    CONSTRAINT ba_jobs_priority_check CHECK (priority BETWEEN 1 AND 10),
    CONSTRAINT ba_jobs_confidence_check CHECK (
        confidence_pct IS NULL OR confidence_pct BETWEEN 0 AND 100
    )
);

CREATE INDEX IF NOT EXISTS idx_ba_jobs_company_status
    ON browser_automation_jobs (company_id, status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_ba_jobs_correlation
    ON browser_automation_jobs (correlation_id)
    WHERE correlation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ba_jobs_retry
    ON browser_automation_jobs (next_retry_at)
    WHERE status = 'failed' AND next_retry_at IS NOT NULL;


-- ── ingestion_queue ──────────────────────────────────────────────────────────
-- Tracks each file seen by the ingestion scanner.
-- Deduplication: (company_id, file_path, file_hash) is unique — a file is only
-- re-processed if its content changes.

CREATE TABLE IF NOT EXISTS ingestion_queue (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id          uuid        NOT NULL,
    file_path           text        NOT NULL,
    file_hash           text        NOT NULL,    -- SHA-256 of file content
    file_size           bigint,
    entity_type         text,                   -- customers | invoices | inventory | unknown
    status              text        NOT NULL DEFAULT 'pending',
                                    -- pending | processing | done | error | skipped
    rows_ingested       int         NOT NULL DEFAULT 0,
    error_detail        text,
    attempt_count       int         NOT NULL DEFAULT 0,
    max_attempts        int         NOT NULL DEFAULT 3,
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    last_processed_at   timestamptz,
    next_retry_at       timestamptz,

    CONSTRAINT iq_status_check CHECK (
        status IN ('pending', 'processing', 'done', 'error', 'skipped')
    ),
    UNIQUE (company_id, file_path, file_hash)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_queue_company_status
    ON ingestion_queue (company_id, status, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_queue_retry
    ON ingestion_queue (next_retry_at)
    WHERE status = 'error' AND next_retry_at IS NOT NULL;

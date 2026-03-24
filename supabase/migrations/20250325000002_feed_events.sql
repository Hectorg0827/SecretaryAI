CREATE TABLE IF NOT EXISTS feed_events (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    event_type   text NOT NULL,  -- 'dormant_account' | 'low_stock' | 'overdue_invoice' | 'anomaly' | 'first_order'
    title        text NOT NULL,
    body         text NOT NULL,
    priority     text NOT NULL DEFAULT 'medium',  -- 'high' | 'medium' | 'low'
    action_label text,           -- e.g. 'Draft Outreach'
    action_type  text,           -- e.g. 'draft_customer_email'
    action_data  jsonb DEFAULT '{}',
    entity_id    text,           -- account_name or item_id
    entity_name  text,
    is_read      boolean NOT NULL DEFAULT false,
    is_dismissed boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz
);
CREATE INDEX IF NOT EXISTS feed_events_company_idx ON feed_events (company_id, is_dismissed, created_at DESC);
CREATE INDEX IF NOT EXISTS feed_events_entity_idx ON feed_events (company_id, entity_id);

ALTER TABLE feed_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY "feed_events_company_isolation" ON feed_events
    FOR ALL USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));

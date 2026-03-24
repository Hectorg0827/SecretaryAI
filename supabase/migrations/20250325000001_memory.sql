CREATE TABLE IF NOT EXISTS account_memory (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    account_name text NOT NULL,
    facts        jsonb NOT NULL DEFAULT '[]',  -- array of {fact: str, confidence: float, updated_at: str}
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, account_name)
);
CREATE INDEX IF NOT EXISTS account_memory_company_idx ON account_memory (company_id);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id         text NOT NULL,
    conversation_id text NOT NULL UNIQUE,
    summary         text NOT NULL,
    account_names   text[] DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conv_summaries_company_user_idx ON conversation_summaries (company_id, user_id);

-- RLS
ALTER TABLE account_memory ENABLE ROW LEVEL SECURITY;
CREATE POLICY "account_memory_company_isolation" ON account_memory
    FOR ALL USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));

ALTER TABLE conversation_summaries ENABLE ROW LEVEL SECURITY;
CREATE POLICY "conv_summaries_company_isolation" ON conversation_summaries
    FOR ALL USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));

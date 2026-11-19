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

-- Client access is read-only and scoped to verified, server-issued tenant claims.
ALTER TABLE account_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_summaries ENABLE ROW LEVEL SECURITY;
CREATE POLICY "account_memory_company_isolation" ON account_memory
  FOR SELECT TO authenticated
  USING (company_id = (auth.jwt() ->> 'company_id')::uuid);
CREATE POLICY "conv_summaries_company_isolation" ON conversation_summaries
  FOR SELECT TO authenticated
  USING (company_id = (auth.jwt() ->> 'company_id')::uuid);

REVOKE ALL ON TABLE account_memory, conversation_summaries FROM PUBLIC, anon, authenticated;
GRANT SELECT ON TABLE account_memory, conversation_summaries TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE account_memory, conversation_summaries TO service_role;

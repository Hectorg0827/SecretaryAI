-- Which sales rep looks after which customer account.
--
-- QuickBooks does not say which of OUR users owns a customer, so the owner or
-- a manager assigns accounts here. A sales rep sees only the accounts assigned
-- to them; an account with no rep is visible to owners/managers/back office
-- only.
CREATE TABLE IF NOT EXISTS account_assignments (
    company_id     UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    qb_customer_id TEXT NOT NULL,                 -- the customer's ID in QuickBooks
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_by    UUID,
    assigned_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, qb_customer_id)     -- one rep per account
);
CREATE INDEX IF NOT EXISTS idx_account_assignments_user ON account_assignments (company_id, user_id);

ALTER TABLE account_assignments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "company_isolation" ON account_assignments;
CREATE POLICY "company_isolation" ON account_assignments FOR ALL
    USING (company_id = (auth.jwt() ->> 'company_id')::uuid)
    WITH CHECK (company_id = (auth.jwt() ->> 'company_id')::uuid);

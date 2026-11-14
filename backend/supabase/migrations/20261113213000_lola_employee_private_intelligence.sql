-- Lola Private Intelligence Phase 1.
-- Intentionally NOT applied to production by this commit.
-- Custom app JWTs do not equal Supabase Auth JWTs. The backend uses a
-- service-role client, so every API query MUST filter company_id + user_id.
-- Revoke direct anon/authenticated access; RLS is defense in depth.

CREATE TABLE IF NOT EXISTS employee_integrations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider text NOT NULL CHECK (provider IN ('gmail', 'outlook')),
    access_token text,
    refresh_token text,
    connected_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, user_id, provider)
);

CREATE TABLE IF NOT EXISTS lola_private_contacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name text NOT NULL,
    email text,
    phone text,
    identity_verified boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, user_id, id)
);

CREATE INDEX IF NOT EXISTS idx_lola_private_contacts_owner
ON lola_private_contacts(company_id, user_id);

CREATE TABLE IF NOT EXISTS lola_private_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    contact_id uuid NOT NULL,
    project_key text NOT NULL,
    channel text NOT NULL CHECK (channel IN ('email', 'whatsapp', 'note', 'document')),
    direction text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    kind text NOT NULL CHECK (kind IN ('request', 'ack', 'draft_sent',
                                        'quote_sent', 'followup_sent', 'feedback',
                                        'closed', 'other')),
    occurred_at timestamptz NOT NULL,
    summary text NOT NULL DEFAULT '',
    source_ref text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, user_id, id),
    FOREIGN KEY (company_id, user_id, contact_id)
        REFERENCES lola_private_contacts(company_id, user_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_lola_private_events_owner_time
ON lola_private_events(company_id, user_id, occurred_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lola_private_events_provider_dedupe
ON lola_private_events(company_id, user_id, channel, source_ref)
WHERE source_ref IS NOT NULL;

CREATE TABLE IF NOT EXISTS lola_private_followup_state (
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id uuid NOT NULL,
    snoozed_until timestamptz,
    resolved_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, user_id, event_id),
    FOREIGN KEY (company_id, user_id, event_id)
        REFERENCES lola_private_events(company_id, user_id, id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS lola_personal_account_memory (
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_name text NOT NULL,
    facts jsonb NOT NULL DEFAULT '[]'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, user_id, account_name)
);

-- Legacy emails have no established employee owner and remain invisible.
-- Do NOT backfill ownership by company role or by guessed email address.
ALTER TABLE IF EXISTS emails
    ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES users(id);
DO $lola$
BEGIN
    IF to_regclass('public.emails') IS NOT NULL THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_emails_private_owner ON public.emails(company_id, user_id, received_at DESC)';
    END IF;
END
$lola$;

ALTER TABLE employee_integrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE lola_private_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE lola_private_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE lola_private_followup_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE lola_personal_account_memory ENABLE ROW LEVEL SECURITY;

-- Only the verified backend service role can access these tables.
-- There are deliberately NO anon/authenticated policies.
REVOKE ALL ON employee_integrations FROM anon, authenticated;
REVOKE ALL ON lola_private_contacts FROM anon, authenticated;
REVOKE ALL ON lola_private_events FROM anon, authenticated;
REVOKE ALL ON lola_private_followup_state FROM anon, authenticated;
REVOKE ALL ON lola_personal_account_memory FROM anon, authenticated;


-- Proactive reminders are opt-in and always addressed to ONE employee.
CREATE TABLE IF NOT EXISTS lola_private_preferences (
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    proactive_enabled boolean NOT NULL DEFAULT false,
    push_enabled boolean NOT NULL DEFAULT false,
    timezone text NOT NULL DEFAULT 'America/New_York',
    reminder_hour smallint NOT NULL DEFAULT 9
        CHECK (reminder_hour BETWEEN 0 AND 23),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, user_id)
);

CREATE TABLE IF NOT EXISTS lola_private_reminder_receipts (
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    push_accepted boolean NOT NULL DEFAULT false,
    PRIMARY KEY (company_id, user_id, event_id),
    FOREIGN KEY (company_id, user_id, event_id)
        REFERENCES lola_private_events(company_id, user_id, id)
        ON DELETE CASCADE
);

ALTER TABLE lola_private_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE lola_private_reminder_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON lola_private_preferences FROM anon, authenticated;
REVOKE ALL ON lola_private_reminder_receipts FROM anon, authenticated;

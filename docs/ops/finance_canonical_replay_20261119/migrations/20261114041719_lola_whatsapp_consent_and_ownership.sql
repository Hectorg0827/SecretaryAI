-- WhatsApp Business App Coexistence ownership / consent foundation.
-- NO live Meta tokens, history sync, message content, or outbound sending.
-- This schema remains staging-only until permission/policy signoff.

CREATE TABLE IF NOT EXISTS lola_employee_whatsapp_connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    phone_number_id text,
    waba_id text,
    status text NOT NULL DEFAULT 'pending_meta_approval'
        CHECK (status IN ('pending_meta_approval','pending_meta_verification',
                         'pending_privacy_review','active','disconnected')),
    meta_verified_at timestamptz,
    employee_consented_at timestamptz,
    employee_revoked_at timestamptz,
    history_sync_consented boolean NOT NULL DEFAULT false,
    policy_clearance_id text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, user_id),
    FOREIGN KEY (company_id, user_id)
        REFERENCES users(company_id, id) ON DELETE CASCADE,
    CONSTRAINT lola_whatsapp_active_ownership CHECK (
        status <> 'active' OR (
            phone_number_id IS NOT NULL
            AND waba_id IS NOT NULL
            AND meta_verified_at IS NOT NULL
            AND employee_consented_at IS NOT NULL
            AND employee_revoked_at IS NULL
            AND policy_clearance_id IS NOT NULL
        )
    )
);

-- Even an accidental service-role write cannot assign one Meta number
-- to more than one employee, including across tenants.
CREATE UNIQUE INDEX IF NOT EXISTS idx_lola_whatsapp_unique_meta_phone
    ON lola_employee_whatsapp_connections(phone_number_id)
    WHERE phone_number_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_lola_whatsapp_company_owner
    ON lola_employee_whatsapp_connections(company_id,user_id);

ALTER TABLE lola_employee_whatsapp_connections ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON lola_employee_whatsapp_connections FROM anon, authenticated;
-- No anon/authenticated policies: service-role backend MUST scope company + user.

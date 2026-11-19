-- Gate 6B: SecretaryAI-Staging ONLY (Supabase ref xerktvdgjvjozvckjyfp).
-- Never copy this migration into the production migration directories.
-- Department staff may enter only via the separately gated, owner-issued,
-- mailbox-verified staging invitation flow. This does not add data access,
-- email permissions, or account/payment write capabilities.

DO $gate6b$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS constraint_record
        JOIN pg_class AS table_record ON table_record.oid = constraint_record.conrelid
        JOIN pg_namespace AS namespace_record ON namespace_record.oid = table_record.relnamespace
        WHERE namespace_record.nspname = 'public'
          AND table_record.relname = 'users'
          AND constraint_record.conname = 'users_role_check'
          AND constraint_record.contype = 'c'
    ) THEN
        RAISE EXCEPTION 'Expected public.users role constraint missing; aborting finance role migration';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.users
        WHERE role NOT IN ('owner', 'manager', 'sales_rep', 'back_office', 'viewer')
    ) THEN
        RAISE EXCEPTION 'Unexpected preexisting user role; review before changing constraint';
    END IF;
END
$gate6b$;

ALTER TABLE public.users DROP CONSTRAINT users_role_check;
ALTER TABLE public.users ADD CONSTRAINT users_role_check
    CHECK (role IN (
        'owner', 'manager', 'sales_rep', 'back_office', 'viewer',
        'finance_controller', 'ar_specialist', 'ap_specialist', 'pricing_billbacks'
    ));

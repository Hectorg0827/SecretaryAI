-- Logistics event ledger: remove ordinary server-side DELETE permission.
-- Staging-first security hardening. This migration must NOT be applied to
-- production until independently reviewed and approved.
--
-- Original journey migration allows DELETE for FK ON DELETE CASCADE and GDPR
-- company erasure, but the service_role has a direct table DELETE grant in
-- actual Supabase staging. Because it BYPASSES RLS, that direct grant permits
-- deleting individual historical events without a trace. Revoke direct DELETE
-- from the API/server role, leaving the owner-controlled company FK cascade.
-- Never add a blanket BEFORE DELETE trigger: that would break lawful company
-- deletion and record erasure by the existing ON DELETE CASCADE relationship.
--
-- In Supabase, service_role is a trusted backend role, NOT a superuser.
-- Keep its SELECT/INSERT grants for normal event-sourcing workflows.
REVOKE DELETE, TRUNCATE ON TABLE public.logistics_events
    FROM PUBLIC, anon, authenticated, service_role;

-- Preserve the explicit UPDATE blocker from the journey migration. Ensure
-- the server role has no UPDATE grant either; the event log stays append-only
-- for ordinary application operations.
REVOKE UPDATE ON TABLE public.logistics_events
    FROM PUBLIC, anon, authenticated, service_role;

-- Finance AR isolated UAT only: least-privilege backend access to core auth/QBO tables.
-- Supabase migration recorded on isolated preview toujmmvlgktprabjbccq
-- as version 20261119063136 (2026-11-19).
-- Main already has these privileges. Do NOT widen anon/authenticated access.
-- Authorize only when the application uses a server-side service_role key;
-- never expose that key to a client. RLS remains enabled.
GRANT SELECT, INSERT, UPDATE ON TABLE public.companies, public.users TO service_role;

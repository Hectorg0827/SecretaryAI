-- SecretaryAI staging migration V16: fix the search path of trigger-only functions.
-- All three functions reference only PostgreSQL built-ins and NEW; no public
-- or tenant-owned schema name is required to resolve their bodies.
-- Keep companies and approved-email-payload RLS DENY-BY-DEFAULT:
-- they intentionally have no authenticated/anonymous policies.
ALTER FUNCTION public.update_updated_at() SET search_path = pg_catalog, pg_temp;
ALTER FUNCTION public.reject_stock_movement_rewrite() SET search_path = pg_catalog, pg_temp;
ALTER FUNCTION public.reject_purchase_evidence_rewrite() SET search_path = pg_catalog, pg_temp;

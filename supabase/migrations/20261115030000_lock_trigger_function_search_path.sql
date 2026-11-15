-- Pin the three legacy trigger functions to the same safe resolution path as
-- the other SecretaryAI application functions. Rerunnable: ALTER FUNCTION SET
-- replaces proconfig in place, without changing bodies, owners or grants.
-- No live database is modified by adding this migration to the repository.
ALTER FUNCTION public.update_updated_at()
    SET search_path = pg_catalog, public, pg_temp;
ALTER FUNCTION public.reject_stock_movement_rewrite()
    SET search_path = pg_catalog, public, pg_temp;
ALTER FUNCTION public.reject_purchase_evidence_rewrite()
    SET search_path = pg_catalog, public, pg_temp;

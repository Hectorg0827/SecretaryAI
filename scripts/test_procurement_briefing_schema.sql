-- Legacy schema complements required by scheduler and procurement.
DO $legacy$
BEGIN
    IF to_regclass('public.morning_briefings') IS NULL
       OR NOT (SELECT relrowsecurity FROM pg_class
               WHERE oid='public.morning_briefings'::regclass) THEN
      RAISE EXCEPTION 'Private morning briefing table requires RLS';
    END IF;
    IF has_table_privilege('anon','public.morning_briefings','SELECT')
       OR has_table_privilege('authenticated','public.morning_briefings','SELECT')
       OR has_table_privilege('authenticated','public.morning_briefings','INSERT')
       OR NOT has_table_privilege('service_role','public.morning_briefings','INSERT') THEN
      RAISE EXCEPTION 'Briefings not backend-only';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns WHERE table_schema='public'
      AND table_name='morning_briefings' AND column_name='sent_at'
      AND is_nullable='YES'
    ) THEN
      RAISE EXCEPTION 'Briefing provider acceptance must be nullable';
    END IF;
    IF to_regclass('public.procurement_cycles') IS NULL
       OR NOT (SELECT relrowsecurity FROM pg_class
               WHERE oid='public.procurement_cycles'::regclass) THEN
      RAISE EXCEPTION 'Procurement cycles RLS missing';
    END IF;
    IF has_table_privilege('anon','public.procurement_cycles','SELECT')
       OR has_table_privilege('authenticated','public.procurement_cycles','INSERT')
       OR NOT has_table_privilege('authenticated','public.procurement_cycles','SELECT') THEN
      RAISE EXCEPTION 'Procurement cycles have improper client grants';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM pg_policy
      WHERE polrelid='public.procurement_cycles'::regclass
      AND polcmd='r' AND polqual IS NOT NULL
    ) THEN
      RAISE EXCEPTION 'Tenant procurement read policy absent';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM pg_trigger t
      WHERE t.tgrelid='public.procurement_cycles'::regclass
      AND t.tgname='set_updated_at'
      AND t.tgfoid='public.update_updated_at()'::regprocedure
    ) THEN
      RAISE EXCEPTION 'Procurement timestamp trigger incorrect';
    END IF;
END $legacy$;

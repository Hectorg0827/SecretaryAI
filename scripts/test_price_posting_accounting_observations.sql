-- Disposable PostgreSQL only: read-only internal observations, not real QBO/QBD.
\set ON_ERROR_STOP on
BEGIN;
DO $accounting_observation_test$
DECLARE
 tenant_a uuid; tenant_b uuid; owner_a uuid; source_book uuid;
 observation_id uuid; blocked boolean;
BEGIN
 IF has_table_privilege('authenticated','public.pricing_accounting_price_observations','SELECT')
 OR has_table_privilege('authenticated','public.pricing_accounting_price_observations','INSERT')
 OR has_table_privilege('service_role','public.pricing_accounting_price_observations','INSERT')
 OR has_table_privilege('service_role','public.pricing_accounting_price_observations','UPDATE')
 OR has_table_privilege('anon','public.pricing_accounting_price_observations','SELECT')
 OR NOT has_table_privilege('service_role','public.pricing_accounting_price_observations','SELECT')
 THEN
   RAISE EXCEPTION 'Unsafe accounting observation table grants';
 END IF;

 INSERT INTO public.companies(name) VALUES('Synthetic accounting observation A')
 RETURNING id INTO tenant_a;
 INSERT INTO public.companies(name) VALUES('Synthetic accounting observation B')
 RETURNING id INTO tenant_b;
 INSERT INTO public.users(company_id,email,hashed_password,role)
 VALUES(tenant_a,'price-accounting-a@example.invalid','synthetic','owner')
 RETURNING id INTO owner_a;
 INSERT INTO public.pricing_price_book(
    company_id,sku,package_label,market_code,channel,currency,
    list_price_per_case,effective_from,revision,status,created_by
 ) VALUES(
    tenant_a,'ACCT-TEST','12x750mL','NJ','retail','USD',
    71.25,'2026-12-01',1,'draft',owner_a
 ) RETURNING id INTO source_book;

 blocked:=false;
 BEGIN
  INSERT INTO public.pricing_accounting_price_observations(
    company_id,price_book_id,provider,source_item_id,source_revision,
    sku,currency,observed_unit_price,unit_basis,source_document_ref,observed_at
  ) VALUES(
    tenant_b,source_book,'qbo','test-foreign','version-1','ACCT-TEST','USD',
    71.25,'case','synthetic-source-a','2026-11-15T12:00:00Z');
 EXCEPTION WHEN foreign_key_violation THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Cross-tenant observation source accepted'; END IF;

 blocked:=false;
 BEGIN
  INSERT INTO public.pricing_accounting_price_observations(
    company_id,price_book_id,provider,source_item_id,source_revision,
    sku,currency,observed_unit_price,unit_basis,source_document_ref,observed_at
  ) VALUES(
    tenant_a,source_book,'qbo','test-bad-uom','version-1','ACCT-TEST','USD',
    5.9375,'single_unit','synthetic-source-a','2026-11-15T12:00:00Z');
 EXCEPTION WHEN check_violation THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Missing per-case factor accepted'; END IF;

 INSERT INTO public.pricing_accounting_price_observations(
    company_id,price_book_id,provider,source_item_id,source_revision,
    sku,currency,observed_unit_price,unit_basis,units_per_case,
    source_document_ref,observed_at,source_origin
 ) VALUES(
    tenant_a,source_book,'qbd','synthetic-QBD-001','version-1',
    'ACCT-TEST','USD',5.937500,'single_unit',12,
    'synthetic-document-ref-001','2026-11-15T12:00:00Z','synthetic_test'
 ) RETURNING id INTO observation_id;

 IF (SELECT observation_state FROM public.pricing_accounting_price_observations
     WHERE id=observation_id) <> 'internally_recorded_unverified'
 THEN RAISE EXCEPTION 'Unsafe accounting observation state'; END IF;

 blocked:=false;
 BEGIN
   UPDATE public.pricing_accounting_price_observations
   SET source_revision='tampered' WHERE id=observation_id;
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Unverified accounting evidence is rewritable'; END IF;
 blocked:=false;
 BEGIN
   DELETE FROM public.pricing_accounting_price_observations
   WHERE id=observation_id;
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Unverified accounting evidence is deletable'; END IF;

 IF (SELECT count(*) FROM public.pricing_accounting_price_observations
      WHERE company_id=tenant_a)<>1
 THEN RAISE EXCEPTION 'Evidence lost after blocked rewrite'; END IF;
END;
$accounting_observation_test$;
ROLLBACK;

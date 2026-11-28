-- Disposable PostgreSQL only: transactional compliance record + audit evidence.
-- This file runs inside BEGIN/ROLLBACK. Nothing survives the CI test.
\set ON_ERROR_STOP on
BEGIN;

DO $compliance$
DECLARE
  a uuid;
  b uuid;
  actor uuid := gen_random_uuid();
  product_id uuid := gen_random_uuid();
  doc jsonb;
  returned jsonb;
  blocked boolean;
  audit_count integer;
  saved_abv numeric;
BEGIN
  INSERT INTO public.companies(name) VALUES ('Compliance synthetic tenant A')
    RETURNING id INTO a;
  INSERT INTO public.companies(name) VALUES ('Compliance synthetic tenant B')
    RETURNING id INTO b;

  IF has_function_privilege(
       'anon', 'public.compliance_write_record_audited(text,jsonb,text,text,uuid)',
       'EXECUTE'
     ) OR has_function_privilege(
       'authenticated', 'public.compliance_write_record_audited(text,jsonb,text,text,uuid)',
       'EXECUTE'
     ) THEN
    RAISE EXCEPTION 'Compliance atomic write callable by a browser role';
  END IF;
  IF NOT has_function_privilege(
       'service_role', 'public.compliance_write_record_audited(text,jsonb,text,text,uuid)',
       'EXECUTE'
     ) THEN
    RAISE EXCEPTION 'Compliance atomic write is unavailable to the service role';
  END IF;

  doc := jsonb_build_object(
    'id', product_id, 'company_id', a, 'sku', 'SYN-001',
    'name', 'Synthetic Compliance Wine', 'product_type', 'wine',
    'abv_pct', 12.5, 'container_size_ml', 750, 'cases_per_container', 56
  );
  returned := public.compliance_write_record_audited(
      'compliance_products', doc, actor::text, 'allow', NULL
  );
  IF returned->>'operation' <> 'created'
     OR returned->'record'->>'name' <> 'Synthetic Compliance Wine'
     OR returned->>'audit_event_id' IS NULL THEN
    RAISE EXCEPTION 'Compliance create was not acknowledged with an audit receipt';
  END IF;

  SELECT count(*) INTO audit_count FROM public.audit_events
   WHERE company_id = a AND entity_id = product_id::text
     AND event_type = 'compliance.record_created';
  IF audit_count <> 1 THEN
     RAISE EXCEPTION 'Create and audit did not persist together';
  END IF;

  doc := jsonb_set(doc, '{abv_pct}', '13.0'::jsonb);
  returned := public.compliance_write_record_audited(
      'compliance_products', doc, actor::text, 'allow', NULL
  );
  IF returned->>'operation' <> 'updated' THEN
     RAISE EXCEPTION 'Compliance record update was not audited';
  END IF;
  SELECT abv_pct INTO saved_abv FROM public.compliance_products WHERE id = product_id;
  IF saved_abv <> 13.0 THEN
     RAISE EXCEPTION 'Compliance update did not persist';
  END IF;

  -- A change to an existing tenant A primary key cannot be made as tenant B.
  blocked := false;
  BEGIN
    PERFORM public.compliance_write_record_audited(
      'compliance_products', jsonb_set(doc, '{company_id}', to_jsonb(b)),
      actor::text, 'allow', NULL
    );
  EXCEPTION WHEN insufficient_privilege THEN
    blocked := true;
  END;
  IF NOT blocked THEN
    RAISE EXCEPTION 'Foreign tenant identity was allowed to overwrite a record';
  END IF;
  SELECT count(*) INTO audit_count FROM public.audit_events
   WHERE company_id = b AND entity_id = product_id::text;
  IF audit_count <> 0 THEN
    RAISE EXCEPTION 'Foreign tenant received a misleading audit result';
  END IF;

  blocked := false;
  BEGIN
    PERFORM public.compliance_write_record_audited(
      'compliance_products', doc, actor::text, 'require_approval', NULL
    );
  EXCEPTION WHEN insufficient_privilege THEN
    blocked := true;
  END;
  IF NOT blocked THEN
    RAISE EXCEPTION 'Approval-required compliance write was permitted';
  END IF;

  blocked := false;
  BEGIN
    PERFORM public.compliance_write_record_audited(
      'companies', doc, actor::text, 'allow', NULL
    );
  EXCEPTION WHEN insufficient_privilege THEN
    blocked := true;
  END;
  IF NOT blocked THEN
    RAISE EXCEPTION 'Unapproved destination table was accepted';
  END IF;
END;
$compliance$;

-- A failing audit insert must roll back the corresponding compliance update.
CREATE FUNCTION pg_temp.reject_compliance_audit()
RETURNS trigger LANGUAGE plpgsql AS $auditfail$
BEGIN
  RAISE EXCEPTION 'Synthetic audit failure';
END
$auditfail$;

CREATE TRIGGER compliance_audit_failure_test
BEFORE INSERT ON public.audit_events
FOR EACH ROW
WHEN (NEW.event_type LIKE 'compliance.record_%')
EXECUTE FUNCTION pg_temp.reject_compliance_audit();

DO $rollback_test$
DECLARE
  a uuid;
  actor uuid := gen_random_uuid();
  id_to_update uuid;
  original numeric;
  attempted boolean := false;
  after_attempt numeric;
BEGIN
  SELECT id, company_id, abv_pct INTO id_to_update, a, original
    FROM public.compliance_products WHERE sku = 'SYN-001' LIMIT 1;
  BEGIN
    PERFORM public.compliance_write_record_audited(
      'compliance_products',
      jsonb_build_object(
        'id', id_to_update, 'company_id', a, 'sku', 'SYN-001',
        'name', 'Synthetic Compliance Wine', 'product_type', 'wine',
        'abv_pct', 25.0, 'container_size_ml', 750, 'cases_per_container', 56
      ),
      actor::text, 'allow', NULL
    );
  EXCEPTION WHEN raise_exception THEN
    attempted := true;
  END;
  IF NOT attempted THEN
    RAISE EXCEPTION 'Synthetic audit failure was not propagated';
  END IF;
  SELECT abv_pct INTO after_attempt FROM public.compliance_products
   WHERE id = id_to_update;
  IF after_attempt <> original THEN
    RAISE EXCEPTION 'Record changed even though its audit insert failed';
  END IF;
END;
$rollback_test$;
ROLLBACK;

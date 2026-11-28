-- Disposable PostgreSQL acceptance: authorized human review and audit are atomic.
\set ON_ERROR_STOP on
BEGIN;
DO $human_review_test$
DECLARE
  tenant_a uuid;
  tenant_b uuid;
  owner_a uuid;
  rep_a uuid;
  owner_b uuid;
  ny_rule uuid;
  nj_rule uuid;
  receipt jsonb;
  count_reviews integer;
  count_events integer;
  denied boolean;
  note text := 'Independent regulator source and tenant scope checked in this synthetic test';
BEGIN
  IF has_function_privilege('anon',
    'public.record_price_posting_rule_review(uuid,uuid,integer,uuid,text,text,boolean,text,uuid)',
    'EXECUTE')
    OR has_function_privilege('authenticated',
    'public.record_price_posting_rule_review(uuid,uuid,integer,uuid,text,text,boolean,text,uuid)',
    'EXECUTE')
    OR NOT has_function_privilege('service_role',
    'public.record_price_posting_rule_review(uuid,uuid,integer,uuid,text,text,boolean,text,uuid)',
    'EXECUTE')
  THEN RAISE EXCEPTION 'Human review RPC permissions are unsafe'; END IF;

  INSERT INTO public.companies(name) VALUES ('Attestation synthetic A') RETURNING id INTO tenant_a;
  INSERT INTO public.companies(name) VALUES ('Attestation synthetic B') RETURNING id INTO tenant_b;
  INSERT INTO public.users(company_id,email,hashed_password,role)
     VALUES (tenant_a,'reviewowner-a@example.invalid','hash','owner') RETURNING id INTO owner_a;
  INSERT INTO public.users(company_id,email,hashed_password,role)
     VALUES (tenant_a,'reviewrep-a@example.invalid','hash','sales_rep') RETURNING id INTO rep_a;
  INSERT INTO public.users(company_id,email,hashed_password,role)
     VALUES (tenant_b,'reviewowner-b@example.invalid','hash','owner') RETURNING id INTO owner_b;
  SELECT id INTO ny_rule FROM public.compliance_price_posting_rule_versions
     WHERE program_key='NY_WHOLESALE' AND version=1;
  SELECT id INTO nj_rule FROM public.compliance_price_posting_rule_versions
     WHERE program_key='NJ_ECPL' AND version=1;

  denied := false;
  BEGIN
    PERFORM public.record_price_posting_rule_review(
      tenant_a,ny_rule,1,owner_a,'synthetic-reviewed-official-reference',note,
      false,'allow',null);
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'Unchecked human attestation accepted'; END IF;

  denied := false;
  BEGIN
    PERFORM public.record_price_posting_rule_review(
      tenant_a,ny_rule,1,owner_a,'synthetic-reviewed-official-reference',note,
      true,'require_approval',null);
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'Approval-required policy bypassed'; END IF;

  denied := false;
  BEGIN
    PERFORM public.record_price_posting_rule_review(
      tenant_a,ny_rule,1,rep_a,'synthetic-reviewed-official-reference',note,
      true,'allow',null);
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'Sales rep recorded regulatory review'; END IF;

  denied := false;
  BEGIN
    PERFORM public.record_price_posting_rule_review(
      tenant_a,ny_rule,1,owner_b,'synthetic-reviewed-official-reference',note,
      true,'allow',null);
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'Cross-tenant owner recorded regulatory review'; END IF;

  denied := false;
  BEGIN
    PERFORM public.record_price_posting_rule_review(
      tenant_a,ny_rule,99,owner_a,'synthetic-reviewed-official-reference',note,
      true,'allow',null);
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'Stale rule revision was accepted'; END IF;

  receipt := public.record_price_posting_rule_review(
    tenant_a,ny_rule,1,owner_a,'synthetic-reviewed-official-reference',note,
    true,'allow',null);
  IF receipt->>'state' <> 'documentary_review_recorded'
     OR receipt->>'submission_permitted' <> 'false'
     OR receipt->>'review_id' IS NULL OR receipt->>'audit_event_id' IS NULL
  THEN RAISE EXCEPTION 'Human attestation/audit receipt incomplete'; END IF;
  SELECT count(*) INTO count_reviews FROM public.compliance_price_posting_rule_reviews
    WHERE company_id=tenant_a AND rule_id=ny_rule;
  SELECT count(*) INTO count_events FROM public.audit_events
    WHERE company_id=tenant_a AND event_type='compliance.price_rule_review_recorded'
      AND entity_id=ny_rule::text;
  IF count_reviews <> 1 OR count_events <> 1
  THEN RAISE EXCEPTION 'Human review and audit mismatch'; END IF;

  denied := false;
  BEGIN
    PERFORM public.record_price_posting_rule_review(
      tenant_a,ny_rule,1,owner_a,'synthetic-reviewed-official-reference',note,
      true,'allow',null);
  EXCEPTION WHEN unique_violation THEN denied := true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'Duplicate review was accepted'; END IF;
  SELECT count(*) INTO count_events FROM public.audit_events
    WHERE company_id=tenant_a AND event_type='compliance.price_rule_review_recorded';
  IF count_events <> 1 THEN RAISE EXCEPTION 'Duplicate review created extra audit event'; END IF;

  -- An audit insert failure must roll back the review record.
  EXECUTE $ddl$CREATE FUNCTION pg_temp.reject_price_review_audit()
    RETURNS trigger LANGUAGE plpgsql AS $failed_audit$
    BEGIN RAISE EXCEPTION 'Synthetic audit unavailable'; END
    $failed_audit$ $ddl$;
  EXECUTE $ddl$CREATE TRIGGER reject_rule_review_audit_test
    BEFORE INSERT ON public.audit_events FOR EACH ROW
    WHEN (NEW.event_type = 'compliance.price_rule_review_recorded')
    EXECUTE FUNCTION pg_temp.reject_price_review_audit()$ddl$;

  denied := false;
  BEGIN
    PERFORM public.record_price_posting_rule_review(
      tenant_a,nj_rule,1,owner_a,'synthetic-reviewed-official-reference',note,
      true,'allow',null);
  EXCEPTION WHEN raise_exception THEN denied := true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'Failed audit was not propagated'; END IF;
  SELECT count(*) INTO count_reviews FROM public.compliance_price_posting_rule_reviews
    WHERE company_id=tenant_a AND rule_id=nj_rule;
  IF count_reviews <> 0 THEN RAISE EXCEPTION 'Review persisted without audit'; END IF;
END;
$human_review_test$;
ROLLBACK;

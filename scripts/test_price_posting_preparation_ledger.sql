-- Disposable-only PostgreSQL integration: NO live tenant, no filing.
\set ON_ERROR_STOP on
BEGIN;
DO $case_test$
DECLARE
 tenant_a uuid;tenant_b uuid;owner_a uuid;owner_b uuid;rule_id uuid;
 book_a uuid;book_b uuid;book_wrong uuid;packet jsonb;receipt jsonb;case_id uuid;
 blocked boolean;note text:='Synthetic supporting reference, not independently authenticated by an agency';
BEGIN
 IF has_function_privilege('anon',
   'public.create_price_posting_preparation(uuid,uuid,integer,uuid,date,uuid[],text,uuid)',
   'EXECUTE')
 OR has_function_privilege('authenticated',
   'public.record_price_posting_external_reference(uuid,uuid,uuid,text,text,date,text,text,uuid)',
   'EXECUTE')
 OR has_table_privilege('authenticated','public.compliance_price_posting_preparations','INSERT')
 OR has_table_privilege('service_role','public.compliance_price_posting_external_references','INSERT')
 OR NOT has_table_privilege('service_role','public.compliance_price_posting_preparations','SELECT')
 THEN RAISE EXCEPTION 'Unsafe internal preparation privileges'; END IF;

 INSERT INTO public.companies(name) VALUES('Preparation synthetic A') RETURNING id INTO tenant_a;
 INSERT INTO public.companies(name) VALUES('Preparation synthetic B') RETURNING id INTO tenant_b;
 INSERT INTO public.users(company_id,email,hashed_password,role)
 VALUES(tenant_a,'preparation-owner-a@example.invalid','synthetic','owner') RETURNING id INTO owner_a;
 INSERT INTO public.users(company_id,email,hashed_password,role)
 VALUES(tenant_b,'preparation-owner-b@example.invalid','synthetic','owner') RETURNING id INTO owner_b;
 SELECT id INTO rule_id FROM public.compliance_price_posting_rule_versions
 WHERE program_key='NJ_ECPL' AND version=1;
 INSERT INTO public.pricing_price_book(
  company_id,sku,package_label,market_code,channel,list_price_per_case,effective_from,
  revision,status,created_by)
 VALUES(tenant_a,'PREP-A','12x750mL','NJ','retail',61.00,'2026-12-01',1,'draft',owner_a)
 RETURNING id INTO book_a;
 INSERT INTO public.pricing_price_book(
  company_id,sku,package_label,market_code,channel,list_price_per_case,effective_from,
  revision,status,created_by)
 VALUES(tenant_b,'PREP-B','12x750mL','NJ','retail',72.00,'2026-12-01',1,'draft',owner_b)
 RETURNING id INTO book_b;
 INSERT INTO public.pricing_price_book(
  company_id,sku,package_label,market_code,channel,list_price_per_case,effective_from,
  revision,status,created_by)
 VALUES(tenant_a,'PREP-WRONG','12x750mL','NY','retail',72.00,'2026-12-01',1,'draft',owner_a)
 RETURNING id INTO book_wrong;

 blocked:=false;
 BEGIN
  PERFORM public.create_price_posting_preparation(
   tenant_a,rule_id,1,owner_a,'2026-12-01',ARRAY[book_a],'allow',NULL);
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Unreviewed regulatory source was accepted'; END IF;

 INSERT INTO public.compliance_price_posting_rule_reviews(
 company_id,rule_id,reviewer_id,evidence_ref,review_note)
 VALUES(tenant_a,rule_id,owner_a,'synthetic-evidence-ref-regulator',
   'Synthetic human regulator source review for tenant A only');

 blocked:=false;
 BEGIN
  PERFORM public.create_price_posting_preparation(
   tenant_a,rule_id,1,owner_a,'2026-12-01',ARRAY[book_a],'require_approval',NULL);
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Policy approval was bypassed'; END IF;

 blocked:=false;
 BEGIN
  PERFORM public.create_price_posting_preparation(
   tenant_a,rule_id,1,owner_b,'2026-12-01',ARRAY[book_a],'allow',NULL);
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Cross-tenant reviewer was accepted'; END IF;

 blocked:=false;
 BEGIN
  PERFORM public.create_price_posting_preparation(
   tenant_a,rule_id,1,owner_a,'2026-12-01',ARRAY[book_a,book_b],'allow',NULL);
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Foreign tenant price was captured'; END IF;

 blocked:=false;
 BEGIN
  PERFORM public.create_price_posting_preparation(
   tenant_a,rule_id,1,owner_a,'2026-12-01',ARRAY[book_wrong],'allow',NULL);
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Wrong-state price book accepted'; END IF;

 blocked:=false;
 BEGIN
  PERFORM public.create_price_posting_preparation(
   tenant_a,rule_id,1,owner_a,'2026-11-01',ARRAY[book_a],'allow',NULL);
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Premature price effective date accepted'; END IF;

 blocked:=false;
 BEGIN
  PERFORM public.create_price_posting_preparation(
   tenant_a,rule_id,1,owner_a,'2026-12-01',ARRAY[book_a,book_a],'allow',NULL);
 EXCEPTION WHEN invalid_parameter_value THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Duplicate source references were allowed'; END IF;

 packet:=public.create_price_posting_preparation(
   tenant_a,rule_id,1,owner_a,'2026-12-01',ARRAY[book_a],'allow',NULL);
 IF packet->>'state' <> 'internal_preparation_only'
 OR packet->>'submission_permitted' <> 'false'
 OR packet->>'agency_confirmation' <> 'false'
 OR packet->>'audit_event_id' IS NULL THEN
  RAISE EXCEPTION 'Preparation response falsely reported filing'; END IF;
 case_id:=(packet->>'preparation_id')::uuid;
 IF (SELECT price_book_snapshot->0->>'sku'
  FROM public.compliance_price_posting_preparations WHERE id=case_id) <> 'PREP-A'
 THEN RAISE EXCEPTION 'Frozen snapshot source mismatch'; END IF;
 IF (SELECT count(*) FROM public.audit_events
   WHERE company_id=tenant_a AND event_type='compliance.price_preparation_created'
   AND entity_id=case_id::text)<>1 THEN
  RAISE EXCEPTION 'Preparation audit missing'; END IF;

 blocked:=false;
 BEGIN
  UPDATE public.compliance_price_posting_preparations
  SET state='internal_preparation_only' WHERE id=case_id;
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Frozen snapshot is rewritable'; END IF;

 blocked:=false;
 BEGIN
  PERFORM public.record_price_posting_external_reference(
   tenant_b,case_id,owner_b,'external_document_reference',
   'synthetic-agency-url',NULL,note,'allow',NULL);
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Cross-tenant external reference accepted'; END IF;

 receipt:=public.record_price_posting_external_reference(
   tenant_a,case_id,owner_a,'agency_message_reference',
   'synthetic-external-agency-document','2026-12-01',note,'allow',NULL);
 IF receipt->>'provenance_status' <> 'human_reported_unverified'
 OR receipt->>'agency_confirmation' <> 'false'
 OR receipt->>'audit_event_id' IS NULL THEN
  RAISE EXCEPTION 'Human reference falsely treated as agency confirmation'; END IF;

 -- Failed audit insertion must roll back external reference insertion.
 EXECUTE $ddl$CREATE FUNCTION pg_temp.reject_price_preparation_audit()
 RETURNS trigger LANGUAGE plpgsql AS $fail$
 BEGIN RAISE EXCEPTION 'Synthetic audit failure'; END;$fail$ $ddl$;
 EXECUTE $ddl$CREATE TRIGGER trg_fail_price_preparation_audit
 BEFORE INSERT ON public.audit_events FOR EACH ROW
 WHEN(NEW.event_type='compliance.price_external_reference_recorded')
 EXECUTE FUNCTION pg_temp.reject_price_preparation_audit()$ddl$;
 blocked:=false;
 BEGIN
  PERFORM public.record_price_posting_external_reference(
   tenant_a,case_id,owner_a,'rejection_reference',
   'synthetic-external-ref-2','2026-12-01',note,'allow',NULL);
 EXCEPTION WHEN raise_exception THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Audit failure not propagated'; END IF;
 IF (SELECT count(*) FROM public.compliance_price_posting_external_references
   WHERE company_id=tenant_a AND preparation_id=case_id)<>1 THEN
  RAISE EXCEPTION 'Reference survived failed audit'; END IF;
END;$case_test$;
ROLLBACK;

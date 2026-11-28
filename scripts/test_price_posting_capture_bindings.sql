-- Patch 10 disposable DB scope and immutable candidate tests; no QB API calls.
\set ON_ERROR_STOP on
BEGIN;
DO $binding_tests$
DECLARE
 a uuid;b uuid;owner_id uuid;book_id uuid;binding_id uuid;blocked boolean;
BEGIN
 IF has_table_privilege('anon','public.pricing_accounting_capture_bindings','SELECT')
 OR has_table_privilege('authenticated','public.pricing_accounting_capture_bindings','SELECT')
 OR has_table_privilege('authenticated','public.pricing_accounting_capture_bindings','INSERT')
 OR has_table_privilege('service_role','public.pricing_accounting_capture_bindings','INSERT')
 OR has_table_privilege('service_role','public.pricing_accounting_capture_bindings','UPDATE')
 OR NOT has_table_privilege('service_role','public.pricing_accounting_capture_bindings','SELECT')
 THEN RAISE EXCEPTION 'Unsafe QB capture candidate privileges'; END IF;

 INSERT INTO public.companies(name) VALUES('Synthetic capture company A')
 RETURNING id INTO a;
 INSERT INTO public.companies(name) VALUES('Synthetic capture company B')
 RETURNING id INTO b;
 INSERT INTO public.users(company_id,email,hashed_password,role)
 VALUES(a,'synthetic-capture-owner@example.invalid','test','owner')
 RETURNING id INTO owner_id;
 INSERT INTO public.pricing_price_book(
  company_id,sku,package_label,market_code,channel,list_price_per_case,
  effective_from,revision,status,created_by)
 VALUES(a,'CAP-TEST','12x750mL','NJ','retail',66.0,'2026-12-01',1,'draft',owner_id)
 RETURNING id INTO book_id;

 blocked:=false;
 BEGIN
  INSERT INTO public.pricing_accounting_capture_bindings(
   company_id,price_book_id,provider,source_item_id,source_revision,
   claimed_currency,candidate_unit_basis,candidate_document_ref)
  VALUES(b,book_id,'qbo','foreign','version1','USD','case','synthetic-doc-foreign');
 EXCEPTION WHEN foreign_key_violation THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Foreign price-book binding accepted'; END IF;

 blocked:=false;
 BEGIN
  INSERT INTO public.pricing_accounting_capture_bindings(
   company_id,price_book_id,provider,source_item_id,source_revision,
   claimed_currency,candidate_unit_basis,candidate_document_ref)
  VALUES(a,book_id,'qbo','bad-unit','version1','USD','single_unit','synthetic-doc-unit');
 EXCEPTION WHEN check_violation THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Missing case-count constraint bypassed'; END IF;

 INSERT INTO public.pricing_accounting_capture_bindings(
  company_id,price_book_id,provider,source_item_id,source_revision,
  source_sku_hint,claimed_currency,candidate_unit_basis,candidate_units_per_case,
  candidate_document_ref)
 VALUES(a,book_id,'qbd','conductor-item-id-42','version1',
  'This is a salesDescription, not SKU','USD','single_unit',12,
  'synthetic-mapping-source-document-42') RETURNING id INTO binding_id;

 IF (SELECT identity_state FROM public.pricing_accounting_capture_bindings
  WHERE id=binding_id) <> 'candidate_unverified'
 THEN RAISE EXCEPTION 'Candidate falsely marked verified'; END IF;

 blocked:=false;
 BEGIN
  UPDATE public.pricing_accounting_capture_bindings
   SET source_item_id='mutated' WHERE id=binding_id;
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Capture binding is rewritable'; END IF;
 blocked:=false;
 BEGIN
  DELETE FROM public.pricing_accounting_capture_bindings WHERE id=binding_id;
 EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
 END;
 IF NOT blocked THEN RAISE EXCEPTION 'Capture binding is deletable'; END IF;
 IF (SELECT count(*) FROM public.pricing_accounting_capture_bindings WHERE company_id=a) <> 1
 THEN RAISE EXCEPTION 'Capture binding immutability failed'; END IF;
END;
$binding_tests$;
ROLLBACK;

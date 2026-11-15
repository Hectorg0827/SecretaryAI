-- Disposable-only PostgreSQL checks for the candidate price-posting rulebook.
-- Changes in this file are rolled back; no agency, tenant or accounting writes.
\set ON_ERROR_STOP on
BEGIN;
DO $rulebook$
DECLARE
  a uuid;
  b uuid;
  reviewer uuid;
  foreign_reviewer uuid;
  rule_id uuid;
  review_id uuid;
  denied boolean;
BEGIN
  IF (SELECT count(*) FROM public.compliance_price_posting_rule_versions
      WHERE program_key IN ('NY_WHOLESALE','NY_RETAIL','NJ_ECPL','FL_MALT_NOTICE')) <> 4
  THEN
    RAISE EXCEPTION 'Missing expected NY/NJ/FL candidate programs';
  END IF;
  IF EXISTS (SELECT 1 FROM public.compliance_price_posting_rule_versions
             WHERE filing_deadline_rule IS NOT NULL OR official_template_url IS NOT NULL)
  THEN
    RAISE EXCEPTION 'Unverified deadline or template was incorrectly pre-certified';
  END IF;
  IF has_table_privilege('anon','public.compliance_price_posting_rule_reviews','SELECT')
     OR has_table_privilege('authenticated','public.compliance_price_posting_rule_reviews','INSERT')
     OR has_table_privilege('service_role','public.compliance_price_posting_rule_reviews','INSERT')
     OR NOT has_table_privilege('service_role','public.compliance_price_posting_rule_reviews','SELECT')
  THEN
    RAISE EXCEPTION 'Unsafe review table grants';
  END IF;

  INSERT INTO public.companies(name) VALUES ('Rulebook synthetic A') RETURNING id INTO a;
  INSERT INTO public.companies(name) VALUES ('Rulebook synthetic B') RETURNING id INTO b;
  INSERT INTO public.users(company_id,email,hashed_password,role)
  VALUES (a,'synthetic-rulebook-a@example.invalid','hash','owner')
  RETURNING id INTO reviewer;
  INSERT INTO public.users(company_id,email,hashed_password,role)
  VALUES (b,'synthetic-rulebook-b@example.invalid','hash','owner')
  RETURNING id INTO foreign_reviewer;
  SELECT id INTO rule_id FROM public.compliance_price_posting_rule_versions
   WHERE program_key='NJ_ECPL' AND version=1;

  denied := false;
  BEGIN
    INSERT INTO public.compliance_price_posting_rule_reviews
    (company_id,rule_id,reviewer_id,evidence_ref,review_note)
    VALUES (a,rule_id,foreign_reviewer,'source-reviewed-foreign',
            'Synthetic foreign reviewer attempted to verify a rule');
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN
    RAISE EXCEPTION 'Foreign tenant was accepted as human reviewer';
  END IF;

  INSERT INTO public.compliance_price_posting_rule_reviews
    (company_id,rule_id,reviewer_id,evidence_ref,review_note)
  VALUES (a,rule_id,reviewer,'synthetic-official-source-evidence',
          'Synthetic verified source with documented human review')
  RETURNING id INTO review_id;

  denied := false;
  BEGIN
    UPDATE public.compliance_price_posting_rule_reviews
       SET review_note='Rewritten evidence'
     WHERE id=review_id;
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN
    RAISE EXCEPTION 'Human review evidence was overwritten';
  END IF;

  denied := false;
  BEGIN
    DELETE FROM public.compliance_price_posting_rule_versions WHERE id=rule_id;
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN
    RAISE EXCEPTION 'Source rule version was deleted';
  END IF;
END;
$rulebook$;
ROLLBACK;

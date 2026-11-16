-- Disposable PostgreSQL only. Verifies that rule verification, preparation,
-- and human reference RPCs cannot commit without the precise tenant API policy.
\set ON_ERROR_STOP on
BEGIN;
DO $scoped_guard$
DECLARE
    tenant_a uuid;
    tenant_b uuid;
    owner_a uuid;
    owner_b uuid;
    rule_id uuid;
    book_id uuid;
    review_policy uuid;
    preparation_policy uuid;
    reference_policy uuid;
    generic_policy uuid;
    wrong_path_policy uuid;
    foreign_policy uuid;
    preparation_id uuid;
    reply jsonb;
    note text := 'Synthetic price-posting evidence for the exact current authority; no government action claimed';
    blocked boolean;
BEGIN
    INSERT INTO public.companies(name) VALUES('Specific policy test tenant A')
        RETURNING id INTO tenant_a;
    INSERT INTO public.companies(name) VALUES('Specific policy test tenant B')
        RETURNING id INTO tenant_b;
    INSERT INTO public.users(company_id,email,hashed_password,role)
    VALUES(tenant_a,'scoped-policy-owner-a@example.invalid','test','owner')
        RETURNING id INTO owner_a;
    INSERT INTO public.users(company_id,email,hashed_password,role)
    VALUES(tenant_b,'scoped-policy-owner-b@example.invalid','test','owner')
        RETURNING id INTO owner_b;
    SELECT id INTO rule_id FROM public.compliance_price_posting_rule_versions
    WHERE program_key='NJ_ECPL' AND version=1;
    IF rule_id IS NULL THEN RAISE EXCEPTION 'Synthetic candidate rule missing'; END IF;

    INSERT INTO public.policy_rules(
        company_id,rule_name,action_class,effect,paths_match,capabilities_match,priority)
    VALUES(tenant_a,'specific rule attestation','commit','allow',
        ARRAY['api'],ARRAY['compliance.rule_review'],1)
        RETURNING id INTO review_policy;
    INSERT INTO public.policy_rules(
        company_id,rule_name,action_class,effect,paths_match,capabilities_match,priority)
    VALUES(tenant_a,'specific preparation','commit','allow',
        ARRAY['api'],ARRAY['compliance.price_preparation'],2)
        RETURNING id INTO preparation_policy;
    INSERT INTO public.policy_rules(
        company_id,rule_name,action_class,effect,paths_match,capabilities_match,priority)
    VALUES(tenant_a,'specific human reference','commit','allow',
        ARRAY['api'],ARRAY['compliance.price_external_reference'],3)
        RETURNING id INTO reference_policy;
    INSERT INTO public.policy_rules(
        company_id,rule_name,action_class,effect,priority)
    VALUES(tenant_a,'generic unsafe allow','commit','allow',4)
        RETURNING id INTO generic_policy;
    INSERT INTO public.policy_rules(
        company_id,rule_name,action_class,effect,paths_match,capabilities_match,priority)
    VALUES(tenant_a,'wrong execution path','commit','allow',
        ARRAY['browser'],ARRAY['compliance.rule_review'],5)
        RETURNING id INTO wrong_path_policy;
    INSERT INTO public.policy_rules(
        company_id,rule_name,action_class,effect,paths_match,capabilities_match,priority)
    VALUES(tenant_b,'foreign review rule','commit','allow',
        ARRAY['api'],ARRAY['compliance.rule_review'],6)
        RETURNING id INTO foreign_policy;

    blocked:=false;
    BEGIN
        PERFORM public.record_price_posting_rule_review(
            tenant_a,rule_id,1,owner_a,'https://synthetic.test/doc',
            note,true,'allow',NULL);
    EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'No-rule review was accepted'; END IF;

    blocked:=false;
    BEGIN
        PERFORM public.record_price_posting_rule_review(
            tenant_a,rule_id,1,owner_a,'https://synthetic.test/doc',
            note,true,'allow',generic_policy);
    EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'Generic COMMIT allow accepted for review'; END IF;

    blocked:=false;
    BEGIN
        PERFORM public.record_price_posting_rule_review(
            tenant_a,rule_id,1,owner_a,'https://synthetic.test/doc',
            note,true,'allow',wrong_path_policy);
    EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'Wrong-path rule accepted'; END IF;

    blocked:=false;
    BEGIN
        PERFORM public.record_price_posting_rule_review(
            tenant_a,rule_id,1,owner_a,'https://synthetic.test/doc',
            note,true,'allow',foreign_policy);
    EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'Foreign company policy accepted'; END IF;

    IF EXISTS(SELECT 1 FROM public.compliance_price_posting_rule_reviews
              WHERE company_id=tenant_a) THEN
        RAISE EXCEPTION 'Review persisted after blocked audit';
    END IF;

    reply:=public.record_price_posting_rule_review(
        tenant_a,rule_id,1,owner_a,'https://synthetic.test/doc',
        note,true,'allow',review_policy);
    IF reply->>'state'<>'documentary_review_recorded'
        OR reply->>'submission_permitted'<>'false' THEN
        RAISE EXCEPTION 'Successful review claimed filing authority';
    END IF;

    INSERT INTO public.pricing_price_book(
        company_id,sku,package_label,market_code,channel,
        list_price_per_case,effective_from,revision,status,created_by)
    VALUES(tenant_a,'SCOPE-TEST-001','12x750mL','NJ','retail',
        66.25,'2026-12-01',1,'draft',owner_a)
    RETURNING id INTO book_id;

    blocked:=false;
    BEGIN
        PERFORM public.create_price_posting_preparation(
            tenant_a,rule_id,1,owner_a,'2026-12-01',
            ARRAY[book_id],'allow',review_policy);
    EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'Attestation rule authorized preparation'; END IF;

    blocked:=false;
    BEGIN
        PERFORM public.create_price_posting_preparation(
            tenant_a,rule_id,1,owner_a,'2026-12-01',
            ARRAY[book_id],'allow',generic_policy);
    EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'Generic allow authorized preparation'; END IF;

    reply:=public.create_price_posting_preparation(
        tenant_a,rule_id,1,owner_a,'2026-12-01',
        ARRAY[book_id],'allow',preparation_policy);
    preparation_id:=(reply->>'preparation_id')::uuid;
    IF reply->>'state'<>'internal_preparation_only'
        OR reply->>'submission_permitted'<>'false' THEN
        RAISE EXCEPTION 'Preparation wrongly treated as a filing';
    END IF;

    blocked:=false;
    BEGIN
        PERFORM public.record_price_posting_external_reference(
            tenant_a,preparation_id,owner_a,'agency_message_reference',
            'synthetic-agency-claim','2026-12-02',note,
            'allow',preparation_policy);
    EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'Preparation permission authorized external reference'; END IF;

    reply:=public.record_price_posting_external_reference(
        tenant_a,preparation_id,owner_a,'agency_message_reference',
        'synthetic-agency-claim','2026-12-02',note,
        'allow',reference_policy);
    IF reply->>'agency_confirmation'<>'false'
       OR reply->>'provenance_status'<>'human_reported_unverified' THEN
       RAISE EXCEPTION 'Human report represented as accepted agency receipt';
    END IF;

    UPDATE public.policy_rules SET is_active=false WHERE id=reference_policy;
    blocked:=false;
    BEGIN
        PERFORM public.record_price_posting_external_reference(
            tenant_a,preparation_id,owner_a,'rejection_reference',
            'synthetic-denial-evidence','2026-12-03',note,
            'allow',reference_policy);
    EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'Revoked policy allowed external reference'; END IF;
    IF (SELECT count(*) FROM public.compliance_price_posting_external_references
        WHERE company_id=tenant_a)<>1 THEN
        RAISE EXCEPTION 'Blocked reference survived audit rollback';
    END IF;
    IF (SELECT count(*) FROM public.audit_events
        WHERE company_id=tenant_a
          AND event_type IN ('compliance.price_rule_review_recorded',
                             'compliance.price_preparation_created',
                             'compliance.price_external_reference_recorded'))<>3 THEN
        RAISE EXCEPTION 'Exactly three authorized audit receipts expected';
    END IF;

    -- Must NOT break the unrelated audit event pipeline.
    INSERT INTO public.audit_events(
        company_id,user_id,event_type,action_class,description)
    VALUES(tenant_a,owner_a,'synthetic.unrelated_event','read',
        'Unrelated audit event is unaffected');
END;
$scoped_guard$;
ROLLBACK;

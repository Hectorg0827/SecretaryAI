-- Staging-only additive migration: tenant-specific human source attestation.
-- A human rule review is DOCUMENTARY evidence, never a filing approval.
-- Both the review row and the audit event commit in the same PostgreSQL transaction.

CREATE FUNCTION public.record_price_posting_rule_review(
    p_company_id uuid,
    p_rule_id uuid,
    p_rule_version integer,
    p_reviewer_id uuid,
    p_evidence_ref text,
    p_review_note text,
    p_acknowledged boolean,
    p_policy_effect text,
    p_policy_rule_id uuid DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $human_review$
DECLARE
    v_rule public.compliance_price_posting_rule_versions%ROWTYPE;
    v_latest_version integer;
    v_review_id uuid;
    v_audit_id uuid := gen_random_uuid();
BEGIN
    IF p_policy_effect IS DISTINCT FROM 'allow'
       OR p_acknowledged IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Explicit policy authorization and human attestation required'
            USING ERRCODE = '42501';
    END IF;
    IF p_company_id IS NULL OR p_reviewer_id IS NULL OR p_rule_id IS NULL
       OR length(btrim(coalesce(p_evidence_ref,''))) NOT BETWEEN 10 AND 1500
       OR length(btrim(coalesce(p_review_note,''))) NOT BETWEEN 30 AND 4000 THEN
        RAISE EXCEPTION 'Rule review evidence or identity is invalid'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_rule
      FROM public.compliance_price_posting_rule_versions
     WHERE id = p_rule_id FOR SHARE;
    IF NOT FOUND OR v_rule.version IS DISTINCT FROM p_rule_version THEN
        RAISE EXCEPTION 'Rule version changed or no longer exists'
            USING ERRCODE = '42501';
    END IF;
    SELECT max(version) INTO v_latest_version
      FROM public.compliance_price_posting_rule_versions
     WHERE program_key = v_rule.program_key;
    IF v_rule.version IS DISTINCT FROM v_latest_version THEN
        RAISE EXCEPTION 'Superseded regulatory rule cannot be attested'
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.users
        WHERE id = p_reviewer_id AND company_id = p_company_id
          AND is_active IS TRUE AND role IN ('owner','manager')
    ) THEN
        RAISE EXCEPTION 'Reviewer is not an active authorized tenant owner or manager'
            USING ERRCODE = '42501';
    END IF;

    INSERT INTO public.compliance_price_posting_rule_reviews
        (company_id,rule_id,reviewer_id,evidence_ref,review_note)
    VALUES
        (p_company_id,p_rule_id,p_reviewer_id,btrim(p_evidence_ref),btrim(p_review_note))
    RETURNING id INTO v_review_id;

    -- Do not copy the underlying source document or reviewer notes to the audit.
    INSERT INTO public.audit_events (
        id,company_id,user_id,event_type,action_class,capability,path_used,
        entity_type,entity_id,description,after_snapshot,policy_effect,
        policy_rule_id
    ) VALUES (
        v_audit_id,p_company_id,p_reviewer_id,'compliance.price_rule_review_recorded',
        'commit','compliance.rule_review','api','compliance_price_posting_rule_versions',
        p_rule_id::text,
        'Human documentary source review recorded; no regulatory filing authorized',
        jsonb_build_object(
            'rule_id',p_rule_id::text,
            'program_key',v_rule.program_key,
            'rule_version',v_rule.version,
            'review_id',v_review_id::text,
            'evidence_digest',md5(btrim(p_evidence_ref))
        ),
        'allow',p_policy_rule_id
    );
    RETURN jsonb_build_object(
        'review_id',v_review_id::text,
        'audit_event_id',v_audit_id::text,
        'rule_id',p_rule_id::text,
        'rule_version',v_rule.version,
        'state','documentary_review_recorded',
        'submission_permitted',false
    );
END;
$human_review$;

REVOKE ALL ON FUNCTION public.record_price_posting_rule_review(
    uuid,uuid,integer,uuid,text,text,boolean,text,uuid
) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.record_price_posting_rule_review(
    uuid,uuid,integer,uuid,text,text,boolean,text,uuid
) TO service_role;

COMMENT ON FUNCTION public.record_price_posting_rule_review(
    uuid,uuid,integer,uuid,text,text,boolean,text,uuid
) IS 'Atomic human documentary attestation with audit. Does not certify regulatory deadlines or authorize a filing.';

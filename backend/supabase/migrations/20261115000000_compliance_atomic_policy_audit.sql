-- Compliance Phase 1: a compliance write and its audit event are one transaction.
-- New migration only. No existing migrations or live data are changed by this PR.
-- The policy decision is evaluated by the backend first; this function refuses
-- anything but an explicit 'allow'. No government filing is submitted here.

CREATE OR REPLACE FUNCTION public.compliance_write_record_audited(
    p_table text,
    p_record jsonb,
    p_actor text,
    p_policy_effect text,
    p_policy_rule_id uuid DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    v_id uuid;
    v_company uuid;
    v_previous jsonb;
    v_written jsonb;
    v_columns text;
    v_selectors text;
    v_event_id uuid := gen_random_uuid();
    v_operation text;
    v_actor_uuid uuid;
    v_fields jsonb;
BEGIN
    IF p_table NOT IN (
        'compliance_products', 'compliance_licenses',
        'compliance_brand_registrations', 'compliance_federal_permits',
        'compliance_distributors', 'compliance_colas'
    ) THEN
        RAISE EXCEPTION 'Unsupported compliance entity' USING ERRCODE = '42501';
    END IF;
    IF p_policy_effect IS DISTINCT FROM 'allow' THEN
        RAISE EXCEPTION 'Compliance record write not permitted by policy'
            USING ERRCODE = '42501';
    END IF;
    IF p_record IS NULL OR jsonb_typeof(p_record) <> 'object' OR
       NULLIF(p_record->>'id', '') IS NULL OR
       NULLIF(p_record->>'company_id', '') IS NULL OR
       NULLIF(p_actor, '') IS NULL THEN
        RAISE EXCEPTION 'Compliance write identity is incomplete'
            USING ERRCODE = '22023';
    END IF;

    v_id := (p_record->>'id')::uuid;
    v_company := (p_record->>'company_id')::uuid;
    IF p_actor ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        v_actor_uuid := p_actor::uuid;
    END IF;

    -- Lock the previous record, if any, and prevent cross-tenant overwrites.
    EXECUTE format('SELECT to_jsonb(row) FROM public.%I AS row WHERE id = $1 FOR UPDATE', p_table)
       INTO v_previous USING v_id;
    IF v_previous IS NOT NULL AND v_previous->>'company_id' <> v_company::text THEN
        RAISE EXCEPTION 'Compliance record belongs to another tenant'
            USING ERRCODE = '42501';
    END IF;

    -- Select only supplied fields, preserving database defaults for other fields.
    -- Table names are whitelisted above; every column identifier is quoted.
    SELECT string_agg(format('%I', k), ', ' ORDER BY k),
           string_agg(format('source.%I', k), ', ' ORDER BY k),
           jsonb_agg(k ORDER BY k)
      INTO v_columns, v_selectors, v_fields
      FROM jsonb_object_keys(p_record) AS names(k);

    IF v_previous IS NULL THEN
        v_operation := 'created';
        EXECUTE format(
            'INSERT INTO public.%I AS target (%s) SELECT %s FROM jsonb_populate_record(NULL::public.%I, $1) AS source RETURNING to_jsonb(target)',
            p_table, v_columns, v_selectors, p_table
        ) INTO v_written USING p_record;
    ELSE
        v_operation := 'updated';
        EXECUTE format(
            'UPDATE public.%I AS target SET (%s) = (SELECT %s FROM jsonb_populate_record(NULL::public.%I, $1) AS source) WHERE id = $2 AND company_id = $3 RETURNING to_jsonb(target)',
            p_table, v_columns, v_selectors, p_table
        ) INTO v_written USING p_record, v_id, v_company;
    END IF;
    IF v_written IS NULL OR v_written->>'company_id' <> v_company::text THEN
        RAISE EXCEPTION 'Compliance record persistence was not confirmed';
    END IF;

    -- Store reference and fingerprints, not full license numbers or private data.
    -- This is an evidence pointer, not yet an immutable full version history.
    INSERT INTO public.audit_events (
        id, company_id, user_id, event_type, action_class, capability,
        path_used, entity_type, entity_id, description,
        before_snapshot, after_snapshot, policy_effect, policy_rule_id
    ) VALUES (
        v_event_id, v_company, v_actor_uuid,
        'compliance.record_' || v_operation, 'commit', 'compliance',
        'api', p_table, v_id::text,
        'Compliance ' || p_table || ' ' || v_operation || ' by ' || p_actor,
        CASE WHEN v_previous IS NULL THEN NULL ELSE
            jsonb_build_object('record_fingerprint', md5(v_previous::text)) END,
        jsonb_build_object(
            'record_fingerprint', md5(v_written::text),
            'submitted_fields', v_fields
        ),
        p_policy_effect, p_policy_rule_id
    );
    RETURN jsonb_build_object(
        'record', v_written,
        'audit_event_id', v_event_id::text,
        'operation', v_operation
    );
END;
$function$;

REVOKE ALL ON FUNCTION public.compliance_write_record_audited(text, jsonb, text, text, uuid)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.compliance_write_record_audited(text, jsonb, text, text, uuid)
    TO service_role;

COMMENT ON FUNCTION public.compliance_write_record_audited(text, jsonb, text, text, uuid) IS
    'Only service-role callers with an allow decision may atomically write a tenant compliance record and its append-only audit event.';

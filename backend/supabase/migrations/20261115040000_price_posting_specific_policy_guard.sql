-- Additive Phase 1 security patch. Every regulatory ledger write must
-- carry a currently-active, tenant-owned, API-path-scoped, capability-specific
-- COMMIT allow rule. Checking at AUDIT INSERT makes the entire service-role RPC
-- transaction roll back if the authorization proof is generic or withdrawn.
-- Does not enable agency filing, price publication or external accounting writes.

CREATE FUNCTION public.enforce_specific_price_posting_audit_policy()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public
AS $specific_policy$
DECLARE
    v_capability text;
    v_entity_type text;
BEGIN
    CASE NEW.event_type
      WHEN 'compliance.price_rule_review_recorded' THEN
        v_capability := 'compliance.rule_review';
        v_entity_type := 'compliance_price_posting_rule_versions';
      WHEN 'compliance.price_preparation_created' THEN
        v_capability := 'compliance.price_preparation';
        v_entity_type := 'compliance_price_posting_preparations';
      WHEN 'compliance.price_external_reference_recorded' THEN
        v_capability := 'compliance.price_reference';
        v_entity_type := 'compliance_price_posting_external_references';
      ELSE
        RETURN NEW;
    END CASE;

    IF NEW.action_class IS DISTINCT FROM 'commit'
       OR NEW.capability IS DISTINCT FROM v_capability
       OR NEW.path_used IS DISTINCT FROM 'api'
       OR NEW.entity_type IS DISTINCT FROM v_entity_type
       OR NEW.policy_effect IS DISTINCT FROM 'allow'
       OR NEW.policy_rule_id IS NULL
       OR NEW.user_id IS NULL
       OR NEW.company_id IS NULL THEN
        RAISE EXCEPTION 'Specific regulated audit context required'
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.users u
         WHERE u.id = NEW.user_id
           AND u.company_id = NEW.company_id
           AND u.is_active IS TRUE
           AND u.role IN ('owner','manager')
    ) THEN
        RAISE EXCEPTION 'Tenant owner or manager no longer authorized'
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.policy_rules r
         WHERE r.id = NEW.policy_rule_id
           AND r.company_id = NEW.company_id
           AND r.is_active IS TRUE
           AND r.action_class = 'commit'
           AND r.effect = 'allow'
           AND r.capabilities_match @> ARRAY[v_capability]::text[]
           AND r.paths_match @> ARRAY['api']::text[]
    ) THEN
        RAISE EXCEPTION 'Tenant policy lacks the exact regulated API capability'
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$specific_policy$;

CREATE TRIGGER trg_specific_price_posting_policy_audit
BEFORE INSERT ON public.audit_events
FOR EACH ROW EXECUTE FUNCTION public.enforce_specific_price_posting_audit_policy();

REVOKE ALL ON FUNCTION public.enforce_specific_price_posting_audit_policy()
FROM PUBLIC,anon,authenticated,service_role;

COMMENT ON FUNCTION public.enforce_specific_price_posting_audit_policy()
IS 'Atomic audit guard: named tenant-specific COMMIT allow for each regulated API capability. No filing authorization.';


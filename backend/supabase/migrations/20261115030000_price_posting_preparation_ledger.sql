-- Phase 1: immutable internal case ledger only. NO government submissions.
CREATE TABLE public.compliance_price_posting_preparations(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 company_id uuid NOT NULL REFERENCES public.companies(id) ON DELETE RESTRICT,
 rule_id uuid NOT NULL REFERENCES public.compliance_price_posting_rule_versions(id) ON DELETE RESTRICT,
 rule_version integer NOT NULL CHECK(rule_version>0),
 created_by uuid NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
 effective_date date NOT NULL,
 price_book_snapshot jsonb NOT NULL CHECK(jsonb_typeof(price_book_snapshot)='array'
     AND jsonb_array_length(price_book_snapshot) BETWEEN 1 AND 50),
 source_fingerprint text NOT NULL CHECK(length(source_fingerprint)=32),
 state text NOT NULL DEFAULT 'internal_preparation_only'
     CHECK(state='internal_preparation_only'),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(company_id,id)
);
CREATE INDEX idx_price_preparations_tenant
 ON public.compliance_price_posting_preparations(company_id,created_at DESC);

-- External document references are human assertions, never agency receipts.
CREATE TABLE public.compliance_price_posting_external_references(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 company_id uuid NOT NULL REFERENCES public.companies(id) ON DELETE RESTRICT,
 preparation_id uuid NOT NULL,
 created_by uuid NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
 reference_kind text NOT NULL CHECK(reference_kind IN
  ('external_document_reference','agency_message_reference','rejection_reference')),
 external_reference text NOT NULL CHECK(length(btrim(external_reference)) BETWEEN 10 AND 1500),
 claimed_event_date date,
 note text NOT NULL CHECK(length(btrim(note)) BETWEEN 30 AND 4000),
 provenance_status text NOT NULL DEFAULT 'human_reported_unverified'
  CHECK(provenance_status='human_reported_unverified'),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(company_id,preparation_id)
  REFERENCES public.compliance_price_posting_preparations(company_id,id)
  ON DELETE RESTRICT
);
CREATE INDEX idx_price_external_refs_tenant
 ON public.compliance_price_posting_external_references(company_id,preparation_id,created_at DESC);

CREATE FUNCTION public.reject_price_preparation_history_rewrite()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $immutable$
BEGIN
 RAISE EXCEPTION 'Internal preparation evidence is append-only' USING ERRCODE='42501';
END;$immutable$;
CREATE TRIGGER trg_price_preparations_immutable BEFORE UPDATE OR DELETE
 ON public.compliance_price_posting_preparations FOR EACH ROW
 EXECUTE FUNCTION public.reject_price_preparation_history_rewrite();
CREATE TRIGGER trg_price_external_references_immutable BEFORE UPDATE OR DELETE
 ON public.compliance_price_posting_external_references FOR EACH ROW
 EXECUTE FUNCTION public.reject_price_preparation_history_rewrite();

ALTER TABLE public.compliance_price_posting_preparations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.compliance_price_posting_external_references ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.compliance_price_posting_preparations,
 public.compliance_price_posting_external_references FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.compliance_price_posting_preparations,
 public.compliance_price_posting_external_references TO service_role;

CREATE FUNCTION public.create_price_posting_preparation(
 p_company_id uuid,p_rule_id uuid,p_rule_version integer,p_actor_id uuid,
 p_effective_date date,p_book_ids uuid[],p_policy_effect text,p_policy_rule_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $prepare$
DECLARE
 v_program text;v_version integer;v_region text;
 v_snapshot jsonb;v_count integer;v_id uuid;v_audit_id uuid:=gen_random_uuid();
 v_digest text;
BEGIN
 IF p_policy_effect IS DISTINCT FROM 'allow' THEN
  RAISE EXCEPTION 'Explicit tenant policy allow required' USING ERRCODE='42501';
 END IF;
 IF p_company_id IS NULL OR p_actor_id IS NULL OR p_rule_id IS NULL
   OR p_effective_date IS NULL OR p_book_ids IS NULL
   OR coalesce(array_length(p_book_ids,1),0) NOT BETWEEN 1 AND 50
   OR EXISTS(SELECT 1 FROM unnest(p_book_ids) AS x(id) WHERE x.id IS NULL)
   OR (SELECT count(DISTINCT x.id) FROM unnest(p_book_ids) AS x(id))
       <> array_length(p_book_ids,1) THEN
  RAISE EXCEPTION 'Invalid or duplicate price-book source IDs' USING ERRCODE='22023';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.users
   WHERE id=p_actor_id AND company_id=p_company_id AND is_active IS TRUE
     AND role IN('owner','manager')) THEN
  RAISE EXCEPTION 'Tenant owner/manager required' USING ERRCODE='42501';
 END IF;
 SELECT program_key,version,region_code INTO v_program,v_version,v_region
 FROM public.compliance_price_posting_rule_versions WHERE id=p_rule_id FOR SHARE;
 IF NOT FOUND OR v_version IS DISTINCT FROM p_rule_version
    OR EXISTS(SELECT 1 FROM public.compliance_price_posting_rule_versions
      WHERE program_key=v_program AND version>v_version) THEN
  RAISE EXCEPTION 'Superseded or missing rule version' USING ERRCODE='42501';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.compliance_price_posting_rule_reviews
  WHERE company_id=p_company_id AND rule_id=p_rule_id) THEN
  RAISE EXCEPTION 'Human source review must be recorded first' USING ERRCODE='42501';
 END IF;
 SELECT count(*),jsonb_agg(jsonb_build_object(
   'price_book_id',b.id::text,'sku',b.sku,'package_label',b.package_label,
   'market_code',b.market_code,'channel',b.channel,
   'customer_specific',(length(btrim(b.customer_ref))>0),
   'currency',b.currency,'list_price_per_case',b.list_price_per_case::text,
   'effective_from',b.effective_from::text,'effective_to',b.effective_to::text,
   'revision',b.revision,'status',b.status,
   'cost_snapshot_id',b.cost_snapshot_id::text,'evidence_ref',b.evidence_ref,
   'created_by',b.created_by::text,'approved_by',b.approved_by::text,
   'approved_at',b.approved_at::text) ORDER BY b.id::text)
 INTO v_count,v_snapshot FROM public.pricing_price_book b
 WHERE b.company_id=p_company_id AND b.id=ANY(p_book_ids);
 IF v_count IS DISTINCT FROM array_length(p_book_ids,1) THEN
  RAISE EXCEPTION 'Source price-book rows missing or foreign' USING ERRCODE='42501';
 END IF;
 IF EXISTS(SELECT 1 FROM public.pricing_price_book b
    WHERE b.company_id=p_company_id AND b.id=ANY(p_book_ids)
      AND (b.market_code IS DISTINCT FROM v_region
       OR b.effective_from>p_effective_date
       OR (b.effective_to IS NOT NULL AND b.effective_to<p_effective_date))) THEN
  RAISE EXCEPTION 'Price book source market or effective date mismatch'
      USING ERRCODE='42501';
 END IF;
 -- MD5 is only a deterministic change fingerprint, NOT an integrity signature.
 v_digest:=md5(v_snapshot::text);
 INSERT INTO public.compliance_price_posting_preparations(
  company_id,rule_id,rule_version,created_by,effective_date,price_book_snapshot,source_fingerprint)
 VALUES(p_company_id,p_rule_id,p_rule_version,p_actor_id,p_effective_date,v_snapshot,v_digest)
 RETURNING id INTO v_id;
 INSERT INTO public.audit_events(
  id,company_id,user_id,event_type,action_class,capability,path_used,
  entity_type,entity_id,description,after_snapshot,policy_effect,policy_rule_id)
 VALUES(v_audit_id,p_company_id,p_actor_id,'compliance.price_preparation_created',
  'commit','compliance.price_preparation','api','compliance_price_posting_preparations',
  v_id::text,'Internal preparation created; no price publishing or regulator filing',
  jsonb_build_object('rule_id',p_rule_id::text,'source_count',v_count,
   'source_fingerprint',v_digest),'allow',p_policy_rule_id);
 RETURN jsonb_build_object(
  'preparation_id',v_id::text,'audit_event_id',v_audit_id::text,
  'source_fingerprint',v_digest,'source_count',v_count,
  'state','internal_preparation_only','submission_permitted',false,
  'agency_confirmation',false,'price_publication_permitted',false);
END;$prepare$;

CREATE FUNCTION public.record_price_posting_external_reference(
 p_company_id uuid,p_preparation_id uuid,p_actor_id uuid,p_reference_kind text,
 p_external_reference text,p_claimed_event_date date,p_note text,p_policy_effect text,
 p_policy_rule_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $reference$
DECLARE v_id uuid;v_audit_id uuid:=gen_random_uuid();
BEGIN
 IF p_policy_effect IS DISTINCT FROM 'allow' THEN
  RAISE EXCEPTION 'Explicit tenant policy allow required' USING ERRCODE='42501';
 END IF;
 IF p_reference_kind NOT IN(
  'external_document_reference','agency_message_reference','rejection_reference')
  OR length(btrim(coalesce(p_external_reference,''))) NOT BETWEEN 10 AND 1500
  OR length(btrim(coalesce(p_note,''))) NOT BETWEEN 30 AND 4000 THEN
  RAISE EXCEPTION 'Invalid external reference fields' USING ERRCODE='22023';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=p_actor_id
  AND company_id=p_company_id AND is_active IS TRUE AND role IN('owner','manager'))
  OR NOT EXISTS(SELECT 1 FROM public.compliance_price_posting_preparations
    WHERE id=p_preparation_id AND company_id=p_company_id) THEN
  RAISE EXCEPTION 'Tenant identity or preparation missing' USING ERRCODE='42501';
 END IF;
 INSERT INTO public.compliance_price_posting_external_references(
  company_id,preparation_id,created_by,reference_kind,external_reference,claimed_event_date,note)
 VALUES(p_company_id,p_preparation_id,p_actor_id,p_reference_kind,
  btrim(p_external_reference),p_claimed_event_date,btrim(p_note)) RETURNING id INTO v_id;
 INSERT INTO public.audit_events(
  id,company_id,user_id,event_type,action_class,capability,path_used,
  entity_type,entity_id,description,after_snapshot,policy_effect,policy_rule_id)
 VALUES(v_audit_id,p_company_id,p_actor_id,'compliance.price_external_reference_recorded',
  'commit','compliance.price_reference','api','compliance_price_posting_external_references',
  v_id::text,'Unverified human-reported reference recorded; no agency verification',
  jsonb_build_object('preparation_id',p_preparation_id::text,'kind',p_reference_kind),
  'allow',p_policy_rule_id);
 RETURN jsonb_build_object('reference_id',v_id::text,'audit_event_id',v_audit_id::text,
  'provenance_status','human_reported_unverified',
  'agency_confirmation',false,'submission_permitted',false);
END;$reference$;

REVOKE ALL ON FUNCTION public.create_price_posting_preparation(
 uuid,uuid,integer,uuid,date,uuid[],text,uuid) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.record_price_posting_external_reference(
 uuid,uuid,uuid,text,text,date,text,text,uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.create_price_posting_preparation(
 uuid,uuid,integer,uuid,date,uuid[],text,uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_price_posting_external_reference(
 uuid,uuid,uuid,text,text,date,text,text,uuid) TO service_role;

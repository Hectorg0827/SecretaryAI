-- SecretaryAI V7: metadata-only procurement evidence claims.
-- These rows are NOT verified files, approved invoices, landed-cost balances,
-- inventory adjustments or QuickBooks journal entries.
-- Upload/storage and server-verified SHA-256 attestation are separate future gates.

CREATE TABLE IF NOT EXISTS purchasing_evidence_claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL,
    po_id UUID NOT NULL,
    evidence_key TEXT NOT NULL CHECK(length(btrim(evidence_key)) BETWEEN 1 AND 150),
    document_type TEXT NOT NULL CHECK(document_type IN (
      'purchase_invoice','packing_list','bill_of_lading','receiving_photo',
      'damage_report','freight_invoice','customs_entry','insurance_invoice',
      'broker_invoice','drayage_invoice','other'
    )),
    storage_object_key TEXT NOT NULL,
    claimed_sha256 TEXT NOT NULL CHECK(claimed_sha256 ~ '^[0-9a-f]{64}$'),
    claimed_amount NUMERIC(18,2),
    currency TEXT,
    submitted_by UUID NOT NULL REFERENCES users(id),
    verification_status TEXT NOT NULL DEFAULT 'unverified'
      CHECK(verification_status = 'unverified'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id,id),
    UNIQUE(company_id,evidence_key),
    FOREIGN KEY(company_id,po_id) REFERENCES purchasing_orders(company_id,id),
    CHECK(claimed_amount IS NULL OR
      (claimed_amount >= 0 AND currency ~ '^[A-Z]{3}$')),
    CHECK(claimed_amount IS NOT NULL OR currency IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_purchase_evidence_po
  ON purchasing_evidence_claims(company_id,po_id,created_at DESC);

CREATE OR REPLACE FUNCTION reject_purchase_evidence_rewrite()
RETURNS trigger LANGUAGE plpgsql AS $immutable$
BEGIN
 RAISE EXCEPTION 'submitted evidence claim is immutable'
   USING ERRCODE='23514';
END
$immutable$;
CREATE TRIGGER trg_purchase_evidence_immutable
  BEFORE UPDATE OR DELETE ON purchasing_evidence_claims
  FOR EACH ROW EXECUTE FUNCTION reject_purchase_evidence_rewrite();

ALTER TABLE purchasing_evidence_claims ENABLE ROW LEVEL SECURITY;
CREATE POLICY purchasing_evidence_tenant_read
  ON purchasing_evidence_claims FOR SELECT
  USING(company_id=(auth.jwt()->>'company_id')::UUID);
REVOKE ALL ON purchasing_evidence_claims
  FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON purchasing_evidence_claims TO authenticated,service_role;

CREATE OR REPLACE FUNCTION record_purchasing_evidence_claim(
    p_company_id UUID,
    p_po_number TEXT,
    p_evidence_key TEXT,
    p_document_type TEXT,
    p_storage_object_key TEXT,
    p_claimed_sha256 TEXT,
    p_claimed_amount NUMERIC,
    p_currency TEXT,
    p_actor UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $record_evidence$
DECLARE
    v_po purchasing_orders%ROWTYPE;
    v_previous purchasing_evidence_claims%ROWTYPE;
    v_id UUID;
    v_key TEXT := btrim(p_evidence_key);
    v_object TEXT := btrim(p_storage_object_key);
    v_currency TEXT := nullif(upper(btrim(p_currency)),'');
    v_digest TEXT := lower(btrim(p_claimed_sha256));
BEGIN
    IF p_company_id IS NULL OR p_actor IS NULL OR
       p_po_number IS NULL OR length(btrim(p_po_number)) NOT BETWEEN 1 AND 100 OR
       v_key IS NULL OR length(v_key) NOT BETWEEN 1 AND 150 OR
       p_document_type IS NULL OR p_document_type NOT IN (
         'purchase_invoice','packing_list','bill_of_lading','receiving_photo',
         'damage_report','freight_invoice','customs_entry','insurance_invoice',
         'broker_invoice','drayage_invoice','other') OR
       v_object IS NULL OR length(v_object) NOT BETWEEN 1 AND 400 OR
       left(v_object,length(p_company_id::TEXT || '/purchasing/'))
           <> p_company_id::TEXT || '/purchasing/' OR
       v_object !~ '^[A-Za-z0-9_./-]+$' OR
       position('..' IN v_object) > 0 OR
       v_digest IS NULL OR v_digest !~ '^[0-9a-f]{64}$' OR
       (p_claimed_amount IS NULL AND v_currency IS NOT NULL) OR
       (p_claimed_amount IS NOT NULL AND
         (p_claimed_amount < 0 OR
          p_claimed_amount='NaN'::NUMERIC OR
          p_claimed_amount<>round(p_claimed_amount,2) OR
          v_currency IS NULL OR v_currency !~ '^[A-Z]{3}$'))
    THEN
       RAISE EXCEPTION 'invalid purchasing evidence metadata'
         USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM users WHERE id=p_actor AND
        company_id=p_company_id AND is_active AND
        role IN('owner','manager','back_office')) THEN
       RAISE EXCEPTION 'unauthorized evidence submitter'
         USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_po FROM purchasing_orders
      WHERE company_id=p_company_id AND po_number=btrim(p_po_number)
      FOR UPDATE;
    IF NOT FOUND THEN
       RAISE EXCEPTION 'purchase order not found'
         USING ERRCODE='P0002';
    END IF;
    IF v_po.status='draft' THEN
       RAISE EXCEPTION 'draft PO not eligible for evidence claims'
         USING ERRCODE='23514';
    END IF;

    INSERT INTO purchasing_evidence_claims(
      company_id,po_id,evidence_key,document_type,storage_object_key,
      claimed_sha256,claimed_amount,currency,submitted_by
    ) VALUES (
      p_company_id,v_po.id,v_key,p_document_type,v_object,
      v_digest,p_claimed_amount,v_currency,p_actor
    ) ON CONFLICT(company_id,evidence_key) DO NOTHING
      RETURNING id INTO v_id;
    IF v_id IS NULL THEN
      SELECT * INTO v_previous FROM purchasing_evidence_claims
      WHERE company_id=p_company_id AND evidence_key=v_key;
      IF NOT FOUND OR v_previous.po_id<>v_po.id OR
         v_previous.document_type<>p_document_type OR
         v_previous.storage_object_key<>v_object OR
         v_previous.claimed_sha256<>v_digest OR
         v_previous.claimed_amount IS DISTINCT FROM p_claimed_amount OR
         v_previous.currency IS DISTINCT FROM v_currency OR
         v_previous.submitted_by<>p_actor THEN
         RAISE EXCEPTION 'evidence idempotency key reused with new contents'
           USING ERRCODE='23505';
      END IF;
      RETURN jsonb_build_object('evidence_id',v_previous.id,'replayed',TRUE,
        'verification_status','unverified','accounting_reconciled',FALSE);
    END IF;
    RETURN jsonb_build_object('evidence_id',v_id,'replayed',FALSE,
      'verification_status','unverified','accounting_reconciled',FALSE);
END
$record_evidence$;

REVOKE ALL ON FUNCTION record_purchasing_evidence_claim(
  UUID,TEXT,TEXT,TEXT,TEXT,TEXT,NUMERIC,TEXT,UUID
) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION record_purchasing_evidence_claim(
  UUID,TEXT,TEXT,TEXT,TEXT,TEXT,NUMERIC,TEXT,UUID
) TO service_role;

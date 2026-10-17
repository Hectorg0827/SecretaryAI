-- V8: server-computed integrity attestation for private evidence objects.
-- The claimed document is not malware-cleared, invoice-approved, or posted to QB.
-- Requires V5 -> V6 -> V7 before this migration.
CREATE TABLE IF NOT EXISTS purchasing_evidence_integrity_checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL,
    evidence_id UUID NOT NULL,
    storage_object_key TEXT NOT NULL,
    computed_sha256 TEXT NOT NULL CHECK(computed_sha256 ~ '^[0-9a-f]{64}$'),
    byte_count INTEGER NOT NULL CHECK(byte_count BETWEEN 1 AND 8388608),
    detected_mime TEXT NOT NULL
      CHECK(detected_mime IN ('application/pdf','image/jpeg','image/png')),
    verified_by UUID NOT NULL REFERENCES users(id),
    integrity_status TEXT NOT NULL DEFAULT 'sha256_matched_at_upload'
      CHECK(integrity_status='sha256_matched_at_upload'),
    malware_scan_status TEXT NOT NULL DEFAULT 'not_scanned'
      CHECK(malware_scan_status='not_scanned'),
    financial_review_status TEXT NOT NULL DEFAULT 'not_reviewed'
      CHECK(financial_review_status='not_reviewed'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id,evidence_id),
    UNIQUE(company_id,id),
    FOREIGN KEY(company_id,evidence_id)
       REFERENCES purchasing_evidence_claims(company_id,id)
);
ALTER TABLE purchasing_evidence_integrity_checks ENABLE ROW LEVEL SECURITY;
CREATE POLICY evidence_integrity_tenant_read
 ON purchasing_evidence_integrity_checks FOR SELECT
 USING(company_id=(auth.jwt()->>'company_id')::UUID);
REVOKE ALL ON purchasing_evidence_integrity_checks
  FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON purchasing_evidence_integrity_checks
  TO authenticated,service_role;
CREATE TRIGGER trg_evidence_integrity_immutable
 BEFORE UPDATE OR DELETE ON purchasing_evidence_integrity_checks
 FOR EACH ROW EXECUTE FUNCTION reject_purchase_evidence_rewrite();

CREATE OR REPLACE FUNCTION attest_purchasing_evidence_integrity(
    p_company_id UUID,p_evidence_id UUID,p_computed_sha256 TEXT,
    p_byte_count INTEGER,p_detected_mime TEXT,p_actor UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $attest$
DECLARE
 v_claim purchasing_evidence_claims%ROWTYPE;
 v_previous purchasing_evidence_integrity_checks%ROWTYPE;
 v_id UUID;
BEGIN
 IF p_computed_sha256 IS NULL OR
    p_computed_sha256 !~ '^[0-9a-f]{64}$' OR
    p_byte_count NOT BETWEEN 1 AND 8388608 OR
    p_detected_mime NOT IN ('application/pdf','image/jpeg','image/png')
 THEN
   RAISE EXCEPTION 'invalid integrity evidence' USING ERRCODE='22023';
 END IF;
 IF NOT EXISTS (
   SELECT 1 FROM users WHERE id=p_actor AND company_id=p_company_id
   AND is_active AND role IN ('owner','manager','back_office')
 ) THEN
   RAISE EXCEPTION 'unauthorized integrity reviewer' USING ERRCODE='42501';
 END IF;
 SELECT * INTO v_claim FROM purchasing_evidence_claims
 WHERE company_id=p_company_id AND id=p_evidence_id FOR SHARE;
 IF NOT FOUND THEN
    RAISE EXCEPTION 'evidence claim missing' USING ERRCODE='P0002';
 END IF;
 IF v_claim.claimed_sha256 <> p_computed_sha256 THEN
   RAISE EXCEPTION 'uploaded evidence does not match claimed digest'
    USING ERRCODE='23514';
 END IF;
 IF NOT (
      (p_detected_mime='application/pdf' AND
         right(v_claim.storage_object_key,4)='.pdf') OR
      (p_detected_mime='image/png' AND
         right(v_claim.storage_object_key,4)='.png') OR
      (p_detected_mime='image/jpeg' AND
         (right(v_claim.storage_object_key,4)='.jpg' OR
          right(v_claim.storage_object_key,5)='.jpeg'))
 ) THEN
    RAISE EXCEPTION 'extension conflicts with detected file type'
      USING ERRCODE='23514';
 END IF;
 INSERT INTO purchasing_evidence_integrity_checks(
   company_id,evidence_id,storage_object_key,computed_sha256,byte_count,
   detected_mime,verified_by
 ) VALUES (
   p_company_id,p_evidence_id,v_claim.storage_object_key,
   p_computed_sha256,p_byte_count,p_detected_mime,p_actor
 ) ON CONFLICT(company_id,evidence_id) DO NOTHING RETURNING id INTO v_id;
 IF v_id IS NULL THEN
   SELECT * INTO v_previous FROM purchasing_evidence_integrity_checks
   WHERE company_id=p_company_id AND evidence_id=p_evidence_id;
   IF NOT FOUND OR v_previous.computed_sha256<>p_computed_sha256 OR
     v_previous.byte_count<>p_byte_count OR
     v_previous.detected_mime<>p_detected_mime OR
     v_previous.verified_by<>p_actor THEN
     RAISE EXCEPTION 'conflicting integrity attestation'
       USING ERRCODE='23505';
   END IF;
   RETURN jsonb_build_object('integrity_check_id',v_previous.id,
     'replayed',TRUE,'integrity_status','sha256_matched_at_upload',
     'malware_scan_status','not_scanned','safe_to_view',FALSE,
     'financial_review_status','not_reviewed','accounting_reconciled',FALSE);
 END IF;
 RETURN jsonb_build_object('integrity_check_id',v_id,'replayed',FALSE,
   'integrity_status','sha256_matched_at_upload',
   'malware_scan_status','not_scanned','safe_to_view',FALSE,
   'financial_review_status','not_reviewed','accounting_reconciled',FALSE);
END
$attest$;

REVOKE ALL ON FUNCTION attest_purchasing_evidence_integrity(
  UUID,UUID,TEXT,INTEGER,TEXT,UUID
) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION attest_purchasing_evidence_integrity(
  UUID,UUID,TEXT,INTEGER,TEXT,UUID
) TO service_role;

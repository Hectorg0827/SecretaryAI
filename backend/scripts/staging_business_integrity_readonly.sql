-- READ ONLY. Approved environment: SecretaryAI-Staging only.
-- Report aggregate counts. No credentials or customer/supplier identifiers.
-- Zero violations does NOT establish real provider, QBO or price certification.
WITH checks AS (
 SELECT 'po_line_wrong_tenant_or_missing_po' check_name,COUNT(*)::bigint violations
 FROM purchasing_order_lines l LEFT JOIN purchasing_orders p ON p.id=l.po_id AND p.company_id=l.company_id WHERE p.id IS NULL
 UNION ALL SELECT 'receipt_doc_wrong_tenant_or_missing_po',COUNT(*) FROM warehouse_receipt_documents d LEFT JOIN purchasing_orders p ON p.id=d.po_id AND p.company_id=d.company_id WHERE p.id IS NULL
 UNION ALL SELECT 'receipt_line_wrong_tenant_or_missing_doc',COUNT(*) FROM warehouse_receipt_lines l LEFT JOIN warehouse_receipt_documents d ON d.id=l.receipt_id AND d.company_id=l.company_id WHERE d.id IS NULL
 UNION ALL SELECT 'receipt_sku_not_ordered',COUNT(*) FROM warehouse_receipt_lines l JOIN warehouse_receipt_documents d ON d.id=l.receipt_id AND d.company_id=l.company_id LEFT JOIN purchasing_order_lines p ON p.po_id=d.po_id AND p.company_id=d.company_id AND p.sku=l.sku WHERE p.sku IS NULL
 UNION ALL SELECT 'assessment_wrong_tenant_or_missing_po',COUNT(*) FROM purchasing_cost_assessments a LEFT JOIN purchasing_orders p ON p.id=a.po_id AND p.company_id=a.company_id WHERE p.id IS NULL
 UNION ALL SELECT 'pricebook_foreign_snapshot',COUNT(*) FROM pricing_price_book b JOIN pricing_cost_snapshots s ON s.id=b.cost_snapshot_id WHERE b.company_id IS DISTINCT FROM s.company_id
 UNION ALL SELECT 'costline_wrong_tenant_or_missing_snapshot',COUNT(*) FROM pricing_cost_lines l LEFT JOIN pricing_cost_snapshots s ON s.id=l.cost_snapshot_id AND s.company_id=l.company_id WHERE s.id IS NULL
 UNION ALL SELECT 'receipt_same_receiver_approver',COUNT(*) FROM warehouse_receipt_documents WHERE received_by=approved_by
 UNION ALL SELECT 'receipt_negative_quantities',COUNT(*) FROM warehouse_receipt_lines WHERE accepted_qty<0 OR damaged_qty<0 OR quarantined_qty<0
 UNION ALL SELECT 'po_negative_quantities',COUNT(*) FROM purchasing_order_lines WHERE qty_ordered<0 OR qty_received<0
 UNION ALL SELECT 'po_over_received',COUNT(*) FROM purchasing_order_lines WHERE qty_received>qty_ordered
)
SELECT check_name,violations FROM checks ORDER BY check_name;

-- Evidence coverage only: missing records block financial signoff.
SELECT 'purchasing_evidence_claims' kind,COUNT(*)::bigint records FROM purchasing_evidence_claims
UNION ALL SELECT 'evidence_integrity_checks',COUNT(*) FROM purchasing_evidence_integrity_checks
UNION ALL SELECT 'evidence_malware_scans',COUNT(*) FROM purchasing_evidence_malware_scans
UNION ALL SELECT 'evidence_review_approvals',COUNT(*) FROM purchasing_evidence_review_approvals
UNION ALL SELECT 'provisional_cost_assessments',COUNT(*) FROM purchasing_cost_assessments WHERE assertion_status='provisional'
UNION ALL SELECT 'reconciled_cost_assessments',COUNT(*) FROM purchasing_cost_assessments WHERE assertion_status='reconciled'
UNION ALL SELECT 'price_book',COUNT(*) FROM pricing_price_book
UNION ALL SELECT 'pricing_cost_snapshots',COUNT(*) FROM pricing_cost_snapshots
UNION ALL SELECT 'pricing_cost_lines',COUNT(*) FROM pricing_cost_lines
UNION ALL SELECT 'logistics_events',COUNT(*) FROM logistics_events
ORDER BY kind;

-- Quantity parity. This is not independent WMS/physical proof.
WITH po AS (
 SELECT p.id,p.company_id,p.approved_at,COALESCE(SUM(l.qty_ordered),0) ordered,COALESCE(SUM(l.qty_received),0) received
 FROM purchasing_orders p LEFT JOIN purchasing_order_lines l ON l.company_id=p.company_id AND l.po_id=p.id
 GROUP BY p.id,p.company_id,p.approved_at
), receipt AS (
 SELECT d.company_id,d.po_id,COUNT(*) receipt_count,COUNT(*) FILTER (WHERE posted_at IS NOT NULL) posted,
 COUNT(*) FILTER (WHERE received_by=approved_by) same_person
 FROM warehouse_receipt_documents d GROUP BY d.company_id,d.po_id
)
SELECT CASE WHEN COALESCE(r.receipt_count,0)>0 THEN 'has_receipt' ELSE 'no_receipt' END receipt_group,
 COUNT(*)::bigint purchase_orders,
 COUNT(*) FILTER (WHERE p.approved_at IS NOT NULL)::bigint approved_orders,
 SUM(p.ordered) ordered_cases,SUM(p.received) recorded_received_cases,
 SUM(COALESCE(r.posted,0)) posted_receipts,SUM(COALESCE(r.same_person,0)) same_person_receipts
 FROM po p LEFT JOIN receipt r ON r.po_id=p.id AND r.company_id=p.company_id
 GROUP BY receipt_group ORDER BY receipt_group;

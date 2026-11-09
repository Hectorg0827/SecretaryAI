-- WS3 logistics_events: append-only, idempotent, backend-only. Rollback-only fixtures.
\set ON_ERROR_STOP on
BEGIN;
DO $lg$
DECLARE a UUID; denied BOOLEAN;
BEGIN
  INSERT INTO companies(name) VALUES('Logistics A') RETURNING id INTO a;
  INSERT INTO logistics_events(event_id,company_id,shipment_id,kind,source,recorded_at,idempotency_key)
    VALUES('e1',a,'SHP-1','milestone','carrier_api','2026-11-09T00:00:00Z','k1');
  denied:=FALSE;
  BEGIN
    INSERT INTO logistics_events(event_id,company_id,shipment_id,kind,source,recorded_at,idempotency_key)
      VALUES('e2',a,'SHP-1','milestone','carrier_api','2026-11-09T00:00:00Z','k1');
  EXCEPTION WHEN unique_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'duplicate event applied twice'; END IF;
  denied:=FALSE;
  BEGIN
    UPDATE logistics_events SET code='x' WHERE event_id='e1';
  EXCEPTION WHEN raise_exception THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'logistics event rewritten'; END IF;
  denied:=FALSE;
  BEGIN
    INSERT INTO logistics_events(event_id,company_id,shipment_id,kind,source,recorded_at,idempotency_key)
      VALUES('e3',a,'SHP-1','milestone','user_confirmed','2026-11-09T00:00:00Z','k3');
  EXCEPTION WHEN check_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'unattributed human confirmation allowed'; END IF;
  IF has_table_privilege('anon','logistics_events','SELECT')
     OR has_table_privilege('authenticated','logistics_events','SELECT')
     OR has_table_privilege('authenticated','logistics_events','INSERT') THEN
    RAISE EXCEPTION 'browser role can reach logistics_events';
  END IF;
END
$lg$;
ROLLBACK;

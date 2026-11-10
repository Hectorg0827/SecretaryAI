-- Lion BI / Lola core tables: real PostgreSQL constraints, rollback-only fixtures.
\set ON_ERROR_STOP on
BEGIN;
DO $lola$
DECLARE
  a UUID; b UUID; owner_a UUID; rep_a UUID; owner_b UUID; receipt UUID; denied BOOLEAN;
BEGIN
  INSERT INTO companies(name) VALUES('Lola A') RETURNING id INTO a;
  INSERT INTO companies(name) VALUES('Lola B') RETURNING id INTO b;
  INSERT INTO users(company_id,email,hashed_password,role)
    VALUES(a,'owner-a@lola.test','x','owner') RETURNING id INTO owner_a;
  INSERT INTO users(company_id,email,hashed_password,role)
    VALUES(a,'rep-a@lola.test','x','sales_rep') RETURNING id INTO rep_a;
  INSERT INTO users(company_id,email,hashed_password,role)
    VALUES(b,'owner-b@lola.test','x','owner') RETURNING id INTO owner_b;

  -- Org profile: manager must be in the same company.
  INSERT INTO lola_org_members(company_id,user_id,department,manager_user_id,territories)
    VALUES(a,rep_a,'sales',owner_a,ARRAY['West']);
  denied:=FALSE;
  BEGIN
    INSERT INTO lola_org_members(company_id,user_id,manager_user_id) VALUES(a,owner_a,owner_b);
  EXCEPTION WHEN foreign_key_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'cross-tenant manager allowed'; END IF;

  -- Delegations: same tenant, bounded expiry, no HR/payroll.
  INSERT INTO lola_delegations(company_id,grantor_user_id,grantee_user_id,resource_types,reason,expires_at)
    VALUES(a,owner_a,rep_a,ARRAY['customer_account'],'vacation cover',now()+INTERVAL '7 days');
  denied:=FALSE;
  BEGIN
    INSERT INTO lola_delegations(company_id,grantor_user_id,grantee_user_id,resource_types,reason,expires_at)
      VALUES(a,owner_a,owner_b,ARRAY['sales'],'cross tenant',now()+INTERVAL '1 day');
  EXCEPTION WHEN foreign_key_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'cross-tenant delegation allowed'; END IF;
  denied:=FALSE;
  BEGIN
    INSERT INTO lola_delegations(company_id,grantor_user_id,grantee_user_id,resource_types,reason,expires_at)
      VALUES(a,owner_a,rep_a,ARRAY['payroll'],'nope',now()+INTERVAL '1 day');
  EXCEPTION WHEN check_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'payroll delegation allowed'; END IF;
  denied:=FALSE;
  BEGIN
    INSERT INTO lola_delegations(company_id,grantor_user_id,grantee_user_id,resource_types,reason,expires_at)
      VALUES(a,owner_a,rep_a,ARRAY['sales'],'forever',now()+INTERVAL '365 days');
  EXCEPTION WHEN check_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'delegation beyond 90 days allowed'; END IF;

  -- Memory: confidential memory cannot be shared.
  INSERT INTO lola_memories(company_id,owner_user_id,scope,kind,sensitivity,content)
    VALUES(a,rep_a,'team','preference','internal','short answers please');
  denied:=FALSE;
  BEGIN
    INSERT INTO lola_memories(company_id,owner_user_id,scope,kind,sensitivity,content)
      VALUES(a,owner_a,'company','business_context','confidential','margin is 42%');
  EXCEPTION WHEN check_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'shared confidential memory allowed'; END IF;

  -- Receipts: idempotent, never "completed" without verification, immutable, permanent.
  INSERT INTO lola_action_receipts(company_id,proposal_id,action_type,risk_tier,status,idempotency_key,
                                   requested_by,proposal)
    VALUES(a,gen_random_uuid(),'draft_customer_email','approval_required','pending_approval','k1',rep_a,'{}')
    RETURNING id INTO receipt;
  INSERT INTO lola_action_receipts(company_id,proposal_id,action_type,risk_tier,status,idempotency_key,
                                   requested_by,proposal)
    VALUES(b,gen_random_uuid(),'draft_customer_email','approval_required','pending_approval','k1',owner_b,'{}');
  denied:=FALSE;
  BEGIN
    INSERT INTO lola_action_receipts(company_id,proposal_id,action_type,risk_tier,status,idempotency_key,
                                     requested_by,proposal)
      VALUES(a,gen_random_uuid(),'draft_customer_email','approval_required','pending_approval','k1',rep_a,'{}');
  EXCEPTION WHEN unique_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'duplicate idempotency key allowed'; END IF;

  denied:=FALSE;
  BEGIN
    UPDATE lola_action_receipts SET status='completed' WHERE id=receipt;
  EXCEPTION WHEN check_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'unverified completion allowed'; END IF;

  denied:=FALSE;
  BEGIN
    UPDATE lola_action_receipts SET requested_by=owner_a WHERE id=receipt;
  EXCEPTION WHEN check_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'receipt identity rewritten'; END IF;

  UPDATE lola_action_receipts
     SET status='completed', verified=TRUE, external_reference='gmail:abc',
         history='[{"event":"done"}]'::jsonb
   WHERE id=receipt;
  denied:=FALSE;
  BEGIN
    UPDATE lola_action_receipts SET status='failed', verified=FALSE WHERE id=receipt;
  EXCEPTION WHEN check_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'final receipt reopened'; END IF;

  denied:=FALSE;
  BEGIN
    UPDATE lola_action_receipts SET history='[]'::jsonb WHERE id=receipt;
  EXCEPTION WHEN check_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'receipt history truncated'; END IF;

  denied:=FALSE;
  BEGIN
    DELETE FROM lola_action_receipts WHERE id=receipt;
  EXCEPTION WHEN insufficient_privilege THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'receipt deleted'; END IF;

  -- Browser keys get nothing.
  IF has_table_privilege('anon','lola_memories','SELECT')
     OR has_table_privilege('authenticated','lola_memories','SELECT')
     OR has_table_privilege('authenticated','lola_action_receipts','SELECT')
     OR has_table_privilege('authenticated','lola_org_members','UPDATE')
     OR has_table_privilege('authenticated','lola_delegations','INSERT') THEN
    RAISE EXCEPTION 'browser role can reach Lola tables';
  END IF;
  IF has_table_privilege('service_role','lola_action_receipts','DELETE')
     OR has_table_privilege('service_role','lola_memories','DELETE') THEN
    RAISE EXCEPTION 'service role can hard-delete Lola history';
  END IF;
END
$lola$;
ROLLBACK;

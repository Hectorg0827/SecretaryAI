-- Lola Stage 12: additive, dormant recovery epoch and immutable audit ledger.
-- GitHub staging candidate ONLY. Never apply to a hosted Supabase project
-- without new explicit authorization. No data is reset by this migration.
-- Requires Stage 8 replay tables + Stage 10 consent/identity triggers.
BEGIN;

ALTER TABLE public.lola_provider_replay_checkpoints
  ADD COLUMN recovery_epoch BIGINT NOT NULL DEFAULT 0
  CHECK (recovery_epoch >= 0);
ALTER TABLE public.lola_provider_replay_events
  ADD COLUMN recovery_epoch BIGINT NOT NULL DEFAULT 0
  CHECK (recovery_epoch >= 0);

CREATE TABLE public.lola_provider_replay_recoveries (
  company_id UUID NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL CHECK (provider='gmail'),
  provider_identity_sha256 TEXT NOT NULL CHECK (provider_identity_sha256 ~ '^[0-9a-f]{64}$'),
  resource_sha256 TEXT NOT NULL CHECK (resource_sha256 ~ '^[0-9a-f]{64}$'),
  mapping_revision TEXT NOT NULL CHECK (length(btrim(mapping_revision)) BETWEEN 1 AND 200),
  old_generation BIGINT NOT NULL CHECK (old_generation >= 1),
  new_generation BIGINT NOT NULL CHECK (new_generation = old_generation + 1),
  old_epoch BIGINT NOT NULL CHECK (old_epoch >= 0),
  new_epoch BIGINT NOT NULL CHECK (new_epoch = old_epoch + 1),
  old_cursor_sha256 TEXT NOT NULL CHECK (old_cursor_sha256 ~ '^[0-9a-f]{64}$'),
  new_cursor_sha256 TEXT NOT NULL CHECK (new_cursor_sha256 ~ '^[0-9a-f]{64}$'),
  old_replay_digest TEXT NOT NULL CHECK (old_replay_digest ~ '^[0-9a-f]{64}$'),
  new_replay_digest TEXT NOT NULL CHECK (new_replay_digest ~ '^[0-9a-f]{64}$'),
  reason TEXT NOT NULL CHECK (reason = 'gmail_history_404'),
  initial_message_count INTEGER NOT NULL CHECK (initial_message_count >= 0),
  new_event_count INTEGER NOT NULL CHECK (new_event_count BETWEEN 0 AND 500),
  recovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (
    company_id,employee_id,provider,provider_identity_sha256,resource_sha256,new_epoch
  ),
  FOREIGN KEY (
    company_id,employee_id,provider,provider_identity_sha256,resource_sha256
  ) REFERENCES public.lola_provider_replay_checkpoints(
    company_id,employee_id,provider,provider_identity_sha256,resource_sha256
  ) ON DELETE RESTRICT
);
CREATE INDEX lola_provider_replay_recoveries_scope
  ON public.lola_provider_replay_recoveries(company_id,employee_id,provider,recovered_at DESC);
ALTER TABLE public.lola_provider_replay_recoveries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.lola_provider_replay_recoveries
  FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT ON public.lola_provider_replay_recoveries TO service_role;
-- No browser-readable history, no unprivileged recovery RPCs, no deletes.

CREATE FUNCTION public.lola_recover_expired_gmail_replay(
  p_company_id UUID,
  p_employee_id UUID,
  p_provider_identity_sha256 TEXT,
  p_resource_sha256 TEXT,
  p_mapping_revision TEXT,
  p_expected_generation BIGINT,
  p_expected_cursor_sha256 TEXT,
  p_cursor_ciphertext TEXT,
  p_cursor_sha256 TEXT,
  p_replay_digest TEXT,
  p_replayed_through TIMESTAMPTZ,
  p_initial_message_count INTEGER,
  p_events JSONB,
  p_reason TEXT
) RETURNS BIGINT
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path=pg_catalog, public
AS $body$
DECLARE
  v_previous public.lola_provider_replay_checkpoints%ROWTYPE;
  v_new_generation BIGINT;
  v_new_epoch BIGINT;
  v_event JSONB;
  v_seen TEXT[] := ARRAY[]::TEXT[];
  v_key TEXT;
  v_op TEXT;
BEGIN
  -- Only the dedicated server connection can perform recovery. A browser
  -- request, SQL editor session or user JWT must never be sufficient.
  IF current_user IS DISTINCT FROM 'service_role'
     OR current_setting('request.jwt.claim.role', TRUE)
        IS DISTINCT FROM 'service_role' THEN
    RAISE EXCEPTION 'Provider recovery writer unavailable';
  END IF;
  IF p_company_id IS NULL OR p_employee_id IS NULL
     OR p_provider_identity_sha256 !~ '^[0-9a-f]{64}$'
     OR p_resource_sha256 !~ '^[0-9a-f]{64}$'
     OR length(btrim(p_mapping_revision)) NOT BETWEEN 1 AND 200
     OR p_expected_generation IS NULL OR p_expected_generation < 1
     OR p_expected_cursor_sha256 !~ '^[0-9a-f]{64}$'
     OR p_cursor_ciphertext !~ '^v1:[A-Za-z0-9+/=]+
     OR p_cursor_sha256 !~ '^[0-9a-f]{64}$'
     OR p_replay_digest !~ '^[0-9a-f]{64}$'
     OR p_replayed_through IS NULL
     OR p_replayed_through > now()+INTERVAL '5 minutes'
     OR p_initial_message_count IS NULL
     OR p_initial_message_count NOT BETWEEN 0 AND 400
     OR p_events IS NULL OR jsonb_typeof(p_events) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_events) > 500
     OR p_reason IS DISTINCT FROM 'gmail_history_404' THEN
    RAISE EXCEPTION 'Gmail recovery evidence malformed';
  END IF;
  -- Reject impossible rollback or reuse of previously stored evidence.
  IF p_expected_cursor_sha256 = p_cursor_sha256 THEN
    RAISE EXCEPTION 'Gmail recovery cursor unchanged';
  END IF;

  SELECT * INTO v_previous
  FROM public.lola_provider_replay_checkpoints
  WHERE company_id=p_company_id AND employee_id=p_employee_id
    AND provider='gmail'
    AND provider_identity_sha256=p_provider_identity_sha256
    AND resource_sha256=p_resource_sha256
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Gmail recovery requires existing checkpoint';
  END IF;
  IF v_previous.generation IS DISTINCT FROM p_expected_generation
     OR v_previous.cursor_sha256 IS DISTINCT FROM p_expected_cursor_sha256
     OR v_previous.mapping_revision IS DISTINCT FROM p_mapping_revision THEN
    RAISE EXCEPTION 'Gmail recovery compare-and-swap conflict';
  END IF;
  IF p_replayed_through < v_previous.replayed_through THEN
    RAISE EXCEPTION 'Gmail recovery watermark regressed';
  END IF;
  IF v_previous.recovery_epoch = 9223372036854775807
     OR v_previous.generation = 9223372036854775807 THEN
    RAISE EXCEPTION 'Gmail recovery counters exhausted';
  END IF;

  -- Validate the entire event batch before modifying the checkpoint.
  FOR v_event IN SELECT value FROM jsonb_array_elements(p_events)
  LOOP
    v_key := v_event->>'key_sha256';
    v_op := v_event->>'operation';
    IF v_key !~ '^[0-9a-f]{64}$'
       OR v_op NOT IN ('added','deleted','label_changed','changed')
       OR v_key=ANY(v_seen) THEN
      RAISE EXCEPTION 'Gmail recovery event evidence invalid';
    END IF;
    v_seen:=array_append(v_seen,v_key);
  END LOOP;

  v_new_generation := v_previous.generation+1;
  v_new_epoch := v_previous.recovery_epoch+1;
  -- The Stage 10 BEFORE UPDATE trigger rechecks active employee membership,
  -- BOTH current channel consent grants, verified contact/work mapping and
  -- the exact approved mailbox resource under row locks until COMMIT.
  UPDATE public.lola_provider_replay_checkpoints
    SET cursor_ciphertext=p_cursor_ciphertext,
        cursor_sha256=p_cursor_sha256,
        replay_digest=p_replay_digest,
        baseline_only=TRUE,
        recovery_epoch=v_new_epoch,
        replayed_through=p_replayed_through,
        committed_at=now(),
        generation=v_new_generation
  WHERE company_id=p_company_id AND employee_id=p_employee_id
    AND provider='gmail'
    AND provider_identity_sha256=p_provider_identity_sha256
    AND resource_sha256=p_resource_sha256;

  FOR v_event IN SELECT value FROM jsonb_array_elements(p_events)
  LOOP
    INSERT INTO public.lola_provider_replay_events(
      company_id,employee_id,provider,provider_identity_sha256,
      resource_sha256,event_key_sha256,operation,replay_digest,recovery_epoch
    ) VALUES (
      p_company_id,p_employee_id,'gmail',p_provider_identity_sha256,
      p_resource_sha256,v_event->>'key_sha256',v_event->>'operation',
      p_replay_digest,v_new_epoch
    );
    -- No ON CONFLICT DO NOTHING; any event collision rolls back the WHOLE
    -- transaction, including cursor and recovery-audit update.
  END LOOP;

  INSERT INTO public.lola_provider_replay_recoveries(
    company_id,employee_id,provider,provider_identity_sha256,resource_sha256,
    mapping_revision,old_generation,new_generation,old_epoch,new_epoch,
    old_cursor_sha256,new_cursor_sha256,old_replay_digest,new_replay_digest,
    reason,initial_message_count,new_event_count
  ) VALUES (
    p_company_id,p_employee_id,'gmail',p_provider_identity_sha256,p_resource_sha256,
    p_mapping_revision,v_previous.generation,v_new_generation,
    v_previous.recovery_epoch,v_new_epoch,
    v_previous.cursor_sha256,p_cursor_sha256,
    v_previous.replay_digest,p_replay_digest,
    p_reason,p_initial_message_count,jsonb_array_length(p_events)
  );
  RETURN v_new_generation;
END;
$body$;
REVOKE ALL ON FUNCTION public.lola_recover_expired_gmail_replay(
  UUID,UUID,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,INTEGER,JSONB,TEXT
) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.lola_recover_expired_gmail_replay(
  UUID,UUID,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,INTEGER,JSONB,TEXT
) TO service_role;
COMMIT;

     OR length(p_cursor_ciphertext) NOT BETWEEN 40 AND 8192
     OR p_cursor_sha256 !~ '^[0-9a-f]{64}$'
     OR p_replay_digest !~ '^[0-9a-f]{64}$'
     OR p_replayed_through IS NULL
     OR p_replayed_through > now()+INTERVAL '5 minutes'
     OR p_initial_message_count IS NULL
     OR p_initial_message_count NOT BETWEEN 0 AND 400
     OR p_events IS NULL OR jsonb_typeof(p_events) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_events) > 500
     OR p_reason IS DISTINCT FROM 'gmail_history_404' THEN
    RAISE EXCEPTION 'Gmail recovery evidence malformed';
  END IF;
  -- Reject impossible rollback or reuse of previously stored evidence.
  IF p_expected_cursor_sha256 = p_cursor_sha256 THEN
    RAISE EXCEPTION 'Gmail recovery cursor unchanged';
  END IF;

  SELECT * INTO v_previous
  FROM public.lola_provider_replay_checkpoints
  WHERE company_id=p_company_id AND employee_id=p_employee_id
    AND provider='gmail'
    AND provider_identity_sha256=p_provider_identity_sha256
    AND resource_sha256=p_resource_sha256
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Gmail recovery requires existing checkpoint';
  END IF;
  IF v_previous.generation IS DISTINCT FROM p_expected_generation
     OR v_previous.cursor_sha256 IS DISTINCT FROM p_expected_cursor_sha256
     OR v_previous.mapping_revision IS DISTINCT FROM p_mapping_revision THEN
    RAISE EXCEPTION 'Gmail recovery compare-and-swap conflict';
  END IF;
  IF p_replayed_through < v_previous.replayed_through THEN
    RAISE EXCEPTION 'Gmail recovery watermark regressed';
  END IF;
  IF v_previous.recovery_epoch = 9223372036854775807
     OR v_previous.generation = 9223372036854775807 THEN
    RAISE EXCEPTION 'Gmail recovery counters exhausted';
  END IF;

  -- Validate the entire event batch before modifying the checkpoint.
  FOR v_event IN SELECT value FROM jsonb_array_elements(p_events)
  LOOP
    v_key := v_event->>'key_sha256';
    v_op := v_event->>'operation';
    IF v_key !~ '^[0-9a-f]{64}$'
       OR v_op NOT IN ('added','deleted','label_changed','changed')
       OR v_key=ANY(v_seen) THEN
      RAISE EXCEPTION 'Gmail recovery event evidence invalid';
    END IF;
    v_seen:=array_append(v_seen,v_key);
  END LOOP;

  v_new_generation := v_previous.generation+1;
  v_new_epoch := v_previous.recovery_epoch+1;
  -- The Stage 10 BEFORE UPDATE trigger rechecks active employee membership,
  -- BOTH current channel consent grants, verified contact/work mapping and
  -- the exact approved mailbox resource under row locks until COMMIT.
  UPDATE public.lola_provider_replay_checkpoints
    SET cursor_ciphertext=p_cursor_ciphertext,
        cursor_sha256=p_cursor_sha256,
        replay_digest=p_replay_digest,
        baseline_only=TRUE,
        recovery_epoch=v_new_epoch,
        replayed_through=p_replayed_through,
        committed_at=now(),
        generation=v_new_generation
  WHERE company_id=p_company_id AND employee_id=p_employee_id
    AND provider='gmail'
    AND provider_identity_sha256=p_provider_identity_sha256
    AND resource_sha256=p_resource_sha256;

  FOR v_event IN SELECT value FROM jsonb_array_elements(p_events)
  LOOP
    INSERT INTO public.lola_provider_replay_events(
      company_id,employee_id,provider,provider_identity_sha256,
      resource_sha256,event_key_sha256,operation,replay_digest,recovery_epoch
    ) VALUES (
      p_company_id,p_employee_id,'gmail',p_provider_identity_sha256,
      p_resource_sha256,v_event->>'key_sha256',v_event->>'operation',
      p_replay_digest,v_new_epoch
    );
    -- No ON CONFLICT DO NOTHING; any event collision rolls back the WHOLE
    -- transaction, including cursor and recovery-audit update.
  END LOOP;

  INSERT INTO public.lola_provider_replay_recoveries(
    company_id,employee_id,provider,provider_identity_sha256,resource_sha256,
    mapping_revision,old_generation,new_generation,old_epoch,new_epoch,
    old_cursor_sha256,new_cursor_sha256,old_replay_digest,new_replay_digest,
    reason,initial_message_count,new_event_count
  ) VALUES (
    p_company_id,p_employee_id,'gmail',p_provider_identity_sha256,p_resource_sha256,
    p_mapping_revision,v_previous.generation,v_new_generation,
    v_previous.recovery_epoch,v_new_epoch,
    v_previous.cursor_sha256,p_cursor_sha256,
    v_previous.replay_digest,p_replay_digest,
    p_reason,p_initial_message_count,jsonb_array_length(p_events)
  );
  RETURN v_new_generation;
END;
$body$;
REVOKE ALL ON FUNCTION public.lola_recover_expired_gmail_replay(
  UUID,UUID,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,INTEGER,JSONB,TEXT
) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.lola_recover_expired_gmail_replay(
  UUID,UUID,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,INTEGER,JSONB,TEXT
) TO service_role;

-- Stage 8's original ordinary incremental writer does not supply the new
-- recovery_epoch column. Bind those future events to the checkpoint's CURRENT
-- epoch in a BEFORE INSERT trigger; old events retain their original epoch.
CREATE FUNCTION public.lola_bind_replay_event_recovery_epoch()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path=pg_catalog,public
AS $body$
DECLARE v_epoch BIGINT;
BEGIN
  SELECT c.recovery_epoch INTO v_epoch
    FROM public.lola_provider_replay_checkpoints c
    WHERE c.company_id=NEW.company_id AND c.employee_id=NEW.employee_id
      AND c.provider=NEW.provider
      AND c.provider_identity_sha256=NEW.provider_identity_sha256
      AND c.resource_sha256=NEW.resource_sha256
      AND c.replay_digest=NEW.replay_digest
    FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Provider event has no current replay epoch';
  END IF;
  IF NEW.recovery_epoch=0 AND v_epoch>0 THEN
    NEW.recovery_epoch:=v_epoch;
  END IF;
  IF NEW.recovery_epoch IS DISTINCT FROM v_epoch THEN
    RAISE EXCEPTION 'Provider event epoch mismatch';
  END IF;
  RETURN NEW;
END;
$body$;
REVOKE ALL ON FUNCTION public.lola_bind_replay_event_recovery_epoch()
  FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.lola_bind_replay_event_recovery_epoch()
  TO service_role;
CREATE TRIGGER lola_provider_replay_event_epoch_gate
  BEFORE INSERT ON public.lola_provider_replay_events
  FOR EACH ROW EXECUTE FUNCTION public.lola_bind_replay_event_recovery_epoch();

COMMIT;

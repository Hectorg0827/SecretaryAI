-- Disposable Postgres only: exactly one racing worker may advance version 2.
\set ON_ERROR_STOP on
DO $assert$
DECLARE
  v_version integer;
  v_count integer;
  v_race_count integer;
  v_digest text;
BEGIN
  SELECT version,cursor_digest INTO v_version,v_digest
    FROM public.lola_delta_checkpoints;
  SELECT count(*) INTO v_count FROM public.lola_delta_event_refs;
  SELECT count(*) INTO v_race_count FROM public.lola_delta_event_refs
    WHERE event_ref IN (repeat('4',64),repeat('5',64));
  IF v_version<>3 OR v_count<>4 OR v_race_count<>1
     OR v_digest NOT IN (repeat('e',64),repeat('f',64)) THEN
    RAISE EXCEPTION 'Race must produce exactly one CAS winner and one atomic event';
  END IF;
END
$assert$;

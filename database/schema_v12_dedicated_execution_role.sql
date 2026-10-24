-- SecretaryAI V12: narrow execution database principal.
-- Deliberately NOLOGIN. Staging operator must provision a dedicated credential
-- out of band; this migration creates no password and no production access.
DO $role$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='secretary_executor') THEN
    CREATE ROLE secretary_executor NOLOGIN NOINHERIT;
  END IF;
END
$role$;
REVOKE ALL ON SCHEMA secretary_internal FROM secretary_executor;
GRANT USAGE ON SCHEMA secretary_internal TO secretary_executor;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM secretary_executor;
GRANT EXECUTE ON FUNCTION
  secretary_internal.enqueue_agent_execution(UUID,UUID,TEXT),
  secretary_internal.claim_agent_execution(UUID,UUID,TEXT),
  secretary_internal.finish_agent_execution(UUID,UUID,UUID,TEXT,TEXT,TEXT)
  TO secretary_executor;

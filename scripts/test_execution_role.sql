-- V12: dedicated execution account is NOLOGIN by default and limited.
\set ON_ERROR_STOP on
BEGIN;
DO $rolecheck$
DECLARE
  can_login BOOLEAN;
BEGIN
 SELECT rolcanlogin INTO can_login FROM pg_roles
   WHERE rolname='secretary_executor';
 IF can_login IS DISTINCT FROM FALSE THEN
    RAISE EXCEPTION 'execution role must not have a login by default';
 END IF;
 IF NOT has_schema_privilege('secretary_executor','secretary_internal','USAGE')
    OR NOT has_function_privilege('secretary_executor',
      'secretary_internal.claim_agent_execution(uuid,uuid,text)','EXECUTE')
    OR NOT has_function_privilege('secretary_executor',
      'secretary_internal.finish_agent_execution(uuid,uuid,uuid,text,text,text)',
      'EXECUTE') THEN
   RAISE EXCEPTION 'restricted executor lacks private RPC access';
 END IF;
 IF has_table_privilege('secretary_executor','agent_executions','INSERT')
    OR has_table_privilege('secretary_executor','agent_executions','UPDATE')
    OR has_table_privilege('secretary_executor','drafts','UPDATE')
    OR has_schema_privilege('service_role','secretary_internal','USAGE')
    OR has_function_privilege('authenticated',
      'secretary_internal.claim_agent_execution(uuid,uuid,text)','EXECUTE')
    THEN
   RAISE EXCEPTION 'excessive execution principal privileges';
 END IF;
END
$rolecheck$;
ROLLBACK;

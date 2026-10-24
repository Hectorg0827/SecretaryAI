-- Read-only catalog evidence. Run with psql -X -A -t -v ON_ERROR_STOP=1.
-- Contains schema metadata only: no tenant rows, credentials or role passwords.
WITH ns AS (
  SELECT oid, nspname FROM pg_namespace
  WHERE nspname IN ('public', 'secretary_internal')
), objects AS (
  SELECT 'table' AS kind, n.nspname || '.' || c.relname AS identity,
    jsonb_build_object('rls', c.relrowsecurity, 'force_rls', c.relforcerowsecurity) AS definition
  FROM pg_class c JOIN ns n ON n.oid=c.relnamespace WHERE c.relkind='r'
  UNION ALL
  SELECT 'column', n.nspname || '.' || c.relname || '.' || a.attname,
    jsonb_build_object('type', format_type(a.atttypid,a.atttypmod),
      'not_null', a.attnotnull, 'default', pg_get_expr(d.adbin,d.adrelid),
      'generated', a.attgenerated, 'identity', a.attidentity)
  FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN ns n ON n.oid=c.relnamespace
  LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
  WHERE c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
  UNION ALL
  SELECT 'constraint', n.nspname || '.' || c.relname || '.' || k.conname,
    to_jsonb(pg_get_constraintdef(k.oid))
  FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN ns n ON n.oid=c.relnamespace
  UNION ALL
  SELECT 'index', n.nspname || '.' || c.relname, to_jsonb(pg_get_indexdef(c.oid))
  FROM pg_class c JOIN ns n ON n.oid=c.relnamespace WHERE c.relkind='i'
  UNION ALL
  SELECT 'policy', schemaname || '.' || tablename || '.' || policyname,
    jsonb_build_object('permissive',permissive,'roles',roles,'command',cmd,'using',qual,'check',with_check)
  FROM pg_policies WHERE schemaname IN ('public','secretary_internal')
  UNION ALL
  SELECT 'trigger', n.nspname || '.' || c.relname || '.' || t.tgname,
    to_jsonb(pg_get_triggerdef(t.oid))
  FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN ns n ON n.oid=c.relnamespace
  WHERE NOT t.tgisinternal
  UNION ALL
  SELECT 'function', n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
    jsonb_build_object('source',p.prosrc,'language',l.lanname,'security_definer',p.prosecdef,
      'config',p.proconfig,'result',pg_get_function_result(p.oid),'volatility',p.provolatile,
      'strict',p.proisstrict)
  FROM pg_proc p JOIN ns n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
  WHERE p.prokind='f' AND NOT EXISTS (
    SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')
)
SELECT jsonb_build_object('format',1,'objects',jsonb_agg(
  jsonb_build_object('kind',kind,'identity',identity,'definition',definition)
  ORDER BY kind,identity)) FROM objects;

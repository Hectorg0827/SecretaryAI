-- Two independent psql clients invoke this SQL simultaneously.
-- Use only on disposable PostgreSQL fixture after Stage 10 acceptance.
-- CLI variables event, cursor_cipher and cursor_digest are single test chars.
\set ON_ERROR_STOP on
SET ROLE service_role;
WITH mapping AS (
  SELECT encode(extensions.digest(
    convert_to(concat_ws(E'\n',
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      'verified-case-1','link-source-rev-1',
      'email:auth-email-1','whatsapp_business:auth-wa-1'
    ),'UTF8'),'sha256'),'hex') AS revision
), barrier AS MATERIALIZED (
  SELECT pg_sleep(1)
)
SELECT (
  public.lola_delta_checkpoint_commit(
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid,
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'::uuid,
    'outlook',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    'employee-verified-outlook','mailbox-1','inbox',
    (SELECT revision FROM mapping),
    2,repeat('d',64),
    repeat(:'cursor_cipher',64),repeat(:'cursor_digest',64),
    repeat('f',64),
    jsonb_build_array(jsonb_build_object(
      'event_ref',repeat(:'event',64),
      'kind','updated_or_added',
      'thread_ref',NULL
    )),
    clock_timestamp()-interval '10 seconds'
  )->>'committed'
)
FROM barrier;

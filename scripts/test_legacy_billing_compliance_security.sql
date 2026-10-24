-- Executed in disposable PostgreSQL CI after the hardened billing/compliance SQL.
DO $billing_compliance$
DECLARE
    t text;
    all_tables text[] := ARRAY[
        'subscription_plans', 'company_subscriptions', 'billing_events',
        'compliance_products', 'compliance_federal_permits', 'compliance_colas',
        'compliance_licenses', 'compliance_brand_registrations',
        'compliance_distributors', 'compliance_check_log'
    ];
    readable text[] := ARRAY[
        'compliance_products', 'compliance_federal_permits', 'compliance_colas',
        'compliance_licenses', 'compliance_brand_registrations',
        'compliance_distributors'
    ];
BEGIN
    FOREACH t IN ARRAY all_tables LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            RAISE EXCEPTION 'Missing sensitive module table: %', t;
        END IF;
        IF NOT (SELECT relrowsecurity FROM pg_class
                WHERE oid=to_regclass('public.' || t)) THEN
            RAISE EXCEPTION 'RLS missing on sensitive module table: %', t;
        END IF;
        IF has_table_privilege('anon','public.' || t,'SELECT')
           OR has_table_privilege('anon','public.' || t,'INSERT')
           OR has_table_privilege('authenticated','public.' || t,'INSERT')
           OR has_table_privilege('authenticated','public.' || t,'UPDATE')
           OR has_table_privilege('authenticated','public.' || t,'DELETE') THEN
            RAISE EXCEPTION 'Unauthorized client privileges on: %', t;
        END IF;
        IF NOT has_table_privilege('service_role','public.' || t,'INSERT')
           OR NOT has_table_privilege('service_role','public.' || t,'SELECT') THEN
            RAISE EXCEPTION 'Backend grants missing on %', t;
        END IF;
    END LOOP;
    FOREACH t IN ARRAY readable LOOP
        IF NOT has_table_privilege('authenticated','public.' || t,'SELECT')
           OR NOT EXISTS (
                SELECT 1 FROM pg_policy
                WHERE polrelid=to_regclass('public.' || t)
                  AND polcmd='r' AND polqual IS NOT NULL
           ) THEN
            RAISE EXCEPTION 'Tenant read-only policy missing on %', t;
        END IF;
    END LOOP;
    FOREACH t IN ARRAY ARRAY['subscription_plans','company_subscriptions',
                             'billing_events','compliance_check_log'] LOOP
        IF has_table_privilege('authenticated','public.' || t,'SELECT') THEN
            RAISE EXCEPTION 'Sensitive ledgers must be backend-only: %', t;
        END IF;
    END LOOP;
END $billing_compliance$;

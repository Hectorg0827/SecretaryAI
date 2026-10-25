# SecretaryAI-Staging setup evidence — October 24, 2026

Status: **setup incomplete; deployment blocked**. This evidence is confined to the
existing isolated staging projects and existing PR #74. No merge, production
deployment, Lyo mutation, customer communication, purchase, or financial action
was performed.

## Verified scope

| Resource | Identifier |
| --- | --- |
| Railway project | `77513c77-44df-485e-a927-d57d6a80b2c1` / SecretaryAI-Staging |
| Railway environment | `f9dc2025-cd5b-4df2-95bc-c3c54a3c3e36` / staging |
| Backend service | `ef05db6e-da57-4e68-a879-7906cd2c0522` |
| Redis service | `f8818b13-7c4f-442c-b19b-b6d9be7ab138` |
| Supabase | `xerktvdgjvjozvckjyfp` / SecretaryAI-Staging / ACTIVE_HEALTHY |
| Anthropic workspace | `wrkspc_01A2ZcW6vYqEGFr5rxmsH2Sp` / SecretaryAI-Staging |
| Existing PR | [SecretaryAI #74](https://github.com/Hectorg0827/SecretaryAI/pull/74) |

Railway remains pinned to `3ec14f78d1f2db16f2a9fbc9b38fe9cd0de76833`.
The configured source pin is reported by Railway, but manual deployments built
`77eab2bc94cc4d9274b4586182266cf25a8d8eb5`. Do not infer deployed source from
the source configuration alone. Inspect each deployment commit.

## Completed actions and checks

1. Inspected the existing staging resources and pinned PR source.
2. Set 14 safety/configuration variables with Railway `skipDeploys=true`:
   `DEBUG=false`, `BILLING_ENABLED=true`, `COMPUTER_USE_ENABLED=false`,
   `INTUIT_ENVIRONMENT=sandbox`, `SUPABASE_URL` and expected project ref pointing
   only at staging, deployment environment `staging`, email delivery and worker
   modes `disabled`, live-email approval and staging-email pilot `false`, an empty
   staging recipient, durable approval `false`, and test-database-only `1`.
   Railway acknowledged the exact names and confirmed deployment was skipped.
   OAuth does not return stored variable values, so a live process check remains
   required. Billing was not bypassed to hide missing credentials.
3. Ran the V1–V5 baseline verifier directly on isolated staging: passed.
   A separate catalog count returned **60 public tables, 60 with RLS**.
4. Retrieved **28** recorded migration bodies. Every body is byte-for-byte equal
   to at least one PR source file. [migration-map.json](migration-map.json) records
   the remote version, source matches and SHA-256 for each, plus the five
   untracked baseline files. Earlier commentary counted 27; the final inventory
   confirms 28.
5. Added read-only catalog export to ordinary PR CI and used PostgreSQL 17 to
   match the staging major version. Rebuilt the complete intended schema in the
   disposable CI database. Compared its **1,283** catalog objects with staging:
   **zero missing, zero extra, zero changed**. This covers tables/RLS flags,
   columns/defaults/types, constraints, indexes, policies, triggers and function
   bodies/security configuration. [schema-comparison.json](schema-comparison.json).
6. Ran five additional repository permission/structure checks against staging
   inside explicit READ ONLY transactions: legacy core; billing/compliance;
   internal queues; Phase1 control plane; procurement/briefings. All passed.
   The separate stock-posting baseline check confirmed anon, authenticated and
   service_role cannot directly execute stock posting.
7. Verified the internal staging email allowlist contains **0 recipients**.
8. Inspected Supabase security advisors: no ERROR/WARN entries; 18 INFO notices
   describe RLS tables with no client policies. These backend-only tables remain
   denied to ordinary client roles. No permissive policies were added to silence
   the notices.
9. Re-ran the full backend suite locally with a real Redis instance:
   **1,351 passed, 5 skipped**. Initial local runs lacked a working test Redis
   process and failed closed; after correcting the local harness, the entire
   suite passed without changing application code or suppressing tests.
10. Verified the original pinned commit's CI/CodeQL and the schema-evidence
    commit's CI/CodeQL all completed successfully. Latest code evidence:
    [CI 40119875504](https://github.com/Hectorg0827/SecretaryAI/actions/runs/40119875504),
    [CodeQL 40119875543](https://github.com/Hectorg0827/SecretaryAI/actions/runs/40119875543).
    [Disposable schema artifact](https://github.com/Hectorg0827/SecretaryAI/actions/runs/40119875504/artifacts/12570735698),
    archive SHA-256 `90a5891ef3564b0bbf149658d244575d833b91f41d2ffc5882c41610f1ba6d8e`.
11. Redis now has a successful deployment
    `25aa809b-d9aa-4193-ba51-5df729ac32d2`, with AOF enabled and
    `appendfsync always` on the existing persistent `/data` volume. Runtime logs
    confirm creation of AOF base/incremental files and readiness. It has no public
    domain or TCP proxy. No controlled restart/revocation persistence probe has
    run; this is configuration/startup evidence only.
12. Completed Anthropic/Google login and phone verification. Created a dedicated
    SecretaryAI-Staging workspace with **zero keys** and set its monthly usage
    cap to **$1.00**, verified in the UI. Existing organization auto-reload is off;
    no credits were purchased and no payment settings were changed.
13. The user created a Stripe sandbox/webhook and saved `STRIPE_SECRET_KEY` and
    `STRIPE_WEBHOOK_SECRET` in Railway. The connector confirms both names are
    present, but withholds values. Their test mode, validity, matching webhook
    secret, selected events and delivered signatures are not independently
    verified. No agent-created payment or customer communication occurred.
14. Verified staging domain `secretaryai-backend-staging.up.railway.app` targets
    port 8080. Source mounts the webhook at `/api/billing/webhook`.
15. CI `40121486228` and CodeQL `40121486219` for the report commit
    `77eab2bc94cc4d9274b4586182266cf25a8d8eb5` both completed successfully.
16. Rechecked staging read-only: **28 history entries, 60 public tables, 60 RLS,
    zero email allowlist recipients**. V1–V16 source parity still passes.
17. Found and fixed old-shape Stripe webhook assumptions in PR source:
    subscription periods now support Acacia top-level fields and newer single-item
    periods; invoice subscription IDs support the newer discriminated parent.
    Mixed-item periods are rejected instead of guessed. Staging rejects signed
    events unless `livemode` is explicitly false. Processing/storage errors return
    HTTP 500 for retry, and only SQLSTATE 23505 duplicate inserts are ignored.
    Checkout subscription lookup failures also retry instead of activating with
    unknown periods. No dependency or database schema changes.
18. Added signed synthetic webhook regression tests. Targeted billing webhook,
    staging isolation and paywall suites: **79 passed**. Initial local run had
    25 failures from the host's unsupported `socks5h` proxy setting during app
    import; rerunning these mocked/local tests without proxy environment settings
    passed. This does not certify all Endive event fields or live webhook delivery.

## Existing failure diagnosed

The user's latest manual backend deployment
`b0e5405a-0e24-4f4d-b4be-a3f907bdc51d` is **FAILED**. Runtime logs show two
required Settings fields missing: `database_url` and `anthropic_api_key`.
Railway's variable inventory independently confirms both are absent. Both Stripe
variables are now present. No deployment was initiated by the agent.

The build also reported an unavailable optional Playwright Chromium installation
on Debian trixie. Browser automation remains disabled; this is not browser or
computer-use certification.

## Remaining gates — do not treat as complete

- Obtain/verify a real isolated Supabase PostgreSQL connection credential and
  validate TLS connectivity. The existing service-role variable is present,
  but its value and project binding have not been independently verified here.
- Five baseline history entries are absent. The 28 later remote versions also
  differ from repository filenames. Physical schema equivalence is now proven,
  but **migration-history reconciliation and a future-push dry run have not been
  performed**. Do not replay CREATE migrations or fabricate execution records.
  Choose one canonical migration root and use reviewed, explicit version mappings.
- Create a workspace-scoped, expiring Anthropic staging key and transfer it
  directly to Railway; browser security requires action-time confirmation before
  creating this persistent credential. No key exists yet. Validate only synthetic
  prompts within the staging cap.
- Independently verify the Stripe sandbox key and existing webhook destination,
  API version, event selection and signing secret. Validate hosted signature
  rejection and idempotent event processing. Local synthetic tests passed; no
  live charges or real customer events are authorized.
- The public domain exists but the backend is unhealthy. Existing Stripe target
  should be `https://secretaryai-backend-staging.up.railway.app/api/billing/webhook`.
  Do not claim delivery verification until the backend starts and a sandbox
  test event is received successfully.
- No private Storage bucket exists and no live ClamAV scan was validated.
  Evidence-file integrations remain unverified and must fail closed.
- Before any backend deployment: confirm credentials, schema/history, frozen
  side effects, current CI/CodeQL and Redis durability configuration. Then run
  live `/health`, authentication/tenant/paywall, synthetic-provider and webhook
  checks. Unit/CI success does not certify the hosted backend.

No secrets are included in this report or its JSON evidence.

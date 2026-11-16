# Lion BI — Logistics erasure-safe additive migration and disposable-clone gate
Date: 2026-11-16 | State: **NO-GO** | Scope: **staging review only**

## Confirmed read-only staging state
Project: `SecretaryAI-Staging` (`xerktvdgjvjozvckjyfp`); production and LYO excluded.

- `public.logistics_events`: RLS enabled; `service_role` can SELECT, INSERT and **DELETE**; cannot UPDATE/TRUNCATE. `authenticated` has no direct DELETE.
- Installed `trg_logistics_events_append_only`: blocks UPDATE, not DELETE.
- `logistics_events_company_id_fkey`: references `public.companies` with `ON DELETE CASCADE` (`confdeltype='c'`).
- Staging migration history has **45** recorded versions (last `20261114163243`); neither the Logistics hardening candidate nor the seven ordered Compliance Phase 1 migrations is recorded.
- Six expected new Compliance Phase 1 tables remain absent. The existing two pricebook migrations are recorded and **must not** be reapplied.

## Intended retention contract — technical assumption, human policy signoff still required
During the active company lifecycle, Logistics events are append-only for regular API/backend clients. No routine direct DELETE/UPDATE/TRUNCATE through `service_role`, `anon`, `authenticated`, or `PUBLIC`. Preserve SELECT and INSERT for `service_role`, and preserve the company-owned FK cascade for an independently approved, audited legal erasure workflow. Verify applicable statutory and commercial retention periods, legal holds, record subject scope, and whether other evidence/backup systems also require erasure. **No fixed retention duration is asserted here.** Do not equate a successful SQL cascade with approval to erase a company.

## Candidate and ancestry
This branch is based on the reviewed draft PR #194, which contains a **diagnostic only** synthetic blanket-delete guard fixture. That blanket BEFORE DELETE trigger **must not be promoted**: it is incompatible with existing company-erasure cascades.

Cherry-copied verbatim from draft PR #148, existing additive candidate:
`backend/supabase/migrations/20261114230000_logistics_events_service_delete_revoke.sql`. It revokes direct DELETE/TRUNCATE/UPDATE from API roles and PUBLIC without adding a trigger, replacing any need for the incompatible review-only proposal. The file is a **candidate** under `backend/supabase/migrations`, not an approved staging CLI bundle. It was not applied to any remote Supabase project.

Also includes PR #148's existing event-store fail-closed fallback fix and its tests. `scripts/validate_schema.sh` now runs the existing review-only diagnostic fixture separately from the erasure-safe candidate against disposable CI PostgreSQL. CI proves only a **synthetic** database shape; it **does not** prove compatibility with a fresh schema clone of the connected staging project.

## Disposable clone acceptance — blocked pending billable branch confirmation
- Supabase quoted **$0.01344 per hour** for a disposable branch, plus potentially applicable usage charges. The cost has **not** been approved; no branch was created.
- A genuine staging clone must come from the current 45-version schema/history and be confirmed against a fresh read-only catalog snapshot before SQL tests. A newly created Supabase development branch can reproduce migrations but may not preserve manually introduced schema drift; **verify parity before trusting it as an exact clone**.
- On that disposable database only: test baseline grants/FK/UPDATE guard; apply this additive candidate in a transaction or isolated preview; verify no direct service DELETE/UPDATE/TRUNCATE, service SELECT/INSERT intact, authenticated denied, cross-tenant isolation intact, and company-erasure cascade permitted only by the approved administrative path. Check idempotence and rollback; record catalog manifest/hash before and after.
- Run all **seven** pending Compliance migrations **in timestamp order** against a disposable clone, after resolving source-to-bundle parity and prerequisites. Test policy/RLS/grants, audit transaction atomicity and rollback, owner/manager/viewer/second-tenant access, denied filings/price publication, and negative/failure cases.
- Independently verify the recorded historical remote SQL of Logistics Journey (`20261111005641`), Lola Core (`20261111005840`) and Conversation Owner RLS (`20261112043411`) against currently installed objects, and trace company-ID JWT claim to non-user-editable, refreshed authorization state. The historical SQL Git-blob matches are **still unresolved**; do not rewrite recorded history to manufacture matches.
- Preserve already-recorded pricebook migrations `20261114162323` and `20261114163243`; never run `migration repair` or indiscriminate `db push`.
- Require separate legal/retention, security, database, compliance/finance, and authenticated staging role acceptance. All releases, migrations, PR merges, production changes, agency submissions and external accounting actions remain **NO-GO** until distinct explicit staging deployment authorization.

## What this branch establishes
A reviewable minimal hardening candidate with real CI synthetic tests, not a verified staging repair. There is no evidence that live staging's dangerous DELETE grant has been removed.

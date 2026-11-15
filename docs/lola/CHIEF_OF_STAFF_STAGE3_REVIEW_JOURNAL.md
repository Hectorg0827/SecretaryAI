# Lola Chief of Staff — Stage 3: durable manual review journal

Status: staging feature branch only; candidate migration file is committed but NOT
applied to any hosted Supabase project. No background monitoring, production
deployment, notification delivery, WhatsApp, QuickBooks or price-book writes.

## User experience

Lola Home (owner and manager roles) displays recorded logistics reviews with
two deliberate actions:

1. Record current exceptions: reads the tenant's source ledger and LogisticsMonitor
   alerts, then atomically inserts minimal evidence pointers into an internal table.
   Repeated scans with identical source revisions are deduplicated using company
   and event key with Postgres ON CONFLICT DO NOTHING. Acknowledgements persist.
2. Acknowledge observation: records current active employee and timestamp only
   if review is pending and source revision matches the version displayed.

No free-form messages, financial amounts, customer allocations or secrets are
stored. No external action occurs upon acknowledgement.

## API contract

- GET /api/lola/chief-of-staff/reviews: bounded tenant-filtered journal page.
- POST /api/lola/chief-of-staff/reviews/capture: manual source-derived scan, not
  browser-provided evidence; maximum 100 alerts, atomic database insert.
- POST /api/lola/chief-of-staff/reviews/{review_id}/acknowledge: revision-checked
  optimistic concurrency; acknowledgement is not action approval.

An empty journal means no recorded reviews, not a risk-free business.
Source outages and missing tables respond 503 rather than an empty list.

## Persistence and security

Candidate additive SQL migration:
supabase/migrations/20261115032000_lola_review_events_staging.sql

- Unique company_id + event_key; source revision is hashed into event identity.
- RLS enabled; no direct anon or authenticated access.
- Service-role privileges limited to SELECT, INSERT, UPDATE on this table.
- Every service-role query is explicitly company filtered.
- Current active employee role is revalidated on every request.
- No deletes, provider writes, scheduled scans or outbound notifications.

The SQL file has NOT been executed in a hosted project. A local Supabase CLI
was unavailable and the package registry could not be reached, so the new
migration needs independent migration-tool validation before application.

## Limitations

- Manual logistics-only capture, not a scheduler or cross-channel memory graph.
- Unscoped supplier analytics are excluded from persistence, with count reported.
- Changed source revisions create new pending reviews; resolved historical
  alerts are not automatically closed.
- Source revisions also incorporate the monitor's evaluation date, so a
  time-based SLA, demurrage or detention warning can require fresh review on
  the next day even if the logistics event ledger is unchanged.
- Every new ledger event on a shipment changes the revision even when the
  underlying exception text has not materially changed.
- Some alerts derive from missing verification or unverified reports, labeled
  accordingly; journal inclusion is not proof of independently validated facts.
- This is completely separate from durable action approvals and execution.

## Acceptance gates

1. Validate SQL on an isolated local/ephemeral database; review RLS/privileges.
2. Run full backend and frontend CI, browser tests and CodeQL.
3. Authenticate owner, manager, denied role, revoked user and second-tenant
   staff in a staging session.
4. Test duplicates, concurrent acknowledgements, stale revisions and outages.
5. Verify no 503 is transformed to all-clear, and no review is treated as
   permission to execute actions.
6. Obtain separate approval before staging deployment, never modify production.

Regression targets:
backend/tests/test_lola_review_journal.py
frontend/src/components/lola/LolaReviewJournal.test.tsx

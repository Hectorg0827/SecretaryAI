# Lola Chief of Staff — Stage 2 (Pricing Evidence, staging only)

## Implemented in code
- GET /api/lola/chief-of-staff/pricing/snapshots: tenant-filtered index of up to 20 recorded cost snapshots.
- GET /api/lola/chief-of-staff/pricing/snapshots/{snapshot_id}: read-only cost and price-book source audit.
- Server authorization reuses get_pricing_user: a current active owner, manager or pricing_billbacks employee; revalidates active role each request.
- Monetary fields are queried as exact numeric text and evaluated with Decimal.
- Cost component coverage includes inbound freight, customs and warehouse storage/receiving/handling/outbound/shrink/carrying.
- Rejects ambiguous or cross-tenant records, duplicate charge identifiers, source outages and oversized source collections.
- Lola Home provides a role-gated, inspect-only evidence panel. No price proposal sign-off button.
- The existing Stage 1 logistics executive briefing remains unchanged.

## Deliberate honesty / safety boundaries
- A source row marked 'reconciled' is a source assertion, NOT an independent QuickBooks bill or shipment reconciliation.
- A price book row marked 'approved' is NOT proof of successful publication to the actual accounting system or customer price list.
- A sum of recorded costs is NOT an audited landed cost, net revenue, gross margin or contribution margin.
- The source reads are separate and are not a single atomic snapshot.
- Incomplete or unavailable source rows are not silently interpreted as zero; error responses must never leak provider details.
- No database migrations, cron, webhooks, Supabase writes, QuickBooks writes, external email/WhatsApp messages, computer-use actions or proactive voice were introduced.

## Required acceptance before staging promotion
1. CI: backend pytest, frontend lint/typecheck/unit/build, container smoke and browser accessibility.
2. Confirm migration availability and accepted-case/warehouse charge coverage in the target staging Supabase project.
3. Authenticated tests with owner, pricing_billbacks and denied role, including stale-role revocation and second-tenant 404.
4. Independently verify a sample snapshot's invoice line mapping, allocation weights, freight invoice version and physical case units against the ledger.
5. Add verified accounting invoice and actual price-source adapters before enabling margin calculations or price-change proposals.
6. No production merge/deployment without separate approval.

## Test targets
- backend/tests/test_lola_pricing_bridge.py
- backend/tests/test_lola_pricing_api.py
- frontend/src/components/lola/LolaPricingEvidence.test.tsx

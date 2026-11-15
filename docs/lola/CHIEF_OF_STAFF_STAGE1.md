# Lola Chief of Staff — Stage 1

## Shipping behavior

- Authenticated read-only route: GET /api/lola/chief-of-staff/briefing.
- Source: canonical logistics event ledger, existing LogisticsMonitor and LogisticsViewer (role-sensitive redaction).
- Lola's home screen displays the highest-priority permitted logistics exceptions, with explicit coverage boundaries and links to source workspaces.
- Empty ledgers, source outages and unverified reports never become business all-clear messages.
- All financial exposure values are estimates, not QuickBooks reconciled amounts.
- Parallel evidence gate accepts injected *server-verified* Finance/Logistics/Portal evidence, with exact Decimal reconciliation, timeouts and fail-closed decisions.

## Excluded deliberately

Background workers, inbox webhooks, durable checkpoints, live QuickBooks cost reconciliation, approved price-book writes, email/WhatsApp sends, voice announcements, and LLM-controlled actions are NOT activated.

## Acceptance before broader rollout

1. Verify role and tenant isolation with authenticated staging accounts.
2. Verify the frontend build, backend imports, and regression suites in CI.
3. Add server-authorized evidence adapters with cross-source revision/identity/FX/case allocation checks.
4. Add durable event/approval tables, expiry, idempotency, server-side actor authorization, and provider reconciliation before any external write.
5. Gate computer use and spontaneous voice behind explicit permission, consent and audit.
6. Do not deploy to production as part of this change.

## Test commands

PYTHONPATH=backend pytest -q backend/tests/test_lola_chief_of_staff_gate.py backend/tests/test_lola_chief_of_staff_briefing.py

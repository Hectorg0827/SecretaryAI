# Lion BI Phase 1 — authenticated staging acceptance

**Status: LIVE_BUSINESS_ACCEPTANCE_PENDING.** CI, /health, or snapshots are not substitutes for owner-authenticated business-data tests.

## Approved scope
- Only `SecretaryAI-Staging`, backend `https://secretaryai-backend-staging.up.railway.app`, and the correct company.
- Obtain a fresh owner/manager session using the approved staging sign-in. Never forge or create JWTs directly.
- Keep the session token in a private operator environment variable `LIONBI_STAGING_BEARER_TOKEN`; never paste it into ChatGPT, a command-line argument, GitHub issue, PR, log, or screenshot.
- Default acceptance performs only read-only GET requests. The optional `--exercise-lola` submits prompts and persists chat conversations, but approves no actions and makes no vendor or compliance writes.

## Commands
From the checked-out repository's `backend` directory, with the approved private environment configured:

```bash
python scripts/phase1_authenticated_acceptance.py
python scripts/phase1_authenticated_acceptance.py --exercise-lola
```

The first command validates account list, detail and QuickBooks-backed order identities/statuses; stock status and source-coverage parity against inventory alerts; compliance setup and counts in all six registries. It is BLOCKED if the account/stock source is missing or the JWT/role is unavailable.

The second command adds three real, authenticated Lola questions and requires successful completed SSE replies that agree with the observed account classification and disclose missing inventory/compliance evidence. It never prints customer details, credentials, invoice contents or responses. **A human must still compare the original 30/90-day sales amounts against the current QuickBooks provider records and review the model's complete wording in the authorized application.**

## Required final signoff
- [ ] Confirm exact pinned commit of backend and web deployments, passing CI and CodeQL.
- [ ] Confirm non-root Chromium launches successfully in the actual new Railway backend (after the fix has been separately approved and deployed).
- [ ] Capture approved owner/manager API authentication; do not copy bearer token into the evidence file.
- [ ] Execute both checks with sanitized evidence and investigate all BLOCKED/mismatched results.
- [ ] Confirm representative account history/health and 30/90-day gross billed amounts against QuickBooks Online.
- [ ] Confirm inventory classifications, including zero stock and unobserved weekly sales velocity, against the Inventory UI.
- [ ] Confirm registry counts and compliance alerts; empty or incomplete configuration is **not** proof of regulatory clearance.
- [ ] Retain isolated CI evidence for cross-tenant denial, source failures, write failures and unauthorized access.
- [ ] No financial writes, production/LYO changes, synthetic records in the real company, or unapproved external messages.
- [ ] Independent reviewer documents acceptance before marking Phase 1 finished.

At inspection, staging Supabase had zero persisted account-health and compliance rows, although one company had a QBO connector configured. That is a limitation of database-only verification; it does not prove that QuickBooks has no customers, invoices or inventory.

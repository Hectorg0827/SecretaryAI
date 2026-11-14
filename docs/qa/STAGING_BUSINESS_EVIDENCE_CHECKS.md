# Lion BI — reproducible staging financial-evidence audit

**Status:** read-only staging QA; no certified prices or supplier invoices.

Run `backend/scripts/staging_business_integrity_readonly.sql` only in
**SecretaryAI-Staging** (project `xerktvdgjvjozvckjyfp`), with an authorized
read-only SQL editor/session. Do not run against production. The script contains
only SELECT/CTE statements and returns aggregate counts, not source identifiers,
employee identities, tokens or customer records.

## Audit interpretation

Every nonzero violation in the 11 integrity checks is a failure needing
authorized investigation. An all-zero result establishes only internal
database record consistency; it **does not** prove that a shipment arrived, a
supplier invoiced, or accounting was reconciled.

Zero evidence-review rows are an explicit missing-evidence blocker, not a
pass. Complete owner/manager, dedicated pricing and restricted staff session
checks using `phase1_authenticated_acceptance.py`,
`logistics_authenticated_acceptance.py`, and
`pricing_authenticated_acceptance.py`, with fresh tokens kept exclusively
in a private operator shell. A second-company session is needed to test
foreign-company assessment HTTP 404. Never put tokens into ChatGPT or CI.

For final signoff compare provider-native supplier invoice, BOL/freight,
customs/duty, warehouse WMS receiving/storage/handling, damage claims, and
QuickBooks **sandbox** Bill plus ledger entries. Require independent human
review before approving cost, margin, or selling price.

## Read-only snapshot (2026-11-14)

11 integrity checks: **0 violations**. Two POs total 2,400 ordered cases.
The approved/received PO has 1,200 received cases (1,180 accepted; 20
damaged; none quarantined); the other PO is unapproved/unreceived. One
staged receipt is correctly linked to the posted receipt, with separate
receiving and approving users.

One **provisional** USD cost assessment asserts **$12,000** supplier goods
cost; accepted-goods estimate **$11,800**, damaged exposure **$200**;
prior unclassified landed charges **$0**. These amounts are not independently
certified. There are **zero** purchasing evidence claims, integrity checks,
malware scans, review approvals, reconciled assessments, price-book entries,
cost snapshots/lines or logistics events. One company has stored QuickBooks
connection metadata, which does not prove provider reachability or parity.

**Release status:** BLOCKED for authenticated business acceptance, source
reconciliation and production. No price, inventory or accounting writes
are authorized by this checklist.

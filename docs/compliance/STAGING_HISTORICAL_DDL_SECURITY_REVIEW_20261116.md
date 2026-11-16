# Lion BI Staging — Three Historical Migration DDL Security Review

**Date:** 2026-11-16  
**Scope:** Read-only inspection of `SecretaryAI-Staging` (`xerktvdgjvjozvckjyfp`) migration history and PostgreSQL catalogs, with NO live writes.  
**Release disposition:** **NO-GO**.  
**Evidence:** `staging_historical_ddl_security_audit_20261116.json` and the earlier `staging_migration_snapshot_20261116.json`.  
**Not audited:** production, personal/customer records, business financial data, OAuth credentials, or actual regulator filings.

## Finding LBI-SEC-LOG-01 — service-role DELETE allowed on append-only logistics events

**Severity: High / release blocker.**

The recorded migration `20261111005641_logistics_journey` describes `public.logistics_events` as an *append-only ledger*, grants `service_role` `SELECT, INSERT, DELETE`, and creates only a `BEFORE UPDATE` trigger. The live staging catalog confirms:
- RLS is enabled and direct `anon`/`authenticated` SELECT/DELETE is not granted.
- `service_role` has SELECT, INSERT, **DELETE**; it does not have UPDATE.
- An UPDATE trigger is active, **but there is no active DELETE trigger**.
- `logistics_events_append_only()` is SECURITY INVOKER and has a pinned `search_path`, but its body does not handle DELETE.
- The backend store `backend/app/intelligence/wholesale_distribution/logistics/store.py` appends events and reads tenant-scoped events; it does not document a legitimate routine hard-delete operation.

**Consequence:** a privileged backend client using the `service_role` key can delete logistics history notwithstanding the ledger's append-only claim. Tenant scoping in normal application reads cannot substitute for database enforcement because `service_role` bypasses RLS. This finding does not imply that actual events have been deleted; **no event data was inspected**.

**Proposed fix, NOT applied:** `docs/compliance/sql-proposals/logistics_events_prevent_delete_REVIEW_ONLY.sql` revokes `DELETE` from `service_role` and adds a separate SECURITY INVOKER `BEFORE DELETE` rejection trigger, preserving the original UPDATE trigger. The SQL is **a review candidate, not an applied or approved migration**. `scripts/test_logistics_delete_guard_proposal.sql` exercises it against a rolled-back **synthetic** event ledger in disposable PostgreSQL, including table-owner deletion attempts and existing UPDATE protection.

**Human review required:** independently confirm retention/deletion obligations, backups and any authorized erasure mechanisms. Do not enforce a lifetime no-delete contract before that review. If approved, generate a separate canonical additive migration using the pinned Supabase CLI and retest on a disposable clone of live staging before requesting explicit staging-only deployment authorization. Do not edit the original recorded migration.

## Finding LBI-SEC-LOLA-02 — Lola action receipt delete controls observed

The recorded `20261111005840_lola_core` SQL is not available as an identical Git blob in the connected repository, but its statement was read from the remote migration history. Read-only catalog observations confirm:
- `lola_action_receipts`, `lola_delegations`, `lola_memories` and `lola_org_members` exist with RLS enabled, and client SELECT is not directly granted.
- `service_role` **does not have DELETE** on `lola_action_receipts`; its active `BEFORE UPDATE OR DELETE` guard rejects deletions.
- `guard_lola_action_receipt()` is SECURITY INVOKER with pinned `search_path`.
- Other lifecycle operations and all column/function definitions were **not comprehensively independently verified**. This is a **partial catalog check**, not full provenance clearance.

## Finding LBI-SEC-CHAT-03 — single owner-scope policy, JWT claim source open

The recorded `20261112043411_lola_conversation_owner_rls_staging` statement is also unmatched to a repository Git blob. Live read-only `pg_policies` returns **one** policy for `public.conversations`: `conversation_owner_scope`, `FOR ALL TO authenticated`. It uses both:
- `company_id = (auth.jwt()->>'company_id')::uuid`
- `user_id = auth.uid()`

Its `WITH CHECK` repeats both requirements. This provides owner-and-tenant predicates; however **JWT claim provenance/refresh is not established** by this audit. Confirm the `company_id` JWT claim is an authenticated, server-controlled entitlement, not user-editable metadata, and verify backend/company role revocation. A policy definition alone is not an authenticated cross-tenant test.

## Provenance and safe next steps

The three historical remote SQL statement Git blobs were not found in the current connected Git repository and must remain **source-unmatched**. Do not silently copy recorded SQL into a historical migration path and call that provenance established. Git blob matching and current schema footprints test different properties.

1. Review LBI-SEC-LOG-01 and retention requirements; approve or reject the *proposed* mitigation.
2. Preserve remote migration history; create only new additive migrations after formal approval.
3. Reconcile current staging schema/trigger/policy definitions with each recorded statement and verify the app's actual authorized operations.
4. Dry-run any approved changes on an isolated clone of the **current** staging schema. Verify grants, tenant isolation, rollback, idempotence, application compatibility and no loss of evidence.
5. Separately complete the seven unrecorded Compliance migration plan, human account/case pack/landed cost reconciliation and regulator source review.
6. Obtain **distinct explicit staging deployment approval** before any database change.

The proposed SQL and CI fixture **do not** connect to Supabase or mutate user data. **No staging or production deployment and no PR merge occurred.**

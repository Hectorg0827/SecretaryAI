# Lola Stage 9 — verified provider read + encrypted checkpoint adapter

**Development only — stacked draft. No application to hosted Supabase, no
Railway deployment, no live OAuth account read and no outbound messages.**

## Implemented

- `authorized_provider_reads.py`: private backend Gmail history and Microsoft
  Graph folder-delta transports. Gmail calls only `users.getProfile` and
  `users.history.list`, and requires the independently verified OAuth owner
  email to match. Graph calls `/me?$select=id` before a folder-delta page
  and pins every URL to the expected verified folder and Graph origin.
  HTTP redirects are not followed. Provider error response bodies and
  credentials are never logged or returned.
- Each provider page is gated before and after on the currently verified
  Stage 5 employee authorization and cross-channel case resolver. The same
  company, employee, provider connection and consent/mapping revision must
  remain valid. A backend service_role key alone is NOT employee authority.
- `replay_persistence.py`: AES-256-GCM encryption of Gmail history cursors
  and Graph delta URLs. Authenticated additional data (AAD) covers company,
  employee, provider, mailbox/folder identity and mapping revision. The
  root key comes only from backend secret `LOLA_PROVIDER_CURSOR_KEY_B64`
  (exactly 32 raw bytes, base64 encoded); absent keys fail closed.
- SHA-256-HMAC-derived scope indexes and event deduplication keys keep
  provider message IDs, mailbox IDs, provider identity and thread IDs
  out of persisted event metadata. A cursor digest is separately keyed.
- A bounded backend service-role Supabase RPC call to the **unapplied**
  Stage 8 `lola_commit_provider_replay` atomically writes replay metadata
  plus the encrypted cursor. After the RPC, the adapter re-reads only the
  same tenant/employee/provider/connection/resource checkpoint and verifies
  generation, cursor integrity, replay digest and persisted event keys.
- `email_sync_service.py` composes verified-case authorization, existing
  cursor read, provider replay, post-fetch reauthorization and atomic commit.
  Gmail without an existing authorized full-sync baseline returns
  `requires_full_gmail_bootstrap` and DOES NOT contact Gmail or write a
  cursor. Expired Gmail history returns `requires_full_gmail_resync` and
  does not advance the existing checkpoint. A first Graph delta round
  can commit a baseline-only snapshot; no past-reply claims result.
- All return types keep `reply_status=not_assessed` and
  `follow_up_eligible=false`, including successful internal DB commits.

## Synthetic tests — no real credentials or network

- `backend/tests/test_lola_authorized_provider_reads.py`
- `backend/tests/test_lola_replay_persistence.py`
- `backend/tests/test_lola_email_sync_service.py`

Tests cover Graph `/me` owner mismatch, wrong folder/cross-origin redirects,
Gmail getProfile identity mismatch, changed employee consent/mapping,
missing encryption key, tampered ciphertext, cross-employee AAD isolation,
expired provider cursors, CAS concurrency conflicts, missing event persistence,
atomic RPC failure and no false follow-up authorization.

## Explicit deployment blockers

1. Stage 5 source-of-truth registry tables and connection-authorizations
   are not yet created/verified in hosted staging; this remains dormant.
2. Stage 8 migration is GitHub-only. A database staging migration and SQL
   RLS/RPC concurrency acceptance require separate explicit approval.
   The mocks in this PR do not substitute for hosted Postgres verification.
3. The trusted employee-private connection loader must source Gmail
   credentials / Outlook token and independently verified OAuth principal
   from backend secrets/storage, NOT from API request or LLM input.
   No HTTP endpoint, scheduler, trigger or automatic job is registered.
4. OAuth token refresh durability and employee consent revoke races across
   the post-fetch check and SQL commit require independent authorization
   tests and stronger database-level coupling before enabling automation.
5. Gmail full-bootstrap plus reconciliation of concurrent changes needs a
   separate implementation. A Graph first delta round is baseline only;
   its per-folder enumeration and complete coverage are not established.
6. WhatsApp app echo, officially supported Meta integration, end-to-end
   conversation correlation and independently verified reply/no-reply status
   are still missing. A successful email metadata import is insufficient
   evidence for any reminder, send or business action.
7. Some existing connectors have write methods, but this stage NEVER invokes
   Gmail `send`, `drafts.create`, `messages.modify`, Graph send or mark-read.
8. The Stage 8 SQL's service-role security predicate must be tested against
   actual PostgREST request-role context; unauthorized user writes must be
   rejected. Do not assume mock validation proves this.

## Staging acceptance gates

- GitHub CI and CodeQL on the final Stage 9 head;
- independent local Postgres schema/RLS tests; then only after approval
  staged hosted Supabase deployment and authenticated employee-source tests;
- provider sandbox Gmail 404 and Graph 410 expiry, token-refresh,
  401/403, 429/5xx, redirect, tenant and employee isolation tests;
- explicit privacy review, encrypted cursor recovery/rotation strategy,
  browser/desktop regression and no outbound effects;
- no production or LYO changes.

## Protocol references

- Gmail sync: https://developers.google.com/workspace/gmail/api/guides/sync
- Gmail users.history.list: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
- Microsoft Graph message delta: https://learn.microsoft.com/en-us/graph/delta-query-messages
- Supabase Data API: https://supabase.com/docs/guides/api/securing-your-api

**This is a staging-code integration, not a claim of live employee
communications monitoring.**
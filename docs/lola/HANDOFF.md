# Lion BI / Lola — where we left off (2026-11-11)

Paste everything below the line into a new Claude Code session on the
`Hectorg0827/SecretaryAI` repository.

---

You are continuing work on Lion BI / Lola in `Hectorg0827/SecretaryAI`. I don't
code, so explain everything in plain, simple language and ask before anything
risky. Rules: never touch the LYO project; never perform live financial writes,
payments or external communications; never touch the production database
("secretaryai"); only merge or apply database changes when I explicitly say so.

**Read first:** `docs/lola/README.md`, `docs/lola/EXECUTION.md`, this file.

**Done and merged into `program/secretaryai-integration-20261026`:**
- PR #104 — all four Lola workstreams combined (core security, business
  intelligence, logistics, screens), email + QuickBooks PO executors (all
  switched off), Org & access screen, Spanish, attachments, PDF/Excel.
- PR #106 — scanned-document OCR (`LOLA_OCR=1`, off), two-person stock
  staging (`SECRETARYAI_LOLA_STOCK_STAGING_ENABLED=1`, off), supplier payment
  runs (approved list + CSV only; Lola never pays).
- PR #107 — fix so approval functions find `digest()` on Supabase (pgcrypto
  lives in the `extensions` schema). If #107 is NOT merged yet, check its CI
  and merge it once I confirm.

**Staging database "SecretaryAI-Staging" (Supabase project `xerktvdgjvjozvckjyfp`):**
- Applied and verified: `logistics_journey` (#1) and `lola_core` (#2).
- STILL TO APPLY, in this order, after #107 is merged (I already approved it;
  I must click "allow" on a computer when the Supabase tool asks):
  1. `supabase/migrations/20261109130000_lola_purchase_order_execution.sql`
  2. `supabase/migrations/20261111090000_approval_digest_search_path.sql`
  Then verify read-only: `agent_execution_po_payloads` exists with no browser
  access, and both `secretary_internal.approve_and_queue_email` and
  `approve_and_queue_purchase_order` have
  `search_path=pg_catalog, extensions, public, pg_temp` and
  `to_regprocedure('digest(bytea,text)')` resolves under it.
- Note: before #107, approving emails on staging was already broken (same
  `digest()` problem); the second migration fixes that too.

**Next steps after the database (do them one at a time, with me):**
1. Try Lola on staging logged in as owner, manager and sales rep; each must
   only see what their role allows.
2. Set up the org chart in Settings → Team → Org & access.
3. Turn features on one by one, safest first: OCR → stock staging → email
   pilot to one test mailbox → QuickBooks POs in the Intuit sandbox. Each
   switch is described in `docs/lola/EXECUTION.md` and `backend/.env.example`.

**Useful facts:** backend tests `cd backend && python -m pytest -q` (needs
Redis running); frontend `cd frontend && npx vitest run`; database checks
`scripts/validate_schema.sh` against a throwaway PostgreSQL. Cursor Bugbot
doesn't run (Cursor usage limit) — that's not a code problem.

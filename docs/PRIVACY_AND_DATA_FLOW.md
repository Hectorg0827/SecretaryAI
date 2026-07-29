# Privacy & Data Flow

How SecretaryAI collects, stores, moves, and deletes data. Pairs with
`THREAT_MODEL.md`.

## Data categories

| Category | Examples | Store | Sensitivity |
|---|---|---|---|
| Identity / auth | email, password hash (bcrypt), 2FA secret, JWTs | Supabase (`users`), Redis (revocation, OAuth state) | High |
| Accounting | invoices, balances, customers, vendors | QuickBooks (source), Supabase cache, desktop local cache | High |
| Inventory / pricing | items, quantities, prices | Supabase, desktop cache | Medium |
| Email | Gmail content, drafts, priority triage | processed transiently; drafts in Supabase | High |
| Integration tokens | QBO/Google/Conductor tokens | Supabase, **encrypted at rest** (`utils/encryption`) | Critical |
| AI | prompts, model responses, computer-use screenshots | transient; screenshots not retained by default | High |
| Telemetry | errors, traces | Sentry — **scrubbed** of headers/cookies/body/PII | Medium |
| Audit | proposed/approved/executed actions | Supabase (`audit_events`) | Medium |

## Data flow (login → action)

1. **Login** → `/auth/login` → JWT (bcrypt-verified). Desktop stores the token
   in the OS keychain/Credential Manager; web uses browser storage. Refresh is
   bounded + single-use rotation; revocation via Redis JTI blacklist.
2. **Reads** → API derives `company_id` from the verified JWT (never the client)
   and queries Supabase (service-role key → RLS bypassed, so every query is
   `company_id`-scoped in code) or the per-company adapter (QuickBooks).
3. **AI chat** → business data is summarized and passed to Claude wrapped in
   `BEGIN/END_UNTRUSTED` markers; the chat model cannot execute actions.
4. **Actions** → proposed → **draft/approval queue** → human approval →
   PolicyEngine gate → execute (email/PO) → audit event. Idempotent.
5. **Integrations** → OAuth tokens encrypted before storage; refreshed server-side.

## Cross-border / third parties
Anthropic (AI), Supabase (DB/auth), Intuit (QuickBooks), Google (Gmail),
SendGrid (email), Sentry (errors), Conductor (QB Desktop). Data shared is the
minimum needed per feature.

## Retention & deletion
- Sessions: JWT ≤ `access_token_expire_minutes`; revocations auto-expire in Redis.
- OAuth state: 10-min TTL.
- Screenshots: not retained unless a feature requires it (then encrypted +
  time-bounded); redaction fail-closed option available.
- **Account deletion / data export:** on request, purge `users`, company rows,
  cached business data, tokens, and audit (subject to legal retention). *Owner
  action:* wire the deletion endpoint to the vendor deletion APIs before GA.

## User controls
- Disconnect/revoke each integration (QBO/Google) independently.
- Screen capture is off by default and requires explicit consent per session.
- Sign out revokes the current token immediately.

> Gaps tracked in `PRODUCTION_READINESS.md`: local desktop cache still plaintext
> (#7); formal data-export/deletion endpoint to be finalized.

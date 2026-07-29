# SecretaryAI — Threat Model

Scope: the web app, desktop app (Tauri), FastAPI backend, background workers,
and the QuickBooks / Gmail / SendGrid integrations. This is a living document;
mitigations link to the defect register in `PRODUCTION_READINESS.md`.

## Assets
- Accounting records, customer/vendor data, inventory, pricing.
- Email content and Gmail/Google OAuth tokens.
- QuickBooks (Online + Desktop/Conductor) tokens and company data.
- User credentials, JWTs/refresh material, 2FA secrets.
- AI prompts and any captured screenshots (computer-use).
- Signing keys and CI secrets.

## Trust boundaries
1. Browser/desktop webview ↔ backend API (authenticated HTTPS).
2. Desktop webview ↔ native Rust (Tauri IPC) — least-privilege capabilities.
3. Backend ↔ Supabase/Postgres, Redis, third-party APIs (service credentials).
4. CI/CD ↔ release artifacts and secrets.
5. Tenant A ↔ Tenant B (multi-tenant isolation, same DB).

## Abuse cases & mitigations

| Threat | Mitigation | Status |
|---|---|---|
| Stolen/replayed JWT | Server-side JTI revocation; refresh is bounded + single-use rotation | ✅ (#19) |
| Cross-tenant data access (IDOR) | `company_id` predicate on every tenant-scoped query; negative tests | ✅ (#27/#28) — sweep ongoing |
| Client-supplied tenant id | `company_id` only ever read from the verified JWT server-side | ✅ (audit) |
| Service-role key bypasses RLS | App-layer `company_id` filtering; wrapper recommended | 🟡 |
| Secrets in logs/telemetry | Sentry scrub (headers/cookies/body/PII); `send_default_pii=False` | ✅ (#22) |
| Info leak via `/health`, `/openapi.json` | Generic health; openapi gated in prod | ✅ (#20/#21) |
| Remote content reaching native commands | Native-bundled UI (no remote iframe); least-privilege capabilities; no devtools in release | 🟡 (#4/#8/#9 — migration in progress) |
| Local cache readable by another user/malware | Encrypt at rest (SQLCipher) or stop caching | ⬜ (#7) |
| Desktop secret theft from `localStorage` | Move token to OS keychain/Credential Manager | 🟡 (#5, ADR 0001) |
| Prompt injection via email/attachment/QB memo | Treat model output + retrieved content as untrusted; typed tool schemas; human approval for sensitive actions | ⬜ (audit pending) |
| Unauthorized AI action (payment/email/PO) | Explicit user approval + exact preview; audit events; idempotency | 🟡 (approval queue exists; harden) |
| Malicious/compromised update | Signed updater, fail-closed | 🔒 blocked on signing key |
| OAuth state/PKCE/callback abuse | Redis-backed state with TTL; exact redirect; PKCE where supported | 🟡 (review) |
| Webhook spoof/replay | Signature + timestamp + idempotency | ⬜ (verify) |
| SSRF via URLs/imports/browser automation | Allowlist + no raw user-URL fetch | ⬜ (verify) |
| Supply-chain (deps/actions) | Committed lockfiles, Dependabot, CodeQL, dep-audit; pin actions | 🟡 (#16/#17/#23) |
| Lost/stolen laptop | OS keychain + at-rest encryption; short token lifetime | 🟡 |
| Screen-capture leakage | Default-off, explicit consent, indicator, no retention | ⬜ (#10) |

## Residual risks (current)
- Local desktop cache is still plaintext (#7).
- Desktop token still in `localStorage` pending the native-UI migration (#5).
- AI/tool-safety and webhook/SSRF reviews not yet completed this pass.
- Production builds are unsigned (external blocker: certificates).

Target posture: OWASP ASVS L2 for web/backend + this desktop threat model.

# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue.

- Email: **security@secretaryai.com** (or the repository owner directly).
- Include: affected component, version/commit, reproduction steps, and impact.
- We aim to acknowledge within 3 business days and to provide a remediation
  timeline after triage.

Please do not test against production tenants or other customers' data. Use a
dedicated test account.

## Supported versions

| Version | Supported |
|---|---|
| Latest released `1.x` | ✅ |
| Pre-release / older | ❌ |

## Handling of sensitive data

SecretaryAI processes accounting records, customer data, email, and OAuth
tokens. Controls in place (see `docs/THREAT_MODEL.md` for detail):

- Sessions are JWT-based with server-side revocation and bounded, single-use
  refresh rotation.
- Tenant isolation is enforced in application code on every query (the Supabase
  service-role key bypasses row-level security), with cross-tenant negative
  tests in `backend/tests/test_tenant_isolation.py`.
- The desktop app stores credentials in the OS keychain / Credential Manager
  (not `localStorage`) and runs the webview at least privilege.
- Error/telemetry (Sentry) is scrubbed of headers, cookies, request bodies,
  and PII; `/health` and `/openapi.json` do not leak internals in production.

## Known gaps (tracked)

See `docs/PRODUCTION_READINESS.md` for the live defect register and the
external blockers (code-signing certificates, notarization) that gate a signed
production release.

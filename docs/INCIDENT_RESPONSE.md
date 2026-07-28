# Incident Response Runbook

Minimal, actionable IR process for SecretaryAI. Keep calm; contain first.

## Severity

| Sev | Definition | Examples |
|---|---|---|
| SEV1 | Confirmed breach / cross-tenant data exposure / data loss | leaked customer data, tenant isolation break, ransomware |
| SEV2 | Security-relevant, no confirmed data loss | auth bypass in staging, exposed secret, exploited high CVE |
| SEV3 | Degraded / suspicious | outage, elevated errors, suspicious logins |

## Roles
- **Incident Lead** — coordinates, owns the timeline and decisions.
- **Comms** — internal + (if needed) customer/regulator notice.
- **Ops/Eng** — containment, forensics, fix.

## Flow (first 60 minutes)
1. **Declare** severity in the incident channel; assign roles; start a timeline
   doc (timestamped facts only).
2. **Contain**
   - Suspected token/secret compromise → rotate the secret (see below); revoke
     affected sessions (Redis JTI blacklist / force logout).
   - Cross-tenant/auth issue → disable the affected endpoint/feature flag.
   - Compromised integration → revoke the OAuth token; disconnect the connector.
   - Malicious update → unpublish the release; halt the updater.
3. **Assess** blast radius: which tenants/data, timeframe, source (logs, audit
   events, Sentry — all scrubbed of secrets).
4. **Eradicate & recover** — deploy the fix (previous known-good GHCR image if
   needed), restore data from backup if integrity is in doubt (`BACKUP_RESTORE.md`).
5. **Notify** — per contract/law for confirmed personal-data breaches; do not
   speculate publicly.
6. **Post-incident** — within 5 business days: root cause, timeline, action
   items with owners; add a regression test.

## Secret rotation targets
`SECRET_KEY` (invalidates all JWTs — forces global re-login), Supabase
service-role key, Anthropic/SendGrid keys, Intuit/Google OAuth client secrets,
signing certs/updater key. Never paste secret values into tickets or chat.

## Contacts / channels
- Security reports: `security@secretaryai.com` (see `SECURITY.md`).
- Vendor status/support: Supabase, Intuit, Google, SendGrid, Anthropic dashboards.

> Reference: tenant isolation + revocation controls are described in
> `THREAT_MODEL.md`; enable audit-log review (`audit_events`) during any SEV1/2.

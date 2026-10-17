# Phase 6 — /health alerting and on-call verification

**Status: NOT CONFIGURED / NOT TESTED. Exit criterion 15 remains open.**
The deployment's actual HTTPS API URL and on-call contact are prerequisites.

## Owner setup (monitoring provider with SMS/phone paging)
1. Finish `docs/DEPLOY_CHECKLIST.md`; verify your production API /health
   returns HTTP 200 and a JSON body with `"status":"ok"`.
2. Open your approved uptime-monitoring service (e.g., Better Stack or an
   equivalent service with real escalation). Create an **HTTP monitor**.
3. Enter the exact verified HTTPS `/health` URL. Do not use a placeholder
   or the currently non-resolving `api.secretaryai.com` host.
4. Require HTTP 200 and `"status":"ok"` in the JSON response. Set a
   one-minute polling interval, at least two monitoring regions, and alert
   after two consecutive failures. Adjust thresholds with the on-call owner.
5. Add a named human on-call responder with a tested phone/SMS notification,
   plus a backup responder. Configure an escalation when unacknowledged.
6. Create a second check for the public release-download route when assets
   are published; this does not replace /health.
7. In a controlled staging environment with the same monitor and on-call
   route, deliberately make /health return HTTP 503 (for example, by stopping
   staging Redis). Confirm the on-call human actually receives the page and
   acknowledges it.
8. Restore staging, confirm a recovery notification, and record the UTC
   failure time, alert arrival time, recipient, acknowledgment time and
   incident URL in the incident log. Then test the production monitor route
   by safely forcing an alert in the provider's test-incident interface.
9. Only when an actual human page is observed may criterion 15 be checked.

## Incident response
- **Critical**: /health not OK, Redis revocation unavailable, backend API
  unavailable. JWT validation fails closed on Redis outage by design.
- On-call: confirm 503 and the affected dependency without publishing
  credentials, notify the owner, inspect production service logs/Sentry,
  restore Redis/database or roll back the offending release, and retest
  sign-in and /health.
- Never disable fail-closed revocation to make the status appear green.
- A log message or Sentry event without an observed human page does NOT meet
  criterion 15.

## Required evidence (currently missing)
Real production host; configured monitoring check; on-call user and escalation;
an incident with delivered/acknowledged page; recovery confirmation.

# Native and cross-device acceptance — owner handoff

Workstream 05, 2026-10-26. No production use, installer signing, GitHub merge, deployment, live financial writes, vendor emails or customer data are authorized by this plan.

## Identity and provenance gate

Before any provider-backed test, the owner and Chat 1 must record **exact** SHA and environment identity for each of:

- Integration branch + Gate A contract revision
- Staged backend service (may track a distinct branch from the web service)
- Staged web service
- Android and iOS build artifact and code-signing identity, if available
- Desktop signed artifact and certificate identity, if available

No screenshots/logs may contain tokens, TOTP secrets, actual contact lists or third-party records. Use two dedicated **synthetic tenant** identities and a test-only mail allowlist; if either is missing, do not run the provider scenario.

## Acceptance matrix

All statuses are **NOT TESTED** until a tester records exact build, device/OS, time, account role, observed result, redacted artifact and any incident ID. A static JS bundle, mocked browser or unit test is not device acceptance.

| ID | Surface | Test / expected proof | Status |
| --- | --- | --- | --- |
| N01 | Android physical device | Install signed QA build; sign in with MFA; restart; verify no password persists in code field; securely sign out; prior session cannot access API | NOT TESTED |
| N02 | iOS physical device | Same as N01; VoiceOver reads every sign-in/MFA field and account settings control; dynamic text + keyboard navigation | NOT TESTED |
| N03 | Both mobile OSes | Member with viewer or sales_rep role opens Settings via Dashboard avatar; can sign out; restricted company-wide tabs cannot be opened by deep link and backend rejects requests | NOT TESTED |
| N04 | Android + iOS | Launch build with missing/insecure backend configuration in controlled test build; see fail-closed configuration screen; zero outbound auth/refresh traffic | NOT TESTED |
| N05 | Both mobile OSes | Authorization-status outage and approvals-read outage never become a zero-pending or successful execution claim | NOT TESTED |
| N06 | Windows/macOS desktop | Install signed QA package; complete 2FA via pre-auth token only; vault unavailable blocks sign-in; vault deletion failure prevents false sign-out | NOT TESTED |
| N07 | Windows/macOS desktop | QB installed detection creates no provider account or auth session; explicit Start click makes one request; deliberate Open launches provider HTTPS consent page; capture remains off until separate consent | NOT TESTED |
| N08 | Desktop + backend | Provider says authorization active but before first completed sync; UI labels sync unverified, with source timestamp/receipt once Chat 2 supplies contract | NOT TESTED |
| N09 | Desktop + backend | Cancel or fail provider connection, network outage for three status polls, screen-capture permission refusal; no silent success, no lasting capture | NOT TESTED |
| N10 | All clients | Login, role scope, MFA revocation and audit continuity across web/mobile/desktop with two synthetic tenants; every cross-tenant request rejected by server | NOT TESTED |
| N11 | All clients | Approval then provider action: durable idempotency key, one execution receipt, second submission no duplicate, action and actor auditable | NOT TESTED |
| N12 | All clients | Billing sandbox checkout/portal/cancellation and signed webhook delivery under approved test-only Stripe mode | NOT TESTED |
| N13 | Release | Verified signed installer metadata, dependency/SBOM checks, rollback/restore drill and monitored failure recovery with exact artifact SHA | NOT TESTED |
| N14 | Mail | Test-only recipient allowlist, audited approved message delivery, consent/unsubscribe handling; all other recipients blocked | NOT TESTED |

## Bounded local evidence (not acceptance)

- Desktop 12 Vitest assertions, TypeScript/lint, and local unsigned webview bundle: **PASS (local only)**.
- Mobile four endpoint-validation assertions and TypeScript: **PASS (local only)**.
- Android/iOS Expo JavaScript export: **PASS (local only)**; no native package, simulator or physical device execution.
- Expo mobile-web export: **FAIL (toolchain)**, missing `react-native-web`/`react-dom`; separate `frontend/` web app remains independently testable.

## Contract / signoff requests

- **Chat 1:** MFA step-two replay prevention and throttling; enforce session revocation across clients, tenant isolation, role gates, audit/idempotency. Freeze staged gate before any real acceptance.
- **Chat 2:** Conductor allowed auth-flow domain(s), signed install path, connection states distinct from first proven sync and as-of/source evidence. Backend status saying `active` alone is not financial record correctness.
- **Chat 3/4:** Frozen read and safe action contract for the domain screens, permission mapping for finance/logistics/compliance, especially sales-rep record assignment.
- **Chat 5:** Remove mobile-web build dependency blocker or explicitly descope that mobile-specific web target; run keyboard, screen reader, emulator, device tests with owner-approved synthetic accounts.

Owner approval field: **NOT SIGNED**. Gate A accepted: **NO**. Staging deployment approval for this workstream: **NO**. Production launch approval: **NO**.

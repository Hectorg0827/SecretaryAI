# Workstream 05 — Product experience and acceptance

Baseline: `d79284a40db6e069aa3b39cb00376c95060c7983` (2026-10-26). Branch: `workstream/05-product-qa-20261026`. No deployment, merge, live migration, provider write or backend modification is authorized by this workstream.

## Source audit and interface requests

| Area | Source evidence | Impact and requested contract |
| --- | --- | --- |
| Web reorder/shipments/cost | `frontend/src/pages/Logistics.tsx` initializes three empty arrays with no read API | Chat 3: authenticated tenant-scoped `GET` contract for reorder recommendations, persisted shipment status and cost variance history. Include source, as-of, pagination, processing/error states |
| Web saved POs | `frontend/src/components/logistics/PurchasingRecords.tsx` calls existing `/api/logistics/orders` and `/orders/{po_number}` | Real read route exists, but staged provider-backed acceptance is pending Chat 3 |
| Mobile logistics | `mobile/src/api/logistics.ts` calls `/reorder-alerts`, `/shipments` and `/cost-variances`, absent from `backend/app/api/logistics.py` | Chat 3: freeze endpoints/schemas or provide supported alternative; never synthesize response data |
| Dashboard sources | `backend/app/api/dashboard.py` summary can lack generated timestamp and substitutes zero for pending/email dependency errors | Chat 1/2: return per-source freshness, tenant, provider identity, partial failure status and stale thresholds |
| Dashboard refresh side effect | `backend/app/api/dashboard.py` `/refresh` schedules a morning-briefing worker rather than a pure refresh | Chat 1: separate safe cache recomputation from delivery; Chat 5 uses GET-only view reload until frozen |
| Sales errors | `backend/app/api/dashboard.py` `/sales` catches adapter error and uses `invoices = []` | Chat 2: fail closed or expose an unavailable source flag; do not convert provider failures into revenue zero |
| Rep accounts | `backend/app/api/accounts.py` assumes optional `assigned_rep_id`, skips only rows assigned to someone else, and does not check assignment in detail | Chat 2 + Chat 1 security gate: fail closed on unknown assignment and enforce record ownership server-side |
| Follow-up notes | `backend/app/api/dashboard.py` notes read/update filter on company but not creator/assigned rep | Chat 2: authenticated user-scoped notes contract before private rep workspace |
| Approval completion | `backend/app/api/actions.py` response distinguishes queued, sent, failed, uncertain | Chat 1: status lookup, durable idempotency and execution evidence; Chat 5 shows real stage, not 'approved & sent' |
| Opportunities | Feed has free-form `event_type`/`action_type` but no frozen opportunity taxonomy | Chat 2/4: classification, source evidence, permission-checked drilldowns and clear next-action schema |
| Billing | Existing `/api/billing/plans`, `/subscription`, `/checkout`, `/portal`, `/invoices` | Chat 1/2: frozen Stripe sandbox checkout, entitlements, cancellation and webhook semantics before customer UX acceptance |
| Ungated direct email send | `backend/app/api/dashboard.py:321-350` executes `adapter.send_email_reply` based only on `trigger_actions`, while an audit insert failure is swallowed | **Chat 1 security release blocker:** require approved immutable draft, policy tier, tenant/recipient validation, idempotency and durable audit before any provider call; disable or quarantine this endpoint until then. Chat 5 removed direct-send UI/API helpers; copying a draft is local-only |
| Read acknowledgment falsely succeeds | `backend/app/api/dashboard.py:355-366` swallows Gmail mark-read errors and returns success; `backend/app/api/inbox.py:238-267` does likewise for database failures | Chat 1/2: surface real failure, persist durable read/ack state and return truthful `ok`/status. Web now updates local read state only after a successful HTTP response, but a false HTTP 200 remains an unresolved server defect |
| Partial inbox reads silently fail | `backend/app/api/inbox.py:78-220` catches per-source DB errors, then returns an ordinary empty success at lines 229-233 | Chat 1/2: fail closed or include per-source `available`, `as_of`, `error_code` and partial status. UI can display transport failures but cannot detect server-suppressed gaps |
| Work queue inventory mismatch | `backend/app/api/inventory.py:78` compares the entire `status` query string with one status, whereas prior web UI sent `critical,out_of_stock` | Chat 3: align canonical `GET /inventory/alerts` schema, tenant and freshness; Chat 5 now uses that existing endpoint and filters `needs_po` client-side |
| Draft PO permissions | `backend/app/api/actions.py:331-359` allows any authenticated user to create a PO draft without an explicit action permission | Chat 1/3: require correct tenant/role policy server-side, durable idempotency, approved vendor/line-item schema. Chat 5 hides the PO-draft control for roles lacking draft rights, but client gating alone is insufficient |
| False settings persistence | Prior `frontend/src/pages/Settings.tsx` showed editable notification, inventory and desktop-agent values and a “Settings saved” toast with no persistence call | Chat 1/4: define tenant/role-scoped read/write settings contract, validation and audit before reactivating these controls; Chat 5 withheld the nonfunctional controls |
| OAuth callback status | Prior settings page treated `?qbo=connected` or `?gmail=connected` query parameters as connection proof; `backend/app/api/auth.py` returns OAuth links to exact Intuit/Google consent hosts | Chat 1/2: verify source-of-truth connection status (and read freshness), authorize connect/disconnect operations server-side. Chat 5 no longer claims connection based on URL params; validates HTTPS consent host |
| QuickBooks Desktop connection | `backend/app/api/setup.py:83-229` starts a Conductor session and polls connection status, while `backend/app/api/settings.py:99` treats mere stored `conductor_end_user_id` as connected | Chat 2: distinguish `registered`, `authorized`, `first_sync_verified`, `stale` and `failed`, including provider receipt and source timestamp. Chat 5 marks Conductor authorization only, not successful sync |
| Connector install and status | `backend/app/api/connectors.py:629-662` rotates owner-only install secret; UI formerly printed an unverified pip package command and showed zeros on status failures | Chat 1/2: freeze verified signed installer distribution, rotation/revocation, source health/freshness, and one-time secret handling. Chat 5 masks the one-time value, removes unverified installer instructions and marks outages unknown |
| MFA status and policy | `backend/app/api/auth.py:550-569` already returns `totp_enabled` from authenticated `GET /auth/me`; `/login` returns a five-minute `pre_auth_token` for second factor | Chat 5 now reads enrollment truthfully and uses the short-lived token without re-sending a password. Chat 1: verify mandatory-MFA policy, one-time token replay protection, per-account second-factor throttling and session revocation across devices |
| Registration policy | `backend/app/api/auth.py:573-602` requires minimum eight characters, a letter, a digit and 72-byte UTF-8 maximum | Chat 5 now mirrors server validation visibly; Chat 1 to freeze invitation/verification, duplicate signup, tenant bootstrap, mandatory MFA and rate-limit acceptance |
| Password recovery | No account password-reset request/complete route is present in inspected `backend/app/api/auth.py`; prior login offered only “Contact your account owner” | Chat 1: authenticated/self-service test-only password reset contract with expiry, single-use token, rate limiting, session revocation and deliverability proof. Chat 5 labels the gap explicitly; does not fabricate a recovery flow |
| Stripe mock URLs | `backend/app/api/billing.py:160-163,220-221` can return mock checkout/portal URLs and subscription GET is DB-record-only | Chat 1/2: fail closed when Stripe unavailable, freeze entitlements, test-mode contract, cancellation states and webhook evidence. Chat 5 added only read-only owner billing status; no checkout or portal action |
| Desktop credential vault | `desktop/src/auth.ts` formerly cached a new JWT even if OS credential persistence failed and silently ignored sign-out vault deletion failures | Chat 5 now fails sign-in/sign-out closed on vault errors; Chat 1: server-side cross-device token revocation and durable sign-out audit remain a separate acceptance gate |
| Desktop 2FA | `desktop/src/auth.ts` sent `email` and `password` again alongside `pre_auth_token + totp_code` | Chat 5 sends the second-step token/code only; Chat 1 still owns backend replay protection, MFA policy and throttling |
| Desktop QB consent | `desktop/src/pages/Setup.tsx` invoked a POST creating/reusing a Conductor identity immediately after local QB detection | Chat 5 now requires explicit Start authorization, opens only an HTTPS provider link, and labels Conductor activation short of first data sync. Chat 2: freeze exact allowed provider host(s), first verified sync / source as-of contract; never equate account registration with financial data correctness |
| Mobile release entry | `mobile/package.json` used `expo-router/entry` despite no router `app/` tree and a fully implemented custom `App.tsx` | Chat 5 aligned the release entry with Expo AppEntry and removed invalid `expo-speech` config plugin; Android and iOS JS export now build locally; no installed-device acceptance yet |
| Mobile endpoint selection | `mobile/src/screens/SettingsScreen.tsx` allowed any runtime API host to receive the stored bearer token; push preference switched locally with no persistence | Chat 5 removed endpoint editing and false push preference, validates a build-configured HTTPS API (dev-only loopback HTTP), and shows a fail-closed configuration screen. Chat 1/4: freeze audited notification preference storage/consent contract before reintroducing toggle |
| Mobile settings reachability | `mobile/src/navigation/AccountsStack.tsx` made Settings reachable only to roles allowed into Accounts | Chat 5 adds authenticated root Settings route via accessible Dashboard avatar so viewer and sales_rep can sign out and manage MFA |
| Mobile approvals false clear state | `mobile/src/screens/ApprovalsScreen.tsx` kept stale drafts on fetch error and showed “All caught up!” for an unreadable queue | Chat 5 invalidates stale draft controls on errors, displays unknown status and says approval authorizes queue processing, not provider-confirmed execution |

## First isolated UI increment

- Web owner/manager dashboard: six read-only briefing sections sourced from existing dashboard, sales, feed, notes, approval and audit reads. Partial outages show unavailable states, and timestamps do not pretend that cached data are live.
- Mobile: `/auth/me` role-filtered tab visibility and independently settled authorized dashboard reads. The backend remains the authorization source of truth. Rep account UI is temporarily withheld pending record-scoping proof.
- MFA: removed external QR rendering that transmitted an authenticator enrollment URI to a third-party host; manual setup key remains available pending a local QR renderer.
- Approvals: distinguish authorization from provider-confirmed execution. No new outbound action is introduced.

## Second isolated UX and QA increment (synthetic only)

- Dashboard and unified Inbox email drafts are **copy-only** pending a verified approval/audit send contract. No web or mobile API helper calls the direct-send endpoint. Existing backend endpoint remains a security blocker for Chat 1.
- Dashboard inbox and approvals plus unified Inbox now show unavailable-source alerts instead of a misleading clean slate after HTTP errors. Backend-suppressed partial failures remain unobservable until Chat 1/2 provide source metadata.
- The Work Queue shows partial source outages; restricts company-wide follow-up note reads to owner/manager; calls existing `GET /api/inventory/alerts`; requires a deliberate approval confirmation; and separates a queued PO draft from an actually resolved inventory shortage. Sales reps can view inventory but cannot trigger PO drafts in this UI.
- Browser fixture coverage exercises drafts without outbound HTTP sends, per-source failures, inbox transport failure, mobile-width dashboard and route restrictions. It does **not** prove live mail/provider or server authorization behavior.
- Local verification: `frontend npm test -- --reporter=dot`: **21 passing** (five suites); web and mobile `npm run typecheck` passed; web `npm run lint` has **0 errors / 21 warnings**; web `npm run build` and `npm run test:css` passed; synthetic Chromium `e2e/verify_morning_briefing.py` passed at phone and desktop widths. Native device, actual email and staging acceptance remain **NOT TESTED**.

## Third isolated onboarding and billing increment

- Owner-only Billing Settings panel reads `/api/billing/subscription`; displays the backend record and unknown/unavailable states clearly. Checkout, portal, cancellation and payment changes are deliberately unavailable until provider-backed acceptance.
- Connector page now shows an error instead of fabricated zero connected devices on a failed API read. Only the owner sees install-secret rotation; the one-time secret is masked and copyable in the session, with no unverified install/package commands.
- Settings stopped claiming OAuth success from query parameters, checks exact HTTPS consent origins, and hides unsaved preference switches. QuickBooks Desktop setup uses an explicit user-initiated authorization link and stops polling on repeated errors. Authorization is not represented as proof of data sync.
- Browser and unit tests remain synthetic. This increment does not constitute QuickBooks, Stripe, MFA, signing, or live deliverability acceptance.
- Local verification after increment: **23 web Vitest tests** across six suites, TypeScript PASS, lint 0 errors / 21 warnings, build and CSS check PASS; synthetic Chromium briefing, source outages, role restrictions, owner-only billing and connector permissions, and no billing writes PASS. Previous GitHub Chat 5 revision (`7299dbd0e2f680951ffeece24706ce71ab2bf526`) CI and CodeQL PASS; new revision CI pending until pushed.

## Fourth isolated authentication and customer-entry increment

- Web login uses backend's existing `pre_auth_token + totp_code` contract for step two, clears the password after successful first-factor verification, and never stores the partial session as an access token. Invalid second-factor attempts remain on the verification form.
- Registration mirrors server password/email validation and displays unmet rules or password mismatch rather than leaving a disabled submit button unexplained.
- Settings gets authoritative `totp_enabled` from `GET /auth/me`; backend errors display unknown with retry, not an assumed disabled state. A returned enabled state does not offer another enrollment button.
- Browser acceptance uses test-only tokens and synthetic inputs for signup, protected-route redirect, invalid MFA retry, successful MFA and lost MFA-status service. No actual identity provider, Redis/session revocation or real tenant data is tested.
- Local validation: 24 web Vitest tests (six suites) PASS, web typecheck/build/CSS PASS, lint 0 errors / 21 warnings, and synthetic Chromium browser PASS across signup, MFA, owner/manager scope, unreadable sources and read-only billing. Provider-backed identity, mail delivery and revoked-session acceptance remain NOT TESTED.

## Fifth isolated cross-device entry, consent and release-build increment

- Desktop login sends only `pre_auth_token + totp_code` in the second step, clears password UI state and exposes labelled code/back controls. The native session does not become authenticated if credential-vault persistence fails, and sign-out does not falsely succeed if deleting the vault credential fails.
- Desktop QB setup checks installation read-only, then waits for an explicit owner Start authorization click before `POST /api/setup/qb-desktop/start`. The separate Open Setup click launches only an HTTPS link in the system browser; repeated polling failures surface as errors. Provider activation is **not** presented as verified first financial sync.
- Mobile package entry now points to the actual `App.tsx` tree (not unimplemented Expo Router pages); invalid `expo-speech` config plugin removed. Production mobile rejects missing, insecure, placeholder or mutable API destinations and does not render sign-in on misconfiguration. Settings are accessible to every authenticated role via the Dashboard avatar.
- Mobile Settings no longer pretends push preferences were saved, nor exposes a writable bearer-token destination. Mobile sign-in clears the password after a successful first factor and surfaces incomplete-session credential cleanup failures; mobile approvals no longer claim zero pending drafts after source failures.
- Local tests: **12** desktop Vitest tests across two suites (including mocked native vault failure and explicit QB consent), desktop typecheck/lint (**0 errors, 1 pre-existing warning**)/build PASS; mobile typecheck PASS; **4** pure native URL validation tests PASS. Android JS bundle (`expo export --platform android`) and iOS JS bundle (`expo export --platform ios`) PASS with synthetic HTTPS configuration. **Actual devices, installed binaries, screen readers, Conductor, and provider-backed MFA remain NOT TESTED**. Mobile-web Expo export is blocked by missing `react-native-web`/`react-dom` dependencies; main web frontend is separate and not a substitute for that test.
- The Auth0 MFA security guidance was used only as general fail-closed design review; this repository retains its existing backend auth, and **no Auth0 provider or SDK was introduced**.

## Sixth isolated mobile-web runtime and release reproducibility increment

- Added the SDK 51-compatible `react-native-web@0.19.10` and `react-dom@18.2.0` dependencies. Set Expo's web output to `single` for the app's actual non-router `App.tsx` entry. The prior `static` mode incorrectly expected an Expo Router route tree and could report an error even with process exit code 0.
- Discovered a real blank-screen bug during local Chromium smoke testing: `expo-secure-store` methods are native-only and the web startup attempted to use them. The web client now holds its bearer token **only in this tab's memory**; a tab reload requires a fresh sign-in. Native iOS/Android still use SecureStore.
- The compiled mobile-web SPA now renders at 390px and 1024px without JavaScript exceptions or horizontal overflow. A synthetic-only Playwright case verified invalid then successful MFA, exact no-password second-step request fields, a role-limited viewer dashboard, and account/security Settings access. All requests to the synthetic API host were intercepted; no provider or staging connection was made.
- A second SPA exported without any API URL now displays a fail-closed configuration screen with **no** sign-in fields or outbound requests. A normal repeated Expo export unexpectedly reused the previous environment's Metro cache and API base URL; `--clear` produced the correct result. The reproducible `mobile npm run export:qa` script includes `--clear`; release engineering must verify the embedded destination and exact artifact hash every time an environment changes.
- The mobile-web **bundle and synthetic browser** blocker is resolved, but device installs, native screen readers, test-only backend identity and real session revocation remain **NOT TESTED**. No artifact was deployed or signed.

## QA / release evidence checklist

Synthetic fixtures belong **only** in preview/tests, never the live UI. Actual staged source identity and timestamps are required for provider acceptance.

| Gate | Status | Pending proof |
| --- | --- | --- |
| Pinned branch isolation | PASS | Exact baseline verified locally |
| Web unit tests and typecheck | PASS | 24 passed; `npm run typecheck`: pass (local only) |
| Web lint | PASS | `npm run lint`: 0 errors, 21 warnings including pre-existing areas |
| Mobile typecheck | PASS | `npm run typecheck`: pass (local only) |
| Desktop typecheck and native-UI unit tests | PASS | `npm run typecheck`: pass; `npm test -- --reporter=dot`: 12 passed; production Vite build with synthetic HTTPS URL passes; not signed or installed |
| Android/iOS JS release bundles | PASS | Local Expo exports compile with synthetic HTTPS URL; no emulator/device, App Store or signed package proof |
| Native Expo mobile-web export | PASS | Expo 51 SPA export with installed `react-native-web` / `react-dom`, clean cache. Synthetic Chromium 390/1024px renders sign-in and fail-closed config; full provider/device acceptance NOT TESTED |
| Mobile secure API destination | PASS | Four local fixture tests reject release HTTP/localhost, missing/placeholder and userinfo/query/fragment; real device requests NOT TESTED |
| Built CSS token verification | PASS | `npm run test:css`: pass |
| Local synthetic browser briefing and role restriction | PASS | `e2e/verify_morning_briefing.py` on built localhost preview at 390/1440px; outage, role-denial, copy-only drafting, billing and onboarding checks |
| Real browser login/MFA/session/restricted routes | NOT TESTED | Staged provider-backed auth and revoked-token acceptance |
| Six digest sections source correctness | NOT TESTED | Provider-backed payload, freshness and per-role scope |
| Approval-then-action idempotency | NOT TESTED | Test-only provider receipt and durable audit state |
| Rep record ownership and private notes | FAIL | Backend fail-closed ownership contract |
| Direct-send endpoint policy gate and audit | FAIL | Backend endpoint lacks confirmed immutable approval/idempotency/audit semantics; UI does not expose it |
| Inbox partial-read and mark-read truthfulness | FAIL | Backend catches per-source and mutation errors and can return success |
| PO draft permission/idempotency | FAIL | Server enforces authentication but not an explicit create-PO-draft permission |
| Logistics shipment/cost device parity | FAIL | Missing read API contracts |
| Mobile/desktop accessibility | NOT TESTED | Device screen reader, keyboard, layout evidence |
| Stripe sandbox checkout/cancel/webhooks | NOT TESTED | Human-approved provider test session |
| Billing subscription record view | PASS | Synthetic UI verifies read-only owner billing and no checkout/portal calls; real Stripe remains NOT TESTED |
| Onboarding preference persistence | FAIL | No frozen settings write/read contract; nonfunctional controls withheld |
| Connector signed installer/source freshness | FAIL | Verifiable installer and first-sync record not yet available |
| Monitoring, outage, recovery, rollback | NOT TESTED | Exact staged SHAs, injected outages, restore drill |
| Signed packages and email deliverability | NOT TESTED | Signing identities, test-only allowlist and receipts |
| Owner launch signoff | NOT TESTED | Explicit signoff after all blockers pass |

The synthetic Chromium test never uses a real JWT or backend and is not evidence of server-side authorization. The built web bundle is over 500 kB minified; code-splitting remains an optimization follow-up, not a release-readiness PASS.

No staging deployment may occur before Chat 1's safety gate and the owner's separate approval. This checklist does **not** assert all 25 domain modules are working.

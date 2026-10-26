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

## First isolated UI increment

- Web owner/manager dashboard: six read-only briefing sections sourced from existing dashboard, sales, feed, notes, approval and audit reads. Partial outages show unavailable states, and timestamps do not pretend that cached data are live.
- Mobile: `/auth/me` role-filtered tab visibility and independently settled authorized dashboard reads. The backend remains the authorization source of truth. Rep account UI is temporarily withheld pending record-scoping proof.
- MFA: removed external QR rendering that transmitted an authenticator enrollment URI to a third-party host; manual setup key remains available pending a local QR renderer.
- Approvals: distinguish authorization from provider-confirmed execution. No new outbound action is introduced.

## QA / release evidence checklist

Synthetic fixtures belong **only** in preview/tests, never the live UI. Actual staged source identity and timestamps are required for provider acceptance.

| Gate | Status | Pending proof |
| --- | --- | --- |
| Pinned branch isolation | PASS | Exact baseline verified locally |
| Web unit tests and typecheck | PASS | `npm test -- --reporter=dot`: 17 passed; `npm run typecheck`: pass (local only) |
| Web lint | PASS | `npm run lint`: 0 errors, 25 warnings including pre-existing areas |
| Mobile typecheck | PASS | `npm run typecheck`: pass (local only) |
| Desktop typecheck and existing unit tests | PASS | `npm run typecheck`: pass; `npm test -- --reporter=dot`: 4 passed |
| Built CSS token verification | PASS | `npm run test:css`: pass |
| Local synthetic browser briefing and role restriction | PASS | `e2e/verify_morning_briefing.py` on built localhost preview at 390/1440px; outage and role-denial checks |
| Real browser login/MFA/session/restricted routes | NOT TESTED | Staged provider-backed auth and revoked-token acceptance |
| Six digest sections source correctness | NOT TESTED | Provider-backed payload, freshness and per-role scope |
| Approval-then-action idempotency | NOT TESTED | Test-only provider receipt and durable audit state |
| Rep record ownership and private notes | FAIL | Backend fail-closed ownership contract |
| Logistics shipment/cost device parity | FAIL | Missing read API contracts |
| Mobile/desktop accessibility | NOT TESTED | Device screen reader, keyboard, layout evidence |
| Stripe sandbox checkout/cancel/webhooks | NOT TESTED | Human-approved provider test session |
| Monitoring, outage, recovery, rollback | NOT TESTED | Exact staged SHAs, injected outages, restore drill |
| Signed packages and email deliverability | NOT TESTED | Signing identities, test-only allowlist and receipts |
| Owner launch signoff | NOT TESTED | Explicit signoff after all blockers pass |

The synthetic Chromium test never uses a real JWT or backend and is not evidence of server-side authorization. The built web bundle is over 500 kB minified; code-splitting remains an optimization follow-up, not a release-readiness PASS.

No staging deployment may occur before Chat 1's safety gate and the owner's separate approval. This checklist does **not** assert all 25 domain modules are working.

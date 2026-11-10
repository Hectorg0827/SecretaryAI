# Lion BI Gate 2 — isolated staging snapshot cron

## Scope

- Railway project `77513c77-44df-485e-a927-d57d6a80b2c1`, environment `staging` only.
- Staging Supabase ref `xerktvdgjvjozvckjyfp` and one approved company `89399925-ea7d-43f3-91cd-95f9a5636b73` only.
- Runs `python -m tasks.staging_snapshot_job` from `backend/Dockerfile.snapshot` on a Railway cron schedule `0 * * * *` (UTC).
- Writes only the scoped `report_snapshots` `dashboard_summary` row and, if QBO access tokens require refreshing, their encrypted OAuth credential metadata. It does **not** write accounting transactions, send messages, run agents, or dispatch other Celery tasks.
- QBO must be an active **sandbox** Online connection. The job fails closed otherwise.

## Required configuration

The cron service uses references to the existing **staging backend** secrets for the Supabase service role, DB isolation validation, encryption secret, Redis lock, and Intuit sandbox OAuth; it does not need SendGrid/Resend, Anthropic, OpenAI, or Stripe credentials. Required nonsecret values:

```
SECRETARYAI_DEPLOYMENT_ENV=staging
SECRETARYAI_EXPECTED_SUPABASE_PROJECT_REF=xerktvdgjvjozvckjyfp
SECRETARYAI_READONLY_REFRESH_COMPANY_ID=89399925-ea7d-43f3-91cd-95f9a5636b73
INTUIT_ENVIRONMENT=sandbox
COMPUTER_USE_ENABLED=false
SECRETARYAI_EMAIL_DELIVERY_MODE=disabled
SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED=false
SECRETARYAI_STAGING_EMAIL_PILOT_ENABLED=false
SECRETARYAI_INVITE_DELIVERY_ENABLED=false
SECRETARYAI_PUSH_DELIVERY_ENABLED=false
BILLING_ENABLED=false
```

The executable also checks Railway's injected project, environment ID, and environment name before opening a database connection. It performs a single-tenant company lookup, verifies `qb_connection_status=connected` and `qb_type=online`, holds a Redis lock across the refresh, and returns non-sensitive completion metadata. An unsuccessful source read does not replace the previously persisted snapshot with invented zero metrics.

## Acceptance and operations

1. Build the dedicated Dockerfile and run the cron once; expect a nonzero exit on missing/mismatched safeguards, missing source, or failed snapshot persistence.
2. Verify the new `report_snapshots` row's `generated_at`, `generated_by=staging_readonly_cron`, `source_read_status=completed`, and `source_quality=partial`.
3. Confirm the Manager dashboard no longer warns about age and `GET /api/briefing/today` returns a **partial** six-section briefing, not a certified complete digest.
4. Confirm no outbound emails, accounting transactions, or Celery beat tasks were triggered. No Desktop connector is expected for the Online tenant.
5. Monitor Railway cron logs and snapshot age. Alert when a refresh fails or the snapshot is older than 24 hours.

Rollback: disable the cron schedule or scale down/stop only the dedicated snapshot service. Leave Backend, Web, and Redis unchanged; the Manager UI will truthfully warn when the last snapshot ages out.

Limitations: An online connector can produce syntactically valid but incomplete sandbox data. The stored snapshot intentionally remains `source_quality=partial`; source-level revenue reconciliation and complete executive-digest validation are separate gates.

## Gate 3 — read-only financial comparison

The same isolated staging cron now optionally performs one additional **GET**
against QuickBooks Online sandbox: `reports/ProfitAndLoss` with a 30-day explicit
window, `accounting_method=Accrual`, and `summarize_column_by=Total`. It extracts
the top-level `Income` section's `Total Income` only if the returned report
name, period, accounting basis, currency and total column are unambiguous.

- The existing `sales_30d` measures nonvoid invoice-header totals. P&L income
  measures a different accounting concept and may include sales receipts, credit
  memos, journal entries and other adjustments. A match **never** certifies
  revenue, and a difference is a review signal, not a proof of corruption.
- The `dashboard_summary` payload contains `finance_comparison` with statuses
  `aligned_not_certified`, `variance_detected`, or `unavailable`, explicit
  dates/currency/source, and `comparison_is_reconciliation=false`.
- If the report fails or is malformed, the cron still retains the completed
  basic snapshot but marks finance `unavailable` and stores **no fabricated
  ledger income**. The executive briefing shows an unavailable evidence warning.
- Finance evidence appears only for roles that can view financials. The
  six-section briefing continues to show `complete_digest=false` and
  `source_quality=partial`.

Acceptance: verify the latest staging snapshot includes the non-certified
comparison object and the Manager briefing reports its availability or variance
accurately. Never set a financial `verified` flag based solely on these two
aggregates. The next gate must independently reconcile credits, sales receipts,
adjustments, tax treatment, currency and source transaction completeness.

### Safe diagnostics for an unavailable P&L

When the sandbox report cannot be compared, the isolated cron now records a
small allowlisted `finance_comparison.reason` and prints that same category to
its Railway runtime log. No provider response bodies, OAuth tokens, account IDs,
or financial amounts are placed in diagnostics. Categories distinguish an OAuth
failure (`provider_auth_failed`), a report API failure (for example
`provider_bad_request`), transport/rate limits, a report with explicit
`NoReportData` (`report_no_data`), an absent income section
(`report_income_missing`), and incompatible report format/period. All still
produce `status=unavailable`, `source_quality=unavailable`, and no ledger total.
For an income section with an unusable summary, finer codes distinguish missing
summary, missing columns, unexpected total label, missing amount, multiple
amounts, and nonnumeric amount; none are interpreted as a valid zero.

Do not infer a valid $0 P&L from `report_no_data` or an absent `Income` section.
The external Intuit sandbox must return a period-matched accrual income summary
before invoice-vs-income comparison can pass its live acceptance gate. If the
category is `provider_auth_failed`, reconnect/refresh the approved sandbox
credentials through the existing OAuth flow; do not copy credentials to logs or
the UI. The hourly production schedule of the staging cron is `0 * * * *` UTC.

## Gate 4 — tenant-scoped purchasing coverage for Lola

The staging cron now also reads **headers only** from two local Supabase
tables: `purchasing_orders` (`id,status,company_id`) and `staged_warehouse_receipts`
(`id,company_id`, filtered to `status=pending`). Each read is filtered by the approved
`company_id`, checked again against each returned row, limited to 500 rows,
and rejected if the limit is exceeded,
identifiers are duplicated/missing, a PO status is unknown, or a read fails.
No supplier references, line details, costs, customer records, receipt payloads,
or individual identifiers enter the dashboard snapshot or briefing.

The cached `purchasing_coverage` reports counts by PO status and the number of
pending staged receipts with `source_quality=partial`. Failed reads produce
`status=unavailable`, null counts and `source_quality=unavailable`: an outage
is never translated to zero purchase orders or zero receipts. The cached data
is validated again before use. Only an owner or manager may see the purchasing
aggregate in `GET /api/briefing/today`; its `complete_digest` flag remains
**false**. The source coverage label is `dashboard_snapshot_plus_purchasing`
for refreshed Gate 4 snapshots; older snapshots retain the prior label.

The digest explicitly notes that freight, customs, vendor ETA, supplier
claims, and full logistics reconciliation are **not assessed**. This gate
does not certify external stock, prove an AP/AR balance, approve purchase
orders or receipts, or dispatch any external action. The ordinary dashboard
refresh remains unchanged; only the allowlisted staging cron opts into these
additional reads. Future gates can expand coverage after verifying independent
source quality and fine-grained role access.

## Gate 5 — read-only QuickBooks sandbox AR/AP document coverage

The same allowlisted staging cron now optionally issues two bounded QuickBooks
Online **query GETs**, independently reading Invoice and Bill headers across
the sandbox company. Pagination rejects duplicate identities and stops without
publishing counts if more than 5,000 documents are encountered. Invoice totals,
balances and dates must pass the existing strict QBO header normalizer; Bill
totals, balances and dates are independently validated. The documents are
counted as open when their observed balance is positive; overdue means a
positive balance with a due date before the recorded `as_of` date. Missing due
dates are shown separately instead of being silently treated as current.

Only aggregate header counts enter the snapshot: examined, open, past-due,
and open-without-due-date. Individual customer/vendor names, invoice or bill
IDs, monetary amounts, payment information and OAuth metadata never enter
Lola's briefing. The two sources fail independently: if AR fails, its counts
are null/unavailable while a valid AP read can still be shown, and vice versa.

No currency has been independently verified and no credits, applied payments,
AR/AP ledger balances or actual supplier obligations have been reconciled.
`currency_verified=false` and `ledger_reconciled=false` are enforced by a
strict cached-data contract. A zero document count means **only** that the
approved sandbox query returned no open records; it is not a certified real
business zero. Both observations are stamped with provenance and shown only
in the owner/manager `/api/briefing/today`; the complete-digest flag remains
false. The normal production and manual dashboard refresh paths do not opt in.

Separate finance-team employee roles, actual payment posting, AR statements,
AP approvals, and email communications remain out of scope. These require
explicit role policies and independent write-action approval gates.

## Gate 6A — read-only departmental finance access foundation

`GET /api/finance/workspace` is a subscription-gated **staging-only** endpoint.
It checks exact finance RBAC permission, confirms the caller's current active
role and tenant directly against `users` (not merely their JWT), then reads only
that tenant's fresh cached `dashboard_summary`. It never queries Intuit, posts
transactions, sends emails, or changes database records. The response is
`private, no-store` and returns only scopes authorized for the role:

| Role | Allowed scopes |
|---|---|
| Owner/Manager | AR, AP, ledger comparison, pricing status |
| Finance Controller | AR, AP, ledger comparison |
| AR Specialist | AR only |
| AP Specialist | AP only |
| Pricing & Billbacks | Pricing status only |

The last four roles are **policy-only reservations**, not active employee
accounts: the existing database `users.role` CHECK and manager-only invitation
flow have intentionally NOT been changed by this gate. Role assignment and
mailbox-verified onboarding must be migrated together, after verifying the
staging-only database target and all authorization/RLS implications. The new
roles have only `view_finance_workspace`, no broad `back_office` inheritance.

The workspace represents unavailable or not-yet-assessed evidence with null
counts. It never asserts billback validation, income certification, currency,
cash flow or a reconciled balance. The UI exposes the server-selected sections
without providing a role/scope selection input. Existing owner and manager
accounts can use the Finance Workspace now in staging; other existing roles
retain their prior access and are denied this endpoint.

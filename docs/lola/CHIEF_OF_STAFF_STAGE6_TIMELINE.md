# Lola Stage 6 — private recorded-case timeline (staging / draft)

Adds a read-only, authenticated, company+employee-scoped way to inspect existing
private Lola event metadata on a specific private contact and work item.

## Implementation

- `backend/app/intelligence/lola/private_timeline.py`: read-only exact tenant,
  employee, private contact UUID and project key scoping. Verifies current
  employee membership before reading records, verifies contact ownership,
  then pages only the exact recorded case events.
- `GET /api/lola/chief-of-staff/continuity/cases/{contact_id}/recorded-events`
  accepts required `project_key` and bounded `limit`/`offset`.
- Source projections exclude `summary`, `source_ref`, contact name, email, phone,
  all OAuth tokens, message text and attachments.
- Events are represented by tenant+employee-scoped opaque hash references and
  metadata (`channel`, `direction`, `kind`, occurred UTC timestamp, source class).
- Malformed or unscoped results, duplicate records, unsupported provider
  metadata, future timestamps and missing tables fail closed.
- Empty page means **no records in this scoped page**, never 'no reply'.
- `reply_status=not_assessed`, `history_completeness=unknown`,
  `provider_coverage_verified=false` and `eligible_for_follow_up_engine=false`
  are always returned. Pages do not establish provider synchronization completeness.

## Explicit non-goals

- No cross-channel contact/project attribution based on names, LLM summaries,
  email subjects or WhatsApp message bodies.
- No automatic reminder, scheduler, notification, webhooks, price change,
  accounting entry, or outbound messaging.
- `gmail_api` / `whatsapp_api` origins in existing records are just recorded
  origin labels. They are NOT sufficient proof of message authenticity,
  delivered status, independent provider history completion or employee consent.
- No provider API call, database schema change or hosted migration.
- Staged recorded events cannot satisfy the Stage 4 verified channel coverage
  requirements until source checkpoints, authentic inbound webhooks, and
  employee consent/mapping evidence are independently established.

## Acceptance

1. Full CI backend pytest + web build + 3-engine browser acceptance + CodeQL.
2. Authenticated staging checks with active employee, disabled user, another
   employee of same tenant, another tenant, and inaccessible private contact.
3. Confirm only approved metadata columns are selected and no raw private
   message/contact data leaves the backend.
4. Confirm missing or partial source data is not falsely labeled no-reply.
5. Stage 6 remains a stacked draft PR, not merged or deployed.
# Lola client ⇄ orchestration contract

**Owners:** Workstream 4 (client experience) ⇄ Workstream 1 (backend policy, permissions, execution).
**Source of truth for types:** [`frontend/src/lola/contract.ts`](../../frontend/src/lola/contract.ts).
**Status (combined Lion BI branch):** all four endpoints below are implemented by Workstream 1 in `backend/app/api/lola.py` on top of the Lola orchestrator, authorization and governance (tests: `backend/tests/test_lola_integration.py`). The legacy fallback remains in the client for older backends.

## Ground rules (both sides)

1. **The server decides.** Permissions, policy, approval rights and execution all happen on the server. The client may *hide* more than the server sends (defence in depth). It never grants more.
2. **Never fabricate.** The client only renders data and receipts the server sent. A turn counts as complete only when the server sends `done`.
3. **Unavailable is not empty.** If a source is down, stale, partial or forbidden, the server says so with `availability`. It must not send an empty list that looks like "all clear".
4. **Verified receipts only.** Set `receipt.verified = true` only after the effect is confirmed in the system of record (email accepted by Gmail, PO created in QuickBooks, and so on).

## Endpoints Workstream 1 needs to provide

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/lola/turn` | One conversational turn. Returns `text/event-stream`, one `LolaEvent` JSON object per `data:` line |
| `POST` | `/api/lola/actions/{id}/approve` | Approve a previewed action. Responds with a `Receipt` |
| `POST` | `/api/lola/actions/{id}/reject` | Reject a previewed action. Body: `{ "reason"?: string }` |
| `GET`  | `/api/lola/briefing` | Role-filtered `Briefing` for the command center |

All endpoints use the existing JWT bearer auth. `403` means permission denied, and the client shows a permission notice.

### `POST /api/lola/turn` request (`TurnRequest`)

```json
{
  "message": "Which invoices are overdue?",
  "conversation_id": "optional-uuid",
  "language": "en",
  "channel": "voice",
  "supersedes_turn": true,
  "attachments": [{ "name": "po.csv", "size": 1234, "mime": "text/csv", "text": "…" }],
  "context_route": "/accounts",
  "retention": { "transcript": "session", "audio": "none" }
}
```

- `supersedes_turn: true` means the user interrupted or corrected the previous turn. The server should cancel any unfinished work from that turn and must not execute anything it started.
- `retention` holds the user's choices. The server applies them and may enforce stricter company policy.
- `attachments[].text` is sent only for small text files (≤ 200 KB). Binary upload is a follow-up for Workstream 1 (see "Open items").

### Stream events (`LolaEvent`)

| `type` | Payload | Client behaviour |
|---|---|---|
| `conversation` | `conversation_id` | Stored for follow-up turns |
| `status` | `state: thinking\|working`, `step?` | Orb shows *thinking* or *working*; `step` is shown as "Working: …" |
| `text` | `delta` | Appended to the answer |
| `language` | `en\|es` | Switches the UI copy and the speaking voice |
| `card` | `EvidenceCard` | Rendered as metrics, chart, table, shipment timeline or financial explanation |
| `action_preview` | `ActionPreview` | Approval card. A button appears only when `can_approve` is true |
| `receipt` | `Receipt` | Execution receipt |
| `notice` | `permission_denied\|connector_outage\|data_unavailable\|info` | Shown as a notice. Never shown as a result |
| `suggestions` | `string[]` | Up to 4 follow-up chips |
| `error` | `message`, `retryable` | Error with a "Try again" button. The message should say that nothing was executed |
| `done` | — | **Required.** Without it, the client marks the turn failed |

Every card carries `availability` (`ok`, `partial`, `stale`, `unavailable` or `forbidden`), an optional `availability_note`, `evidence[]` (source, `as_of`, record ref, deep link) and an optional `sensitivity` (`standard`, `financial` or `executive`).

### Example stream

```
data: {"type":"conversation","conversation_id":"c-123"}
data: {"type":"status","state":"working","step":"Pulling open invoices from QuickBooks"}
data: {"type":"text","delta":"Five invoices are past due, totaling $48,200. "}
data: {"type":"card","card":{"id":"ar","kind":"table","title":"Overdue invoices","availability":"ok","sensitivity":"financial","columns":[…],"rows":[…],"evidence":[{"source":"QuickBooks Online · A/R Aging","as_of":"2026-11-09T14:02:00Z"}]}}
data: {"type":"action_preview","action":{"id":"draft-88","action_type":"send_collection_email","title":"Send 3 payment reminders","summary":"…","fields":[…],"risk":"low","can_approve":true,"status":"proposed"}}
data: {"type":"done"}
```

### `Receipt`

```json
{ "action_id": "draft-88", "status": "executed", "verified": true, "message": "3 reminders sent and accepted by Gmail.", "reference": "gmail:17f…", "executed_at": "…" }
```

`status` is one of:

- `executed` — finished.
- `submitted` — queued, not yet confirmed. The UI shows "awaiting confirmation".
- `failed` — did not happen.

The UI shows "Done · verified" **only** when `status = executed` and `verified = true`.

## Role-based visibility

The client mirrors `backend/app/auth/rbac.py` (see `frontend/src/lola/permissions.ts`) to hide command-center sections:

| Role | Sections |
|---|---|
| owner, manager | all nine |
| sales_rep | morning briefing, department priorities, next actions, recent tasks |
| back_office | the sales rep set plus logistics exceptions |
| viewer | Lola not available |

Cards and sections marked `financial` or `executive` are also hidden from roles without `view_financials`. **The server must still filter.** The client filter is only a backstop.

## Legacy fallback in use today

| Contract call | Fallback | What the user sees |
|---|---|---|
| `/api/lola/turn` (404/405) | `/api/chat/message` | Text only. The legacy keyword "action" becomes an info notice ("create it from Work"), never an approve button, because it has no server draft id. Attachments are refused with a notice |
| `/api/lola/actions/{id}/approve` (404) | `/api/actions/approve/{id}` | Receipt `submitted`, `verified: false` ("check the Audit Log") |
| `/api/lola/briefing` (404) | `dashboard/summary`, `feed`, `actions/pending` | Sections the legacy API can't produce are marked **unavailable**, not empty |

## Open items for Workstream 1 — status after combining

| # | Item | Status |
|---|---|---|
| 1 | back_office can use Lola | **Done** — `/api/lola/turn` uses Lola's own authorization, not `view_own_accounts` |
| 2 | `/api/chat/transcribe` language | **Done** — honours `language` (`en`/`es`), otherwise auto-detects |
| 3 | Attachments | **Text files done** — CSV/TSV/TXT/Markdown/JSON (inline `text`, ≤200 KB, ≤5 files) are read as untrusted data: preview card, totals, inventory and customer cross-checks within the caller's permissions, instruction-like text flagged. **PDF and Excel done** — the client posts the file to `POST /api/lola/files` (multipart, ≤5 MB); the server extracts text (PDF) or CSV (first sheet of .xlsx, values only), stores nothing, and the client sends the text inline as a normal attachment. Encrypted PDFs, scanned PDFs without text, legacy .xls, macro workbooks and zip bombs are refused with a plain message |
| 4 | Retention | **Partly done** — `transcript: none` stores no conversation rows; audio is never stored by the server |
| 5 | Pollable receipts | **Done** — `GET /api/lola/actions/{id}` returns `client_receipt` |
| — | Spanish answers | **Done** — `language: es` (or Spanish detected) answers in Spanish; a `language` event is sent; card labels, previews, notices and suggestions are Spanish; business data is never translated. WS2/WS3 findings text is still English, marked "(detalle en inglés)" |
| — | `supersedes_turn` | Turns are computed atomically before streaming, so there is no unfinished work to cancel; no action is executed by a turn |

### Original requests

1. Add a Lola permission so `back_office` can use Lola. Today `/api/chat/message` requires `view_own_accounts`, so back office gets `403` (the client shows a permission notice).
2. `/api/chat/transcribe` hard-codes `language="en"`. Please honour the `language` form field the client now sends, or auto-detect, for Spanish voice input on browsers without built-in speech recognition (Firefox).
3. Binary attachment upload (PDF, images), probably reusing `file_ingestion`.
4. Server-side retention: honour `retention.transcript = none` (don't persist the turn) and `retention.audio`.
5. Emit `receipt` events, or a pollable receipt endpoint, so that `submitted` receipts can later become `verified`.

# Lola — Workstream 1: Core Intelligence, Security & Orchestration

Lola is Lion BI's central assistant. She is **not a separate app**: she sits on
top of the existing FastAPI backend, Supabase database, Claude integration,
RBAC, policy engine (`policy_rules`), approval queue (`drafts`) and durable
execution ledger (`agent_executions`).

```
client (web / desktop / mobile / voice)
   │  POST /api/lola/ask  (session token)
   ▼
LolaOrchestrator ── build_principal()  ← users + lola_org_members + lola_delegations (fresh every request)
   │                Authorizer        ← RBAC + org level + record + attribute rules
   ├─ planning      intents · entities (only VISIBLE records) · multi-step plan
   ├─ tools         sales · finance · operations · purchasing · hr  (authorise → read → filter → redact)
   ├─ memory        lola_memories (company / user / team / sensitivity isolated)
   ├─ governance    proposals → drafts (existing queue) → receipts (lola_action_receipts)
   ├─ reconcile     dedupe evidence · stale / conflicting sources
   └─ compose       deterministic answer from authorised data (optional Claude narrator)
```

Code: `backend/app/lola/` · API: `backend/app/api/lola.py` · Tests: `backend/tests/test_lola_*.py`

## Combined Lion BI (Workstreams 1–4)

| Workstream | What it adds | How it is connected to Lola |
|---|---|---|
| 1 — core (this guide) | orchestrator, permissions, governed actions, memory | — |
| 2 — business intelligence (`app/lola_bi`) | 26 read-only finance / AR / AP / pricing / sales / CRM tools | `LolaBIScopeProvider` replaces the JWT-role scope; tools registered as `bi.*` Lola tools; `/api/lola/bi/*` uses the same scope |
| 3 — logistics (`intelligence/.../logistics`) | 14-stage shipment ledger, documents, receiving, monitoring, Q&A | `logistics_viewer_for` builds the viewer from the Lola principal; `logistics.assistant` is a Lola tool |
| 4 — client experience (`frontend/src/lola`, `desktop/src/lola`) | command center, dock, voice, approval cards | `/api/lola/turn`, `/briefing`, `/actions/{id}/approve|reject` (see `CONTRACT.md`, `CLIENT.md`) |

Two TypeScript contract files exist on purpose: `frontend/src/lola/contract.ts`
is the **UI wire format** (WS4 events, cards, receipts) and
`frontend/src/lola/contracts.ts` mirrors the **core WS1 models**
(`LolaResponse`, `ToolResult`, …). `backend/app/lola/client_events.py`
translates the second into the first.

## Security guarantees (all tested)

| Guarantee | Where | Test file |
|---|---|---|
| Role, company and hierarchy re-read from DB each request; token role ignored | `authorization.build_principal` | `test_lola_authorization.py`, `test_lola_api.py` |
| Org profile can never lift a user above their role | `_clamp_org_level` | `test_lola_authorization.py` |
| Record level: company / department / reporting line / territory / assignment / delegation | `Authorizer.account_scope` | `test_lola_authorization.py` |
| Attribute level: balances & costs hidden without financial access | `FINANCIAL_FIELDS`, `Authorizer.redact` | `test_lola_authorization.py` |
| HR restricted; payroll owner+HR-clearance only, and no payroll source exists | `Authorizer._base` | `test_lola_authorization.py` |
| Delegations always expire (≤90 days), bounded by grantor's current rights, no HR/payroll, no re-delegation | `_load_delegations`, `/api/lola/delegations` | both + SQL test |
| Denied tool never touches the source | `LolaTool.run` | `test_lola_authorization.py` |
| No existence oracle: hidden and non-existent accounts answer identically | `planning.resolve_customers` | `test_lola_orchestrator.py` |
| Prompt injection cannot widen scope or trigger actions; the model only sees authorised facts | orchestrator design | `test_lola_orchestrator.py` |
| Memory isolated by tenant/user/team/sensitivity; re-checked at recall (stale access hides it) | `memory.py` | `test_lola_memory.py` |
| Idempotent proposals; safe retry never duplicates a draft | `governance.propose` | `test_lola_governance.py` |
| Policy source unavailable → nothing queued | `governance.propose` | `test_lola_governance.py` |
| High-risk needs an independent leadership approver; limits; approver must see the record; requester re-validated at approval | `check_lola_approval` (hooked into existing `/api/actions/approve`) | `test_lola_governance.py` |
| Approved ≠ done: `completed` only with a ledger external reference (`verified=true`), enforced by a DB CHECK too | `governance.refresh`, migration | `test_lola_governance.py`, `scripts/test_lola_core.sql` |
| Browser keys cannot read Lola tables at all | migration grants | `scripts/test_lola_core.sql` |

## Shared contracts (for Workstreams 2, 3, 4)

* Python: `backend/app/lola/contracts.py` (source of truth, version `1.0.0`)
* JSON Schema: `docs/lola/contracts.schema.json`
* Example payloads for every status: `docs/lola/examples.json`
* TypeScript: `frontend/src/lola/contracts.ts`
* Live: `GET /api/lola/contracts`
* Offline mock: `app.lola.mocks.MockLola` (keyword routed — see its docstring)

Regenerate after changing a model: `python scripts/export_lola_contracts.py`
(a test fails if the published files are stale).

Statuses: `answered · partial · unavailable · denied · draft · pending_approval ·
approved · executing · completed · failed · rejected · needs_reconciliation`.

Client rules: always branch on `status`; show `withheld` notes; an action is
done only when `receipt.status == "completed" && receipt.verified`; voice
clients speak `speech`.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/lola/ask` | `LolaRequest` → `LolaResponse` |
| GET | `/api/lola/me` | caller's `LolaContext` + a `PermissionDecision` per resource (UI hints only) |
| GET | `/api/lola/tools` | registered department tools |
| GET | `/api/lola/contracts` | schemas + examples |
| GET/POST/DELETE | `/api/lola/memory[/{id}]` | personal / team / company memory |
| GET | `/api/lola/actions[/{receipt_id}]` | receipts, re-verified against queue + ledger |
| GET/POST/DELETE | `/api/lola/delegations[/{id}]` | expiring delegations |

Approvals use the **existing** `/api/actions/pending`, `/approve/{draft_id}`
and `/reject/{draft_id}` endpoints, which now apply Lola's rules to Lola drafts.

### Adding a department tool (Workstreams 3/4)

```python
from app.lola.tools import LolaTool, ToolOutput
from app.lola.contracts import Department, ResourceType, Sensitivity, EvidenceRef

class OpenShipmentsTool(LolaTool):
    name = "operations.open_shipments"
    department = Department.OPERATIONS
    resource = ResourceType.INVENTORY          # what the caller must be allowed to read
    sensitivity = Sensitivity.INTERNAL

    async def fetch(self, ctx, args, decision):
        rows = await ctx.adapter.track_shipments(args.get("tracking", []))
        # filter with ctx.authorizer (can_see_account / redact) — never by raw role
        return ToolOutput(data={"shipments": rows}, summary=f"{len(rows)} shipments",
                          evidence=[EvidenceRef(source_system="shiptrack", record_type="shipment")])
```

Register it in `default_registry()` and map an intent in
`planning.INTENT_TOOLS`. Authorisation before `fetch`, `unavailable` on
errors and the `ToolResult` envelope are handled by the base class.

## Migration requirements

1. Apply `supabase/migrations/20261109120000_lola_core.sql` (additive; adds
   `users_company_id_id_unique` if missing and four `lola_*` tables).
   Validated in CI by `scripts/validate_schema.sh` + `scripts/test_lola_core.sql`.
2. Optional, per company: insert `lola_org_members` rows. Without a row a user
   keeps today's behaviour (owner/manager company-wide, others own-scope). A
   manager row with `org_level='department_manager'` narrows them to their
   department + reporting line.
3. Optional: `LOLA_LLM_NARRATION=1` enables the Claude narrator (rewrites the
   deterministic answer using authorised facts only). Off by default.

## Repairs made to existing code

* `/api/chat/message` handed every customer and invoice to the model for any
  role. It now rebuilds the principal and filters reads through
  `ScopedAdapter`; company-wide `account_memory` is only injected for
  company-wide users; history loads are scoped to the user, not just the tenant.
* `/api/actions/pending` shows Lola drafts only to reviewers of that team.
* `tests/test_paywall.py` classified `/api/team` (added by PR #100 without a
  classification, which was failing the suite).

## Remaining blockers / follow-ups

* Lola answers in English only (Spanish UI/voice input works; answers get an info notice).
* Attachments are not read yet (notice returned; nothing is executed from them).
* WS3 logistics endpoints other than Lola's tool still build their viewer from the
  JWT role (short-lived tokens; role is reloaded on refresh). Moving them onto
  `logistics_viewer_for` is a follow-up.
* WS3 approved stock postings / claims and WS2 AP payment proposals have no
  executor (by design: nothing is posted or paid).

* **No external execution path for Lola drafts yet.** The durable gateway only
  accepts `email_reply`. Approving a Lola `draft_customer_email` /
  `draft_purchase_order` returns 409 and nothing is sent (by design for this
  workstream). Wiring Lola drafts into the durable email outbox / a QBO PO
  executor is a separate reviewed change.
* Legacy `/api/chat` still shows balances of a rep's *own* accounts (record
  scope is fixed; attribute redaction is not). Recommended: move clients to
  `/api/lola/ask`.
* Intent detection is rule-based (deterministic and injection-proof). An LLM
  classifier can be added behind it, but must only choose tools, never grant.
* Memory recall scans the 200 most recent tenant rows; add a per-user index /
  query before tenants accumulate large memory volumes.
* An admin UI/API for editing `lola_org_members` (owner-only) is not built yet;
  rows can be managed in SQL for now.
* Account visibility relies on the `accounts` table (`assigned_rep_id`,
  `territory`). Customers that exist in QuickBooks but not in `accounts` are
  visible only to company-wide users (fail closed).

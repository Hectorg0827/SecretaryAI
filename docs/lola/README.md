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
2. Optional, per company: set up the org chart in **Settings → Team → Org & access** (owner only), which writes `lola_org_members` rows. Without a row a user
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

* Spanish: Lola understands Spanish questions, answers in Spanish and drafts Spanish customer
  emails (`app/lola/i18n.py`, contract 1.1.0 `language`). The Workstream 3 logistics assistant
  understands Spanish questions. Detail that WS2/WS3 phrase in English is translated by
  Claude only when `LOLA_LLM_TRANSLATION=1`, and a translation is used only if every number,
  date, amount and identifier is unchanged; otherwise it is shown marked "(detalle en inglés)".
  Briefing and receipts follow `?language=` or the browser's `Accept-Language`.
* Attachments: CSV/TSV/TXT/Markdown/JSON are read as untrusted data (`app/lola/attachments.py`):
  never instructions, never stored, formulas neutralised, cross-checks only within permissions.
  PDF and Excel (.xlsx) are extracted by `POST /api/lola/files` (`app/lola/file_extraction.py`):
  in memory only, type checked by content, 5 MB cap, encrypted PDFs / legacy .xls / macro
  workbooks / zip bombs refused, formulas never evaluated.
* Scanned PDFs and photos (PNG/JPEG) are read by text recognition (`app/lola/ocr.py`) only when
  `LOLA_OCR=1` and `poppler-utils` + `tesseract-ocr` (eng, spa) are installed (the backend
  Dockerfile installs them). Bounded: 10 pages, 60 s, 2 jobs per worker, images ≤ 40 MP,
  no shell, no secrets in the programs' environment, temp folder deleted at once. Results carry
  `ocr: true` and a caution ("check numbers against the original") that the chat shows.
* WS3 logistics endpoints now build their viewer from the database role via
  `logistics_viewer_for` (demotion/deactivation applies on the next request).
* Approved Lola emails and QuickBooks Online purchase orders now have a gated
  executor — see `EXECUTION.md`. Every switch is off by default.
* WS3 stock postings: with `SECRETARYAI_LOLA_STOCK_STAGING_ENABLED=1` a clean count is staged on
  the existing two-person receiving path and booked only after a different owner/manager approves
  it (`app/lola/stock_receiving.py`, `EXECUTION.md`). WS3 claims stay drafts (never filed).

* Lola emails and purchase orders run through the durable queue and a gated
  executor (`EXECUTION.md`). Until an operator turns the switches on, approving
  returns "not enabled — nothing was approved or sent".
* Legacy `/api/chat` now applies record scope AND attribute redaction (balances,
  costs, margins hidden without financial access; shown as "withheld").
* Intent detection is rule-based (deterministic and injection-proof). An LLM
  classifier can be added behind it, but must only choose tools, never grant.
* Memory recall scans the 200 most recent tenant rows; add a per-user index /
  query before tenants accumulate large memory volumes.
* ~~Admin UI for `lola_org_members`~~ Done: **Settings → Team → Org & access**
  (owner-only; API `GET/PUT/DELETE /api/lola/org`, see below).
* Account visibility relies on the `accounts` table (`assigned_rep_id`,
  `territory`). Customers that exist in QuickBooks but not in `accounts` are
  visible only to company-wide users (fail closed).


## Org & access administration

`backend/app/api/lola_org.py` · UI `frontend/src/pages/OrgAccessSection.tsx` · tests
`backend/tests/test_lola_org_api.py`, `frontend/src/pages/OrgAccessSection.test.tsx`.

| Rule | Enforced |
|---|---|
| Only an **active owner in the database** can view or change it (token role ignored) | `_require_owner` |
| Nobody above their role: owner → executive only; manager → executive / department manager / employee; others → employee | `_ALLOWED_LEVELS` |
| Department managers need a real department | `put_org_profile` |
| "Reports to" is an active same-company member, never themselves, never a loop | `_creates_loop` |
| Can't remove someone who still has people reporting to them | `delete_org_profile` |
| Every change is audited with before/after | `lola_org_profile_updated` / `_removed` |
| Takes effect on the person's next request (Lola re-reads it every time) | `build_principal` |

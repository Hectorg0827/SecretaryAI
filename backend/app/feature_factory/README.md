# SecretaryAI · Feature Factory

The self-extending feature subsystem. A logged-in user describes a feature in
plain English ("flag any invoice over $5,000"), and SecretaryAI safely builds,
reviews, previews, and runs it — under eight layers of defense.

This module is **fully additive**. It introduces new files and new database
tables (all prefixed `ff_`). It does not modify or remove anything you already
have. You can install it, and if you change your mind, drop the `ff_` tables and
remove the folder — nothing else is touched.

---

## What you (Hector) need to decide / confirm

There is exactly **one** thing to confirm before your developer wires this in:

> **What does your `database/schema.sql` call the customer/organization id and
> the user id?**

This module uses `tenant_id` (the customer/org) and `user_id` (the person).
If your schema uses different names (`org_id`, `organization_id`, etc.), your
developer changes them in **one** place each — they're all marked with
`-- CONFIRM:` in the migration and `SEAM:` in the router. That's the whole
integration risk.

---

## The 8 security layers → where each one lives

| # | Layer | What it does | File |
|---|-------|--------------|------|
| 1 | Capabilities, not code | A closed allow-list of what any feature can ever do | `app/feature_factory/capabilities.py` |
| 2 | Human-only generation | Features can only be built by a logged-in user via the API — never by ingested email/data (kills prompt-injection) | `app/feature_factory/router.py` + `service.py` |
| 3 | Static validation | Machine proves the spec can't exceed approved capabilities | `app/feature_factory/validator.py` |
| 4 | Tenant isolation | Every query is locked to one tenant; cross-tenant access is impossible | `app/feature_factory/repository.py` |
| 5 | Dry-run preview | Shows what it *would* do, changes nothing | `app/feature_factory/engine.py` + `DryRunPreview.tsx` |
| 6 | Progressive trust | Features watch (observe) and earn the right to act | `engine.py` + `service.py` + `FeatureRegistryPage.tsx` |
| 7 | Circuit breaker + kill switch | Auto-disable on repeated failure; one-tap master stop | `service.py` + `FeatureRegistryPage.tsx` |
| 8 | Hash-chained audit log | Append-only, tamper-evident record of everything | `app/feature_factory/audit.py` + migration trigger |

---

## Install in 5 steps

### 1. Run the database migration
Paste `database/migrations/001_feature_factory.sql` into the Supabase SQL editor
and run it. (Or `psql "$DATABASE_URL" -f 001_feature_factory.sql`.)
First, do a find for `CONFIRM:` and adjust the two foreign-key lines if your
tenant/user tables are named differently.

### 2. Copy the backend module
Drop the `app/feature_factory/` folder into your `backend/app/`, and
`tasks/feature_runs.py` into your `backend/tasks/`.

### 3. Mount the router (in `backend/app/main.py`)
```python
from app.feature_factory.router import router as feature_factory_router
app.include_router(feature_factory_router)
```

### 4. Wire the four seams (in `app/feature_factory/router.py`)
Search for `SEAM:` and replace the four `NotImplementedError` stubs with your
existing pieces:

```python
# SEAM 1 — your DB executor
async def get_db() -> DBExecutor:
    return your_existing_db_pool   # see adapter below

# SEAM 2 — the logged-in user's id (from your JWT/session)
async def get_current_user_id(user = Depends(your_auth)) -> UUID:
    return user.id

# SEAM 3 — the tenant id for this request
async def get_tenant_id(user = Depends(your_auth)) -> UUID:
    return user.tenant_id

# SEAM 4 — business data gateways (read your invoices/customers; perform effects)
```

**DB adapter (10 lines).** The module talks to Postgres through a tiny
`fetch / fetchrow / execute` interface. If you use `asyncpg`:
```python
class AsyncpgExecutor:
    def __init__(self, pool): self._pool = pool
    async def fetch(self, q, *a):    return [dict(r) for r in await self._pool.fetch(q, *a)]
    async def fetchrow(self, q, *a): r = await self._pool.fetchrow(q, *a); return dict(r) if r else None
    async def execute(self, q, *a):  return await self._pool.execute(q, *a)
```

**Business gateways.** Implement the two protocols in `engine.py` against your
real tables. Minimal example:
```python
class MyDataGateway:
    def __init__(self, db): self._db = db
    async def fetch_records(self, entity, source_filter, tenant_id):
        if entity == "invoices":
            return await self._db.fetch(
                "SELECT id, ref, amount FROM invoices WHERE tenant_id = $1", tenant_id)
        # ... customers, orders, inventory, payments
        return []

class MyEffectExecutor:
    def __init__(self, db): self._db = db
    async def perform(self, action_type, params, record, tenant_id):
        if action_type == "flag":
            await self._db.execute(
                "INSERT INTO record_flags (tenant_id, ref, note) VALUES ($1,$2,$3)",
                tenant_id, record.get("ref"), params.get("note", ""))
        # ... notify, create_task, draft_email, tag, summarize
        return {"ok": True}
```

### 5. Schedule the runner (Celery beat)
In your `celery_app.py` beat schedule:
```python
celery_app.conf.beat_schedule["feature-factory-tick"] = {
    "task": "tasks.feature_runs.tick_scheduled_features",
    "schedule": 60.0,   # every minute; the task decides what's actually due
}
```
Then finish the two `CONFIRM:` spots in `tasks/feature_runs.py` so the worker can
build a DB executor + gateways (same objects as step 4).

### Frontend
Copy `frontend/src/features/featureFactory/` into your app and route to the
pages:
- `FeatureBuilderPage` — build a new feature
- `FeatureRegistryPage` — manage features + kill switch
- `FeatureAuditPage` — per-feature history & approvals

They use your existing `VITE_API_URL` and send the session cookie
(`credentials: "include"`). Styling is Tailwind, matching your stack.

---

## How a feature flows (the safe path)

```
User types request
   │
   ▼
POST /features ─ interpret (Claude) ─ validate (machine) ─► stored as "pending"
   │                                                          (Layer 2,3)
   ▼
User approves the plain-English capability list  ─► grants saved (Layer 1)
   │
   ▼
Dry run: "here's what I'd do" ─ changes nothing ─► user says "looks right" (Layer 5)
   │
   ▼
OBSERVE: runs on schedule, logs intentions only ─ earns trust (Layer 6)
   │   (after N clean runs, user may promote)
   ▼
ACTIVE: acts for real ─ but commit/destructive actions wait for approval
   │                                                   (Layers 5,7)
   ▼
Every step written to the tamper-evident audit log (Layer 8)
```

If anything misbehaves, the circuit breaker auto-disables the feature, and the
tenant kill switch can stop everything instantly (Layer 7).

---

## What is intentionally NOT possible at Tier 1
By design, generated features **cannot**: delete data, reach the internet,
write to QuickBooks, run arbitrary code, or touch another customer's data.
Those are not "blocked" — they simply don't exist in the capability allow-list.
Tier 2 (sandboxed micro-plugins) and Tier 3 (full generation + review) are
gated off by default in `ff_tenant_settings` and are the next phase, not this one.

---

## A note on models
`interpreter.py` uses `claude-opus-4-6` as a placeholder. Set it to whatever
model your backend standardizes on, and read the key from your existing config.

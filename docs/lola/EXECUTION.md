# Lola approved-action execution — operator runbook

Lola prepares customer emails and QuickBooks purchase orders. **Nothing is sent
or created until a permitted person approves it AND an operator has switched
the executor on.** Every switch below is off by default.

## How an action flows

```
Lola drafts ─► approval queue (drafts) ─► human approves ─► ONE database transaction:
                                                             approve + queue + frozen copy
           ─► executor worker claims it once ─► Gmail/Outlook or QuickBooks Online
           ─► ledger "completed" + provider id ─► Lola receipt "Done · verified"
```

| Safety rule | Where it is enforced |
|---|---|
| Email recipient comes only from the customer record on file; chat text can't add addresses, links or CCs | `app/lola/drafting.py` |
| Approvers can polish wording / add a missing PO vendor, never change recipient, PO lines or totals | `check_lola_approval` + SQL total check |
| Approved content is frozen; the worker sends the frozen copy, never the editable draft | `agent_execution_email_payloads`, `agent_execution_po_payloads` |
| Each job is claimed at most once; replays do nothing | `claim_agent_execution` |
| Purchase orders use Intuit's `requestid`, so a repeated create cannot duplicate a PO | `qb_online._post` |
| QuickBooks Desktop POs are never automated (no idempotent create) | `po_execution_target` |
| "Done · verified" only with a provider id (Gmail id, Graph request id, QuickBooks PO id) | `finish_agent_execution` + receipt CHECK |
| Unclear outcome → "needs reconciliation", never retried automatically | `lola_executor.py`, Celery `max_retries=0` |

## Turning it on — staging first

1. Apply migrations `20261109120000_lola_core.sql` and `20261109130000_lola_purchase_order_execution.sql`.
2. Give the `secretary_executor` role a login (out of band) and set `SECRETARYAI_EXECUTION_DATABASE_URL`.
3. `SECRETARYAI_DURABLE_APPROVAL_ENABLED=1` (approvals now queue).
4. Email pilot: `SECRETARYAI_DEPLOYMENT_ENV=staging`, `SECRETARYAI_STAGING_EMAIL_PILOT_ENABLED=1`,
   `SECRETARYAI_STAGING_EMAIL_RECIPIENT=<your test mailbox>`, `SECRETARYAI_EMAIL_DELIVERY_MODE=live`,
   `SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED=1`. Only that one mailbox can ever receive mail; the database
   refuses to claim anything else.
5. Purchase orders: connect an **Intuit sandbox** company and set `SECRETARYAI_PO_EXECUTION_MODE=sandbox`.
6. Finally `SECRETARYAI_LOLA_EXECUTOR_ENABLED=1` and run a Celery worker (`tasks.lola_execution`).

Production needs the same switches without the staging pilot, plus
`SECRETARYAI_LIVE_PO_CREATION_APPROVED=1` and `SECRETARYAI_PO_EXECUTION_MODE=live` for real POs —
a business decision, not a code change.

## When something needs reconciliation

The receipt shows "needs a human check". Look in Gmail "Sent" / Outlook / QuickBooks for the item
(the PO memo contains the draft id; the request id is `lola-<execution id>`). Requeueing requires a
reviewed database procedure by design — there is no button that resends.

## Tests

`backend/tests/test_lola_executor.py` (unit), `scripts/test_lola_po_execution.sql` (SQL),
`scripts/test_lola_executor_integration.py` (real PostgreSQL as `secretary_executor`, fake providers).

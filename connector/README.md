# Local QuickBooks Desktop connector — operator use only

**Do not hand this to customers.**

This script talks to Conductor (the QuickBooks Desktop bridge) using
`CONDUCTOR_API_KEY`. That key is the operator's master key: together with any
company's end-user ID it reaches that company's QuickBooks. A copy of this
connector on a customer's PC would put the master key on that PC.

Customers connect QuickBooks Desktop through the **setup wizard in the desktop
app** (Settings → QuickBooks Desktop → Set up). That flow uses Conductor's own
installer on the customer's machine and never exposes the operator key; the
cloud backend then reads QuickBooks through Conductor directly. Nothing in the
product requires this connector.

It remains in the repository for operator-side use (for example a self-hosted
deployment where the operator runs it on their own server):

- read-only: it only fetches customers, invoices, inventory, purchase orders,
  payments and vendors — it never changes QuickBooks data;
- it registers with a single-use install secret (Settings → Connectors) that
  expires after 24 hours and is stored server-side only as a SHA-256 digest;
- its connector token is saved to `connector_state.json`, readable by the
  owner of the process only.

Run from the `backend/` directory so the QuickBooks adapter is importable:

```bash
cd backend && python -m connector.main   # with the repo root on PYTHONPATH
```

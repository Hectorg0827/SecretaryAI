"""
API Documentation helpers.

Exposes /api/docs/openapi.json with enriched metadata,
and /api/docs/redoc for a human-friendly reference.
"""
from fastapi import APIRouter
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()


OPENAPI_DESCRIPTION = """
## SecretaryAI API

The SecretaryAI backend provides:

### Core
- **Auth** — JWT login, refresh, RBAC role enforcement
- **Dashboard** — Snapshot summaries, KPI stats, proactive feed
- **Chat** — Multi-turn AI assistant (Claude-powered) with memory
- **Inbox** — Email read/reply/compose via Gmail OAuth

### Business Intelligence
- **Accounts** — Customer health scoring, churn detection
- **Inventory** — QB-synced inventory with reorder alerts
- **Actions** — AI-generated recommendation queue (approve / reject)
- **Feed** — Proactive intelligence feed (opportunities, risks)
- **Workflows** — Automated multi-step business workflows

### Logistics & Procurement (9-Stage Pipeline)
- **Reorder Intelligence** — Demand monitoring, urgency scoring, safety stock
- **PO Generation** — Container-optimized purchase orders with email bodies
- **Vendor Tracking** — Response parsing (EN + ES), escalation management
- **Freight Capture** — Invoice parsing, cost component allocation
- **Transit Tracking** — Vessel tracking, delay detection, inventory impact
- **Customs Clearance** — Milestone logging, demurrage risk, broker email parsing
- **Warehouse Receipt** — Lead time logging, shortage detection
- **Cost Reconciliation** — Variance detection, annualized impact, margin protection
- **Learning Engine** — Lead time stats, supplier scoring, seasonal indices

### Administration
- **Settings** — Company config, integrations, notification preferences
- **Billing** — Subscription management, Stripe checkout, invoice history
- **Setup** — Onboarding wizard, QuickBooks Desktop connection

### Integrations (webhooks)
- QuickBooks Online (data sync)
- Stripe (subscription lifecycle)
- Gmail (email events)

---

### Authentication
All endpoints (except `/auth/login`, `/auth/register`, `/api/billing/plans`)
require a **Bearer JWT** in the `Authorization` header.

```
Authorization: Bearer <your_jwt_token>
```

Obtain tokens via `POST /auth/login`.

### Rate Limits
| Endpoint Group | Limit |
|----------------|-------|
| `/api/chat` | 20 req/min |
| `/api/agent` | 30 req/min |
| `/api/logistics` | 60 req/min |
| All others | 120 req/min |

### Roles & Permissions
| Role | Key Permissions |
|------|----------------|
| `owner` | Full access including billing |
| `manager` | All operational + settings |
| `sales_rep` | Chat, accounts, inventory (read) |
| `back_office` | Inbox, actions, inventory |
| `viewer` | Dashboard only (read-only) |
"""

OPENAPI_TAGS = [
    {"name": "auth",         "description": "Authentication — login, refresh, user profile"},
    {"name": "dashboard",    "description": "KPI snapshots and proactive intelligence cards"},
    {"name": "chat",         "description": "AI assistant (Claude) — multi-turn with memory"},
    {"name": "inbox",        "description": "Gmail-connected email inbox management"},
    {"name": "accounts",     "description": "Customer account health and churn analytics"},
    {"name": "inventory",    "description": "QuickBooks-synced inventory with reorder alerts"},
    {"name": "actions",      "description": "AI recommendation queue — approve or reject"},
    {"name": "feed",         "description": "Proactive opportunity and risk feed"},
    {"name": "workflows",    "description": "Automated multi-step business workflow engine"},
    {"name": "logistics",    "description": "9-stage procurement pipeline — demand → warehouse"},
    {"name": "billing",      "description": "Subscription plans, Stripe checkout, invoice history"},
    {"name": "settings",     "description": "Company config, integrations, notification preferences"},
    {"name": "setup",        "description": "Onboarding wizard and QuickBooks Desktop connection"},
    {"name": "agent",        "description": "AI agent status and control"},
    {"name": "webhooks",     "description": "Inbound webhooks: QuickBooks, Stripe, Gmail"},
    {"name": "computer-use", "description": "Browser automation (Playwright) for QB Desktop"},
]


@router.get("/openapi.json", include_in_schema=False)
async def custom_openapi(request=None):
    """Serve enriched OpenAPI schema."""
    # Import here to avoid circular at startup
    from app.main import app as fastapi_app
    schema = get_openapi(
        title="SecretaryAI API",
        version="2.0.0",
        description=OPENAPI_DESCRIPTION,
        routes=fastapi_app.routes,
        tags=OPENAPI_TAGS,
    )
    schema["info"]["contact"] = {
        "name": "SecretaryAI Support",
        "url": "https://secretaryai.com",
    }
    schema["info"]["x-logo"] = {"url": "/static/logo.png"}
    return JSONResponse(schema)


@router.get("/", include_in_schema=False)
async def redoc_ui():
    """Serve ReDoc-based API reference."""
    return HTMLResponse("""<!DOCTYPE html>
<html>
  <head>
    <title>SecretaryAI API Reference</title>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
    <style>body { margin: 0; padding: 0; }</style>
  </head>
  <body>
    <redoc spec-url='/api/docs/openapi.json'
           expand-responses="200,201"
           hide-download-button
           theme='{"colors":{"primary":{"main":"#2563EB"}},"typography":{"fontSize":"14px","fontFamily":"Roboto, sans-serif"}}'
    ></redoc>
    <script src="https://cdn.jsdelivr.net/npm/redoc@latest/bundles/redoc.standalone.js"></script>
  </body>
</html>""")

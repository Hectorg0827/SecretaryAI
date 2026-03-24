"""
QB Desktop onboarding — agentic setup flow.

Endpoints:
  POST /api/setup/qb-desktop/start    — create Conductor EndUser, return authFlowUrl
  GET  /api/setup/qb-desktop/status   — poll connection health
  POST /api/setup/qb-desktop/guidance — screenshot → Claude vision → plain-English help text
"""
import base64
import logging
import uuid
from typing import Optional

import anthropic
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_db
from app.auth.rbac import require_permission
from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

# ── Conductor helpers ──────────────────────────────────────────────────────────

CONDUCTOR_BASE = "https://api.conductor.is/v1"


def _conductor_headers() -> dict:
    return {"Authorization": f"Bearer {settings.conductor_api_key}"}


async def _conductor_post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{CONDUCTOR_BASE}{path}", json=body, headers=_conductor_headers()
        )
        r.raise_for_status()
        return r.json()


async def _conductor_get(path: str, params: Optional[dict] = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{CONDUCTOR_BASE}{path}",
            params=params or {},
            headers=_conductor_headers(),
        )
        r.raise_for_status()
        return r.json()


# ── Request / response models ──────────────────────────────────────────────────

class StartSetupResponse(BaseModel):
    auth_flow_url: str
    end_user_id: str
    connection_id: str


class StatusResponse(BaseModel):
    connected: bool
    status: str  # "connected" | "pending" | "error"
    message: str


class GuidanceRequest(BaseModel):
    screenshot_b64: str  # PNG encoded as base64
    step: str            # human-readable description of what we expect on screen


class GuidanceResponse(BaseModel):
    instruction: str     # plain-English next-step instruction for the user
    detected_state: str  # what Claude sees on screen
    needs_action: bool   # true if user still has something to click/do


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/qb-desktop/start", response_model=StartSetupResponse)
async def start_qb_desktop_setup(
    user: dict = Depends(require_permission("manage_users")),
    db=Depends(get_db),
):
    """
    Begin agentic QB Desktop onboarding.

    1. Creates (or reuses) a Conductor EndUser for this company.
    2. Creates an auth session which returns the authFlowUrl that the desktop
       app opens in a webview — Conductor's UI guides the user through the
       .QWC file download and Web Connector authorization.
    3. Persists the end_user_id on the companies row so future API calls can
       route requests to the right QuickBooks instance.
    """
    company_id = user["company_id"]

    if not settings.conductor_api_key:
        raise HTTPException(
            status_code=503,
            detail="Conductor API key not configured. Add CONDUCTOR_API_KEY to your environment.",
        )

    # Check if we already have a Conductor end_user_id stored
    try:
        row = (
            db.table("companies")
            .select("conductor_end_user_id")
            .eq("id", company_id)
            .single()
            .execute()
        )
        existing_end_user_id: Optional[str] = (row.data or {}).get("conductor_end_user_id")
    except Exception:
        existing_end_user_id = None

    # Create EndUser if we don't have one
    if existing_end_user_id:
        end_user_id = existing_end_user_id
        log.info("Reusing existing Conductor EndUser %s for company %s", end_user_id, company_id)
    else:
        try:
            end_user_data = await _conductor_post(
                "/end-users",
                {
                    "sourceId": company_id,
                    "email": user.get("email", f"company-{company_id}@secretaryai.com"),
                    "companyName": user.get("company_name", "SecretaryAI Customer"),
                },
            )
            end_user_id = end_user_data["id"]
            # Persist so we don't create duplicates on retry
            db.table("companies").update(
                {"conductor_end_user_id": end_user_id}
            ).eq("id", company_id).execute()
            log.info("Created Conductor EndUser %s for company %s", end_user_id, company_id)
        except httpx.HTTPStatusError as exc:
            log.error("Conductor EndUser creation failed: %s", exc.response.text)
            raise HTTPException(
                status_code=502, detail="Failed to create QuickBooks connection. Check Conductor API key."
            )

    # Create an auth session — this gives us the authFlowUrl
    try:
        session_data = await _conductor_post(
            f"/end-users/{end_user_id}/auth-sessions",
            {"integrationSlug": "quickbooks-desktop"},
        )
        auth_flow_url = session_data["authFlowUrl"]
        connection_id = session_data.get("connectionId", "")
    except httpx.HTTPStatusError as exc:
        log.error("Conductor auth session creation failed: %s", exc.response.text)
        raise HTTPException(status_code=502, detail="Failed to start QuickBooks auth flow.")

    return StartSetupResponse(
        auth_flow_url=auth_flow_url,
        end_user_id=end_user_id,
        connection_id=connection_id,
    )


@router.get("/qb-desktop/status", response_model=StatusResponse)
async def get_qb_desktop_status(
    user: dict = Depends(require_permission("manage_users")),
    db=Depends(get_db),
):
    """
    Poll Conductor to check whether the Web Connector is active and syncing.
    The desktop wizard calls this every 5 s while waiting for the user to
    complete the authorization step in QuickBooks.
    """
    company_id = user["company_id"]

    if not settings.conductor_api_key:
        return StatusResponse(connected=False, status="error", message="Conductor not configured.")

    try:
        row = (
            db.table("companies")
            .select("conductor_end_user_id")
            .eq("id", company_id)
            .single()
            .execute()
        )
        end_user_id: Optional[str] = (row.data or {}).get("conductor_end_user_id")
    except Exception:
        end_user_id = None

    if not end_user_id:
        return StatusResponse(
            connected=False,
            status="pending",
            message="Setup not started. Open the SecretaryAI desktop app to connect QuickBooks.",
        )

    # Ask Conductor for integration connections for this end-user
    try:
        data = await _conductor_get(f"/end-users/{end_user_id}/integration-connections")
        connections = data.get("data", [])
        qbd_conn = next(
            (c for c in connections if c.get("integrationSlug") == "quickbooks-desktop"),
            None,
        )
        if qbd_conn and qbd_conn.get("status") == "active":
            return StatusResponse(
                connected=True,
                status="connected",
                message="QuickBooks Desktop is connected and syncing.",
            )
        elif qbd_conn:
            return StatusResponse(
                connected=False,
                status="pending",
                message="QuickBooks is set up but waiting for the first sync.",
            )
        else:
            return StatusResponse(
                connected=False,
                status="pending",
                message="Waiting for QuickBooks authorization...",
            )
    except httpx.HTTPStatusError as exc:
        log.warning("Conductor status check failed: %s", exc.response.text)
        return StatusResponse(
            connected=False,
            status="error",
            message="Could not reach QuickBooks. Check that it is open and the Web Connector is running.",
        )


@router.post("/qb-desktop/guidance", response_model=GuidanceResponse)
async def get_setup_guidance(
    body: GuidanceRequest,
    user: dict = Depends(require_permission("manage_users")),
):
    """
    Agentic visual guidance: the desktop app sends a screenshot and we use
    Claude vision to detect the current state and return a plain-English
    instruction for the (non-technical) user.

    This is the AI-assisted fallback when the user stalls during setup.
    We never click anything — we only look and explain.
    """
    try:
        # Validate it's valid base64
        image_bytes = base64.b64decode(body.screenshot_b64)
        if len(image_bytes) < 100:
            raise ValueError("Screenshot too small")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid screenshot data.")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    prompt = f"""You are helping a small business owner connect QuickBooks Desktop to SecretaryAI.
They are non-technical. Look at their screen and give them one clear, friendly instruction.

Current setup step: {body.step}

Analyze the screenshot and respond with JSON:
{{
  "detected_state": "<one sentence describing exactly what you see on screen>",
  "instruction": "<one friendly sentence telling them exactly what to click or do next>",
  "needs_action": <true if they still need to do something, false if this step looks complete>
}}

Common states to recognize:
- QuickBooks authorization dialog: "Do you want to allow [app] to access QuickBooks?"
  → Tell them: "Click 'Yes, always; allow access even if QuickBooks is not running'"
- Web Connector password prompt
  → Tell them to enter the password shown in the setup window
- Web Connector checkbox list showing SecretaryAI
  → Tell them to check the box next to SecretaryAI and click 'Update Selected'
- Setup complete / connected indicator
  → Tell them they're all done

Respond ONLY with valid JSON, no markdown."""

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",  # Fast + cheap for vision guidance
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": body.screenshot_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        import json as json_lib
        text = response.content[0].text.strip()
        parsed = json_lib.loads(text)
        return GuidanceResponse(
            instruction=parsed.get("instruction", "Please follow the instructions on screen."),
            detected_state=parsed.get("detected_state", ""),
            needs_action=bool(parsed.get("needs_action", True)),
        )

    except Exception as exc:
        log.error("Guidance vision call failed: %s", exc)
        # Graceful fallback — don't break the wizard
        return GuidanceResponse(
            instruction="Please follow the prompts shown in QuickBooks. "
                        "If you see a permission dialog, choose 'Yes, always allow access'.",
            detected_state="Unable to analyze screen.",
            needs_action=True,
        )

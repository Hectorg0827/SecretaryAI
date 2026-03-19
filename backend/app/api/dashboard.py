from fastapi import APIRouter, Depends
from app.auth.rbac import get_current_user

router = APIRouter()


@router.get("/summary")
async def get_dashboard_summary(user: dict = Depends(get_current_user)):
    """High-level dashboard: account health distribution, inventory alerts, recent activity."""
    company_id = user["company_id"]
    # TODO: Query from Supabase
    return {
        "company_id": company_id,
        "accounts": {"healthy": 0, "slowing": 0, "at_risk": 0, "dormant": 0},
        "inventory_alerts": [],
        "recent_activity": [],
    }

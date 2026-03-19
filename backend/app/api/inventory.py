from fastapi import APIRouter, Depends
from app.auth.rbac import get_current_user

router = APIRouter()


@router.get("/")
async def list_inventory(user: dict = Depends(get_current_user)):
    company_id = user["company_id"]
    # TODO: Query from Supabase with RLS
    return {"inventory": [], "company_id": company_id}


@router.get("/alerts")
async def get_inventory_alerts(user: dict = Depends(get_current_user)):
    """Returns only items with low or critical stock status."""
    company_id = user["company_id"]
    # TODO: Filter by stock_status IN ('low', 'critical', 'out_of_stock')
    return {"alerts": [], "company_id": company_id}

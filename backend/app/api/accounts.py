from fastapi import APIRouter, Depends
from app.auth.rbac import get_current_user

router = APIRouter()


@router.get("/")
async def list_accounts(user: dict = Depends(get_current_user)):
    company_id = user["company_id"]
    # TODO: Query from Supabase with RLS
    return {"accounts": [], "company_id": company_id}


@router.get("/{account_id}")
async def get_account(account_id: str, user: dict = Depends(get_current_user)):
    # TODO: Query account + recent orders
    return {"account_id": account_id}

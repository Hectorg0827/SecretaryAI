"""
Chat API endpoints — conversational AI interface.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.rbac import get_current_user
from app.ai.secretary import classify_intent, stream_chat
from app.ai.data_summarizer import build_query_context
from fastapi.responses import StreamingResponse

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


@router.post("/message")
async def send_message(
    request: ChatRequest,
    user: dict = Depends(get_current_user),
):
    """
    Send a message to SecretaryAI and get a streaming response.
    The AI receives processed data summaries — never raw DB records.
    """
    company_id = user["company_id"]

    # TODO: Load conversation history from DB
    history: list[dict] = []

    # Classify intent to fetch appropriate data
    intent = await classify_intent(request.message)

    # TODO: Fetch real data based on intent and company_id
    data: dict = {}
    data_summary = build_query_context(intent, data)

    company_context = {
        "company_name": user.get("company_name", "your company"),
        "business_type": user.get("business_type", "importer/distributor"),
        "user_role": user.get("role", "viewer"),
        "timezone": user.get("timezone", "America/New_York"),
        "preferred_language": user.get("preferred_language", "English"),
    }

    async def generate():
        async for chunk in stream_chat(
            request.message, history, data_summary, company_context
        ):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

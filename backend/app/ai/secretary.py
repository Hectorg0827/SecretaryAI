"""
Main conversational AI handler.
Orchestrates intent classification → data summarization → Claude API call → action detection.
"""
import json
from typing import AsyncGenerator, Optional

import anthropic

from app.config import get_settings
from app.ai.system_prompts import SECRETARY_SYSTEM_PROMPT, INTENT_CLASSIFIER_PROMPT

settings = get_settings()
_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


async def classify_intent(message: str) -> str:
    prompt = INTENT_CLASSIFIER_PROMPT.format(message=message)
    response = await _client.messages.create(
        model=settings.claude_model,
        max_tokens=20,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip().lower()


async def chat(
    user_message: str,
    conversation_history: list[dict],
    data_summary: str,
    company_context: dict,
) -> str:
    """
    Single-turn AI response with full conversation history.

    company_context keys: company_name, business_type, user_role, timezone, preferred_language
    """
    system_prompt = SECRETARY_SYSTEM_PROMPT.format(**company_context)

    # Inject the current data summary as the first user turn context
    augmented_history = list(conversation_history)
    augmented_message = f"[Context data for this query]\n{data_summary}\n\n[User message]\n{user_message}"

    messages = augmented_history + [{"role": "user", "content": augmented_message}]

    response = await _client.messages.create(
        model=settings.claude_model,
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    )
    return response.content[0].text


async def stream_chat(
    user_message: str,
    conversation_history: list[dict],
    data_summary: str,
    company_context: dict,
) -> AsyncGenerator[str, None]:
    """
    Streaming version — yields text chunks as they arrive from Claude.
    Use with Server-Sent Events or WebSocket for real-time UX.
    """
    system_prompt = SECRETARY_SYSTEM_PROMPT.format(**company_context)
    augmented_message = f"[Context data for this query]\n{data_summary}\n\n[User message]\n{user_message}"
    messages = list(conversation_history) + [{"role": "user", "content": augmented_message}]

    async with _client.messages.stream(
        model=settings.claude_model,
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text


def detect_action_in_response(ai_response: str) -> Optional[dict]:
    """
    Look for action proposals in the AI's response.
    Returns action metadata if found, None otherwise.
    """
    action_keywords = {
        "draft_email": ["draft an email", "send an email", "write to", "email them"],
        "draft_po": ["draft a PO", "purchase order", "place an order", "reorder"],
        "generate_report": ["generate a report", "create a report", "export"],
        "send_alert": ["send an alert", "notify", "flag this"],
    }

    lower_response = ai_response.lower()
    for action_type, keywords in action_keywords.items():
        if any(kw in lower_response for kw in keywords):
            return {"action_type": action_type, "raw_response": ai_response}
    return None

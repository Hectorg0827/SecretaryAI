"""
Main conversational AI handler.
Orchestrates intent classification → data summarization → Claude API call → action detection.

Industry module is loaded per company — the AI automatically speaks the right language,
uses the right terminology, and understands the right compliance context for each industry.
"""
from typing import AsyncGenerator, Optional

import anthropic

from app.config import get_settings
from app.ai.model_router import model_for
from app.ai.system_prompts import (
    build_secretary_prompt,
    build_intent_classifier_prompt,
    SECRETARY_SYSTEM_PROMPT,
    INTENT_CLASSIFIER_PROMPT,
)

settings = get_settings()
_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _get_module(company_config: Optional[dict] = None):
    """Load the industry module for a company, falling back to system default."""
    from app.industry_modules.loader import get_module_for_company, get_module
    if company_config:
        return get_module_for_company(company_config)
    return get_module(settings.default_industry_module)


async def classify_intent(message: str, company_config: Optional[dict] = None) -> str:
    """
    Classify the user's message intent using industry-aware categories.
    Accepts an optional company_config to load the appropriate industry module.
    """
    module = _get_module(company_config)
    prompt = build_intent_classifier_prompt(message, module)
    response = await _client.messages.create(
        model=model_for("classify_intent"),
        max_tokens=20,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip().lower()


async def chat(
    user_message: str,
    conversation_history: list[dict],
    data_summary: str,
    company_context: dict,
    company_config: Optional[dict] = None,
) -> str:
    """
    Single-turn AI response with full conversation history.

    company_context keys: company_name, business_type, user_role, timezone, preferred_language
    company_config: full company record from DB (used to load industry module)
    """
    module = _get_module(company_config)
    system_prompt = build_secretary_prompt(company_context, module)

    augmented_message = f"[Context data for this query]\n{data_summary}\n\n[User message]\n{user_message}"
    messages = list(conversation_history) + [{"role": "user", "content": augmented_message}]

    response = await _client.messages.create(
        model=model_for("chat"),
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
    company_config: Optional[dict] = None,
) -> AsyncGenerator[str, None]:
    """
    Streaming version — yields text chunks as they arrive from Claude.
    Use with Server-Sent Events or WebSocket for real-time UX.
    """
    module = _get_module(company_config)
    system_prompt = build_secretary_prompt(company_context, module)
    augmented_message = f"[Context data for this query]\n{data_summary}\n\n[User message]\n{user_message}"
    messages = list(conversation_history) + [{"role": "user", "content": augmented_message}]

    async with _client.messages.stream(
        model=model_for("stream_chat"),
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
        "draft_po": ["draft a po", "purchase order", "place an order", "reorder"],
        "generate_report": ["generate a report", "create a report", "export"],
        "send_alert": ["send an alert", "notify", "flag this"],
    }

    lower_response = ai_response.lower()
    for action_type, keywords in action_keywords.items():
        if any(kw in lower_response for kw in keywords):
            return {"action_type": action_type, "raw_response": ai_response}
    return None

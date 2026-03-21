"""
Model router — maps task names to the correct Claude model tier.

Haiku  (claude-haiku-4-5)   Fast, cheap. Classification, batch summarization,
                             structured one-shot outputs, background briefings.

Sonnet (claude-sonnet-4-6)  Balanced. Interactive chat, complex multi-step
                             reasoning, leadership reports. The daily workhorse.

Opus                        Reserved — not used in standard flows.
                             Add a task to _TASK_MAP with OPUS if needed.

Usage:
    from app.ai.model_router import model_for
    model = model_for("classify_intent")   # → Haiku
    model = model_for("chat")              # → Sonnet
"""
from app.config import get_settings

HAIKU  = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

# ── Task → tier mapping ────────────────────────────────────────────────────────
_TASK_MAP: dict[str, str] = {
    # Haiku tier — fast, structured, low-latency or high-volume
    "classify_intent":        HAIKU,   # 6-label intent classifier, max_tokens=20
    "email_priority_batch":   HAIKU,   # batch triage up to 10 emails
    "morning_briefing":       HAIKU,   # daily structured summary, ~512 tokens
    "stock_alert_message":    HAIKU,   # fill-in-the-blank alert copy
    "detect_action_proposal": HAIKU,   # scan response for action keywords

    # Sonnet tier — nuanced reasoning, interactive, executive-level output
    "chat":                   SONNET,  # interactive conversational AI
    "stream_chat":            SONNET,  # streaming interactive chat
    "weekly_report":          SONNET,  # leadership report with trend analysis
    "draft_email_reply":      SONNET,  # tone-aware customer email drafts
    "demand_forecast":        SONNET,  # multi-variable inventory projections
    "anomaly_analysis":       SONNET,  # explain detected anomalies
}


def model_for(task: str) -> str:
    """
    Return the Claude model ID for the given task name.

    Falls back to Sonnet for unknown tasks.
    Respects CLAUDE_MODEL env var as a version pin for Sonnet-tier tasks only
    (e.g. pin to claude-sonnet-4-5 in staging without touching Haiku tasks).
    """
    model = _TASK_MAP.get(task, SONNET)

    # Honour operator version pin for Sonnet-tier tasks
    if model == SONNET:
        settings = get_settings()
        if settings.claude_model and settings.claude_model != SONNET:
            return settings.claude_model

    return model

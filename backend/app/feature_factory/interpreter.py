"""
SecretaryAI · Feature Factory — Interpreter
===========================================
Turns a user's plain-English request ("alert me when a customer hasn't ordered
in 90 days") into a structured, validated FeatureSpec + a declared capability
list + a plain-English description.

Critical safety properties:
  * The model is told the EXACT closed set of capabilities, source entities,
    and action types it may use. It cannot invent new ones; if it tries, the
    validator (Layer 3) rejects the output.
  * The model returns STRICT JSON only. We parse it into typed contracts; if it
    doesn't parse, we treat it as a failed interpretation, never as code.
  * The model is instructed to ask a clarifying question instead of guessing
    when the request is ambiguous. Guessing on someone's financial data is
    worse than asking.

This file does NOT execute anything and does NOT touch the database. It only
proposes. Generation can only be *triggered* by an authenticated human in the
UI (enforced in router.py / service.py) — never by ingested data. That is
Layer 2 (prompt-injection defense): data that flows in can never become an
instruction that builds a feature.
"""
from __future__ import annotations

import json
from typing import Optional

from anthropic import Anthropic

from . import capabilities as caps
from .contracts import InterpretedFeature

_MODEL = "claude-opus-4-6"  # CONFIRM: pick the model your backend standardizes on


def _build_system_prompt() -> str:
    """Construct the system prompt from the live allowlist so it never drifts."""
    cap_lines = "\n".join(
        f'  - "{c.capability_key}"  ({c.scope.value}) — {c.human_summary}'
        for c in caps.all_capabilities()
    )
    sources = ", ".join(sorted(caps.SOURCE_ENTITY_TO_READ_CAPABILITY.keys()))
    action_types = ", ".join(sorted(caps.ACTION_TYPE_TO_CAPABILITY.keys()))

    return f"""You are the Feature Builder for SecretaryAI, an operations copilot for
small importers and distributors. You convert a user's plain-English request
into a STRICT JSON feature specification.

You may ONLY use the following capabilities. Do not invent any others:
{cap_lines}

You may read from ONLY these source entities: {sources}
You may use ONLY these action types: {action_types}

RULES:
1. Output STRICT JSON only. No prose, no markdown, no backticks.
2. Use only the capabilities, sources, and action types listed above.
3. Declare every capability the feature needs in "declared_capabilities".
   - Reading a source needs that source's ".read" capability.
   - Each action needs its backing capability.
4. Set "scope" on each declared capability to exactly: read, draft, commit, or destructive.
5. If the request is ambiguous, vague, or could touch money/records in a way
   you are unsure about, DO NOT guess. Set "clarifying_question" to a single
   plain-English question and still return your best-effort draft.
6. Always set "tier" to 1 (this builder only produces rule-based features).
7. "description" must be one or two plain-English sentences a non-technical
   business owner can understand. Describe what it does AND what it will not do.
8. Set "confidence" between 0 and 1.

JSON shape:
{{
  "suggested_name": str,
  "description": str,
  "tier": 1,
  "spec": {{
    "trigger": {{ "kind": "manual|schedule|event", "cron": str|null, "event": str|null }},
    "source_entity": str,
    "source_filter": object,
    "conditions": [ {{ "field": str, "op": "<|<=|==|!=|>=|>|contains|in|older_than_days", "value": any }} ],
    "actions": [ {{ "type": str, "params": object }} ]
  }},
  "declared_capabilities": [
    {{ "capability_key": str, "resource": str, "scope": str, "human_summary": str }}
  ],
  "clarifying_question": str|null,
  "confidence": number
}}
"""


def interpret(request_text: str, client: Optional[Anthropic] = None) -> InterpretedFeature:
    """
    Ask Claude to turn the request into a structured feature.
    Raises ValueError if the model output cannot be parsed into our contract.
    """
    client = client or Anthropic()  # reads ANTHROPIC_API_KEY from env

    message = client.messages.create(
        model=_MODEL,
        max_tokens=1500,
        system=_build_system_prompt(),
        messages=[{"role": "user", "content": request_text.strip()}],
    )

    # Concatenate any text blocks the model returned.
    raw = "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    ).strip()

    # Defensive: strip accidental code fences before parsing.
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"interpreter did not return valid JSON: {exc}") from exc

    # Parse into our typed contract. Pydantic enforces the shape; anything
    # malformed raises here and is handled by the caller as a failed interpret.
    return InterpretedFeature.model_validate(data)

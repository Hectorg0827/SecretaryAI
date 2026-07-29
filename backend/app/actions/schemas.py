"""
Typed schemas for action payloads (#32).

Actions selected by the AI/automation layer carry a raw dict payload. Before an
action with side effects executes, its payload is validated against a typed
schema here — so e.g. `update_inventory_count` cannot write a non-numeric or
negative quantity, and a caller cannot smuggle arbitrary field values into a DB
write. Unmapped action types pass through unchanged (they are gated by the
autonomy rules + approval queue elsewhere).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError


class ActionValidationError(ValueError):
    """Raised when an action payload fails schema validation."""


class UpdateInventoryCount(BaseModel):
    item_id: str = Field(min_length=1)
    quantity: int = Field(ge=0)  # coerces "5" → 5; rejects negatives / non-numeric


# Map action_type → schema. Only actions with a strict, safety-relevant shape are
# listed; others intentionally pass through.
_ACTION_SCHEMAS: dict[str, type[BaseModel]] = {
    "update_inventory_count": UpdateInventoryCount,
}


def validate_action_payload(action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Validate + normalize a payload for the given action_type.

    Returns a dict with the validated (coerced) fields merged over the original
    payload; raises ActionValidationError on failure. Unmapped action types are
    returned unchanged.
    """
    model = _ACTION_SCHEMAS.get(action_type)
    if model is None:
        return payload
    if not isinstance(payload, dict):
        raise ActionValidationError(f"Payload for '{action_type}' must be an object")
    try:
        validated = model(**payload)
    except ValidationError as exc:
        raise ActionValidationError(f"Invalid payload for '{action_type}': {exc}") from exc
    # Keep any extra fields, but with the validated/coerced values winning.
    return {**payload, **validated.model_dump()}

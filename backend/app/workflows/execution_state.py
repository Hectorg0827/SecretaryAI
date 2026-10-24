"""State contract for durable, auditable AI-initiated business execution.

This module has no database writes or task dispatch. Storage and workers must
enforce these transitions atomically using a tenant-scoped execution record.
"""
from enum import Enum


class ExecutionState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_RECONCILIATION = "needs_reconciliation"


_TRANSITIONS = {
    ExecutionState.QUEUED: {ExecutionState.RUNNING, ExecutionState.FAILED},
    ExecutionState.RUNNING: {
        ExecutionState.COMPLETED,
        ExecutionState.FAILED,
        ExecutionState.NEEDS_RECONCILIATION,
    },
    ExecutionState.FAILED: {ExecutionState.QUEUED},
    ExecutionState.NEEDS_RECONCILIATION: {
        ExecutionState.COMPLETED,
        ExecutionState.FAILED,
    },
    ExecutionState.COMPLETED: set(),
}


def can_transition(current: ExecutionState, target: ExecutionState) -> bool:
    """Reject invalid transitions; completion is terminal."""
    return target in _TRANSITIONS[current]


def classify_interruption(*, external_effect_may_have_occurred: bool) -> ExecutionState:
    """Unknown external results must be reconciled, never blindly retried."""
    return (
        ExecutionState.NEEDS_RECONCILIATION
        if external_effect_may_have_occurred
        else ExecutionState.FAILED
    )


def can_requeue(
    current: ExecutionState, *, explicitly_authorized: bool,
    idempotency_verified: bool,
) -> bool:
    """No background auto-retry of uncertain external side effects."""
    return (
        current is ExecutionState.FAILED
        and explicitly_authorized
        and idempotency_verified
    )

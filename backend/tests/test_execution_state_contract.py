"""Durable execution safety contract regressions."""
import pytest

from app.workflows.execution_state import (
    ExecutionState as S, can_requeue, can_transition, classify_interruption,
)


@pytest.mark.parametrize("initial,next_state", [
    (S.QUEUED, S.RUNNING),
    (S.RUNNING, S.COMPLETED),
    (S.RUNNING, S.FAILED),
    (S.RUNNING, S.NEEDS_RECONCILIATION),
    (S.NEEDS_RECONCILIATION, S.COMPLETED),
])
def test_allowed_transitions(initial, next_state):
    assert can_transition(initial, next_state)


@pytest.mark.parametrize("initial,next_state", [
    (S.QUEUED, S.COMPLETED),
    (S.COMPLETED, S.QUEUED),
    (S.COMPLETED, S.RUNNING),
    (S.NEEDS_RECONCILIATION, S.QUEUED),
    (S.RUNNING, S.QUEUED),
])
def test_prohibited_transitions(initial, next_state):
    assert not can_transition(initial, next_state)


def test_unknown_external_result_requires_reconciliation():
    assert classify_interruption(external_effect_may_have_occurred=True) is S.NEEDS_RECONCILIATION
    assert classify_interruption(external_effect_may_have_occurred=False) is S.FAILED


@pytest.mark.parametrize("state", list(S))
def test_retry_requires_confirmed_failure_and_both_guards(state):
    expected = state is S.FAILED
    assert can_requeue(
        state, explicitly_authorized=True, idempotency_verified=True
    ) is expected
    assert not can_requeue(
        state, explicitly_authorized=False, idempotency_verified=True
    )
    assert not can_requeue(
        state, explicitly_authorized=True, idempotency_verified=False
    )

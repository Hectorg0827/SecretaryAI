"""Fail-closed, read-only cross-channel follow-up analysis for Lola.

No provider reads, notifications, scheduling, external writes or action
approvals. Inputs must come from authenticated, tenant-scoped adapters and a
verified work-item/contact mapping registry; names and message text cannot
establish a link across channels.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Sequence


class ContinuityState(str, Enum):
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    NO_VERIFIED_WORKFLOW = "NO_VERIFIED_WORKFLOW"
    NOT_DUE = "NOT_DUE"
    REPLIED = "REPLIED"
    REVIEW_FOLLOW_UP = "REVIEW_FOLLOW_UP"


class Channel(str, Enum):
    EMAIL = "email"
    WHATSAPP = "whatsapp_business"


class Activity(str, Enum):
    REQUEST_RECEIVED = "request_received"
    DRAFT_SENT = "draft_sent"
    REPLY_RECEIVED = "reply_received"


@dataclass(frozen=True)
class WorkScope:
    company_id: str
    employee_id: str
    contact_id: str
    work_item_id: str
    mapping_revision: str
    employee_consent_verified: bool


@dataclass(frozen=True)
class ChannelCoverage:
    company_id: str
    employee_id: str
    channel: Channel
    window_start: datetime
    complete_through: datetime
    complete: bool
    source_revision: str
    connector_authorized: bool


@dataclass(frozen=True)
class ActivityRecord:
    company_id: str
    employee_id: str
    contact_id: str
    work_item_id: str
    channel: Channel
    activity: Activity
    source_record_id: str
    source_revision: str
    observed_at: datetime
    source_authenticated: bool
    contact_mapping_verified: bool
    work_item_mapping_verified: bool
    # Outbound echo/provider confirmation, NOT a locally composed unsent draft.
    outbound_delivery_confirmed: bool = False


@dataclass(frozen=True)
class FollowUpPolicy:
    minimum_wait: timedelta = timedelta(hours=24)
    maximum_coverage_lag: timedelta = timedelta(minutes=10)
    max_source_age: timedelta = timedelta(days=60)
    max_records: int = 500

    def __post_init__(self) -> None:
        if (self.minimum_wait <= timedelta(0)
                or self.maximum_coverage_lag < timedelta(0)
                or self.max_source_age <= self.minimum_wait
                or not 1 <= self.max_records <= 10000):
            raise ValueError("Invalid continuity policy")


@dataclass(frozen=True)
class ContinuityDecision:
    state: ContinuityState
    reason: str
    work_item_id: str
    contact_id: str
    supporting_record_ids: tuple[str, ...]
    evidence_fingerprint: str | None
    follow_up_after: str | None
    checked_through: str | None = None
    requires_human_review: bool = False
    cross_channel_link_verified: bool = False
    messages_sent: bool = False
    notifications_sent: bool = False
    external_writes_executed: bool = False


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp is mandatory")
    return value.astimezone(timezone.utc)


def _identity(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("Missing or oversize identity")
    return value


def _unavailable(scope: WorkScope, reason: str) -> ContinuityDecision:
    return ContinuityDecision(
        state=ContinuityState.EVIDENCE_UNAVAILABLE,
        reason=reason, work_item_id=scope.work_item_id,
        contact_id=scope.contact_id, supporting_record_ids=(),
        evidence_fingerprint=None, follow_up_after=None,
    )


def evaluate_follow_up(
    scope: WorkScope,
    *,
    activities: Sequence[ActivityRecord],
    coverage: Sequence[ChannelCoverage],
    now: datetime,
    policy: FollowUpPolicy | None = None,
) -> ContinuityDecision:
    """Recommend human review only when cross-channel silence is proven.

    The absence of a reply cannot be inferred from a partial account history.
    Both authorized providers must attest complete event coverage from the
    initial request until the evaluation time.
    """
    cfg = policy or FollowUpPolicy()
    try:
        check_at = _utc(now)
        for identifier in (
            scope.company_id, scope.employee_id, scope.contact_id,
            scope.work_item_id, scope.mapping_revision,
        ):
            _identity(identifier)
        if scope.employee_consent_verified is not True:
            return _unavailable(scope, "Employee mapping or consent is unverified")
        if len(activities) > cfg.max_records or len(coverage) != 2:
            return _unavailable(scope, "Event or channel coverage scope unavailable")

        by_channel: dict[Channel, ChannelCoverage] = {}
        for item in coverage:
            if (not isinstance(item, ChannelCoverage)
                    or item.company_id != scope.company_id
                    or item.employee_id != scope.employee_id
                    or item.channel not in (Channel.EMAIL, Channel.WHATSAPP)
                    or item.channel in by_channel
                    or item.connector_authorized is not True
                    or item.complete is not True):
                return _unavailable(scope, "Channel authorization or sync incomplete")
            _identity(item.source_revision)
            start, end = _utc(item.window_start), _utc(item.complete_through)
            if start > end or end > check_at or check_at - end > cfg.maximum_coverage_lag:
                return _unavailable(scope, "Channel coverage does not include evaluation time")
            by_channel[item.channel] = item
        if set(by_channel) != {Channel.EMAIL, Channel.WHATSAPP}:
            return _unavailable(scope, "Both channels are required")

        relevant: list[ActivityRecord] = []
        seen: dict[tuple[str, str], ActivityRecord] = {}
        for event in activities:
            if (not isinstance(event, ActivityRecord)
                    or event.company_id != scope.company_id
                    or event.employee_id != scope.employee_id
                    or event.channel not in by_channel
                    or event.activity not in (Activity.REQUEST_RECEIVED, Activity.DRAFT_SENT, Activity.REPLY_RECEIVED)
                    or event.source_authenticated is not True
                    or event.contact_mapping_verified is not True
                    or event.work_item_mapping_verified is not True):
                return _unavailable(scope, "Untrusted source event or verified mapping absent")
            _identity(event.source_record_id)
            _identity(event.source_revision)
            observed = _utc(event.observed_at)
            window = by_channel[event.channel]
            if (observed > _utc(window.complete_through)
                    or observed < _utc(window.window_start)
                    or check_at - observed > cfg.max_source_age):
                return _unavailable(scope, "Event lies outside verified source coverage")
            key = (event.channel.value, event.source_record_id)
            previous = seen.setdefault(key, event)
            if previous != event:
                return _unavailable(scope, "Duplicate provider ID has conflicting data")
            if event.work_item_id == scope.work_item_id:
                if event.contact_id != scope.contact_id:
                    return _unavailable(scope, "Conflicting contact identity for this work item")
                relevant.append(event)

        relevant = sorted(set(relevant), key=lambda e: (_utc(e.observed_at), e.source_record_id))
        requests = [
            e for e in relevant
            if e.channel == Channel.EMAIL and e.activity == Activity.REQUEST_RECEIVED
        ]
        sent = [
            e for e in relevant
            if e.channel == Channel.WHATSAPP and e.activity == Activity.DRAFT_SENT
            and e.outbound_delivery_confirmed is True
        ]
        if not requests or not sent:
            return ContinuityDecision(
                state=ContinuityState.NO_VERIFIED_WORKFLOW,
                reason="No verified email request and confirmed WhatsApp draft send",
                work_item_id=scope.work_item_id, contact_id=scope.contact_id,
                supporting_record_ids=(), evidence_fingerprint=None, follow_up_after=None,
            )
        last_sent = max(sent, key=lambda e: _utc(e.observed_at))
        earlier_requests = [
            e for e in requests if _utc(e.observed_at) <= _utc(last_sent.observed_at)
        ]
        if not earlier_requests:
            return _unavailable(scope, "Draft send lacks a prior verified request")
        first_request = min(earlier_requests, key=lambda e: _utc(e.observed_at))
        first_at = _utc(first_request.observed_at)
        if any(_utc(c.window_start) > first_at for c in by_channel.values()):
            return _unavailable(scope, "Message history began after initial request")
        replies = [
            e for e in relevant
            if e.activity == Activity.REPLY_RECEIVED
            and _utc(e.observed_at) > _utc(last_sent.observed_at)
        ]
        evidence = [first_request, last_sent] + sorted(replies, key=lambda e: _utc(e.observed_at))
        data = (
            scope.company_id, scope.employee_id, scope.contact_id,
            scope.work_item_id, scope.mapping_revision,
            *(f"{e.channel.value}:{e.source_record_id}:{e.source_revision}:{e.activity.value}:{_utc(e.observed_at).isoformat()}" for e in evidence),
            *(f"{c.channel.value}:{c.source_revision}" for c in sorted(
                coverage, key=lambda c: c.channel.value)),
        )
        fingerprint = hashlib.sha256("\n".join(data).encode("utf-8")).hexdigest()
        due = _utc(last_sent.observed_at) + cfg.minimum_wait
        checked_through = min(_utc(x.complete_through) for x in coverage)
        if replies:
            state, reason = ContinuityState.REPLIED, "Reply verified after WhatsApp draft send"
        elif check_at < due:
            state, reason = ContinuityState.NOT_DUE, "Follow-up waiting period not elapsed"
        elif checked_through < due:
            return _unavailable(scope, "Both channels have not yet verified the full follow-up interval")
        else:
            state, reason = ContinuityState.REVIEW_FOLLOW_UP, "No reply in authorized records through verified coverage end"
        return ContinuityDecision(
            state=state, reason=reason,
            work_item_id=scope.work_item_id, contact_id=scope.contact_id,
            supporting_record_ids=tuple(e.source_record_id for e in evidence),
            evidence_fingerprint=fingerprint, follow_up_after=due.isoformat(),
            checked_through=checked_through.isoformat(),
            requires_human_review=state == ContinuityState.REVIEW_FOLLOW_UP,
            cross_channel_link_verified=True,
        )
    except (AttributeError, TypeError, ValueError, OverflowError):
        return _unavailable(scope, "Invalid or untrusted source identity/time")

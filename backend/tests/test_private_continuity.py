"""Privacy-critical acceptance tests for Lola's private continuity engine."""
from datetime import datetime, timedelta, timezone

from app.intelligence.private_continuity import pending_followups

NOW = datetime(2026, 11, 14, 16, tzinfo=timezone.utc)


def event(id, contact, project, channel, direction, kind, hours_ago):
    return {
        "id": id, "contact_id": contact, "project_key": project,
        "channel": channel, "direction": direction, "kind": kind,
        "occurred_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "summary": "First draft of label",
    }


def test_joan_email_request_followed_by_whatsapp_draft_yields_private_reminder():
    events = [
        event("1", "joan", "label-design", "email", "inbound", "request", 52),
        event("2", "joan", "label-design", "email", "outbound", "ack", 50),
        event("3", "joan", "label-design", "whatsapp", "outbound", "draft_sent", 30),
    ]
    due = pending_followups(events, now=NOW)
    assert len(due) == 1
    assert due[0]["event_id"] == "3"
    assert due[0]["contact_id"] == "joan"
    assert due[0]["last_channel"] == "whatsapp"


def test_reply_in_different_channel_cancels_followup():
    events = [
        event("3", "joan", "label-design", "whatsapp", "outbound", "draft_sent", 30),
        event("4", "joan", "label-design", "email", "inbound", "feedback", 1),
    ]
    assert pending_followups(events, now=NOW) == []


def test_same_name_or_other_contact_never_closes_joan_reminder():
    events = [
        event("3", "verified-contact-joan", "label-design", "whatsapp", "outbound", "draft_sent", 30),
        event("4", "different-contact-same-name", "label-design", "email", "inbound", "feedback", 1),
    ]
    assert [x["event_id"] for x in pending_followups(events, now=NOW)] == ["3"]


def test_other_project_does_not_close_pending_item():
    events = [
        event("3", "joan", "label-design", "whatsapp", "outbound", "draft_sent", 30),
        event("4", "joan", "invoice", "email", "inbound", "feedback", 1),
    ]
    assert len(pending_followups(events, now=NOW)) == 1


def test_no_reminder_before_delay():
    assert pending_followups([
        event("3", "joan", "label-design", "whatsapp", "outbound", "draft_sent", 5),
    ], now=NOW) == []


def test_unverified_contact_is_not_guessed_from_message():
    bad = event("3", None, "label-design", "whatsapp", "outbound", "draft_sent", 30)
    bad["summary"] = "Joan sent a label"
    assert pending_followups([bad], now=NOW) == []


def test_resolved_commitment_never_surfaces():
    events = [
        event("3", "joan", "label-design", "whatsapp", "outbound", "draft_sent", 30),
        event("5", "joan", "label-design", "note", "outbound", "closed", 1),
    ]
    assert pending_followups(events, now=NOW) == []


def test_timezone_required():
    bad = event("3", "joan", "label-design", "whatsapp", "outbound", "draft_sent", 30)
    bad["occurred_at"] = "2026-11-13T12:00:00"
    assert pending_followups([bad], now=NOW) == []

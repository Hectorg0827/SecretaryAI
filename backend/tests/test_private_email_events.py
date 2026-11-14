"""Personal Gmail sync never guesses identity from names or a company."""
from app.intelligence.private_email_events import map_gmail_to_private_events


def message(id, from_, to, subject, labels=None):
    return {"id": id, "from": from_, "to": to, "subject": subject,
            "labels": labels or ["INBOX"], "date": "Fri, 13 Nov 2026 09:30:00 -0500",
            "snippet": "Can you send the label?"}


def test_verified_email_mapped_and_reply_subject_normalized():
    contacts = [{"id": "joan-verified", "email": "joan@example.com",
                 "identity_verified": True}]
    events = map_gmail_to_private_events([
        message("1", "Joan <joan@example.com>", "me@example.com", "Label Artwork"),
        message("2", "me@example.com", "Joan <joan@example.com>",
                "Re: Label Artwork", labels=["SENT"]),
    ], contacts)
    assert len(events) == 2
    assert {e["project_key"] for e in events} == {"label-artwork"}
    assert events[0]["direction"] == "inbound"
    assert events[1]["direction"] == "outbound"
    assert all(e["contact_id"] == "joan-verified" for e in events)


def test_unverified_duplicate_or_multirecipient_cannot_be_linked():
    assert map_gmail_to_private_events(
        [message("1", "Joan <joan@example.com>", "me@example.com", "Label")],
        [{"id": "c1", "email": "joan@example.com", "identity_verified": False}],
    ) == []
    assert map_gmail_to_private_events(
        [message("1", "Joan <joan@example.com>", "me@example.com", "Label")],
        [{"id": "c1", "email": "joan@example.com", "identity_verified": True},
         {"id": "c2", "email": "joan@example.com", "identity_verified": True}],
    ) == []
    assert map_gmail_to_private_events(
        [message("1", "Joan <joan@example.com>, Other <x@example.com>", "me@example.com", "Label")],
        [{"id": "c1", "email": "joan@example.com", "identity_verified": True}],
    ) == []


def test_unsent_draft_cannot_cancel_whatsapp_followup():
    contacts = [{"id": "joan", "email": "joan@example.com",
                 "identity_verified": True}]
    assert map_gmail_to_private_events([
        message("draft1", "Joan <joan@example.com>", "me@example.com",
                "Label Artwork", labels=["DRAFT"]),
    ], contacts) == []

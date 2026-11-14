"""Conservative WhatsApp outbound planning policy.

NO send API, queued task, recipient body, or provider token in this module.
WhatsApp Messaging Policy reviewed 2026-11-13: required recipient opt-in;
24-hour freeform reply window after last *customer* message; otherwise an
approved purpose-matched template. Alcohol-promotional messaging is limited to
specific jurisdictions; unknown jurisdictions and alcohol age are fail-closed.

References: https://whatsappbusiness.com/policy/
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


ALCOHOL_ALLOWED_COUNTRIES = frozenset({
    "AS", "AI", "AG", "AR", "AW", "AU", "BB", "BZ", "BM", "BT", "BO", "BR",
    "VG", "KH", "KY", "CL", "CN", "CO", "CR", "CW", "DM", "DO", "EC", "SV",
    "FK", "FM", "FJ", "GF", "PF", "GD", "GP", "GU", "GT", "GY", "HT", "HN",
    "HK", "ID", "JM", "JP", "KI", "LA", "MO", "MY", "MV", "MH", "MQ", "MX",
    "MN", "MM", "NR", "NC", "NZ", "NI", "MP", "PW", "PA", "PG", "PY", "PE",
    "PH", "PR", "KN", "VC", "WS", "SG", "SB", "KR", "LK", "LC", "SR", "BS",
    "TL", "TO", "TT", "TC", "TV", "UY", "VI", "VU", "VE", "VN",
})


def _aware(value: datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


@dataclass(frozen=True)
class MessageDecision:
    permitted_for_employee_review: bool
    mode: str  # blocked | freeform | approved_template
    reason: str
    # Even if True: a human must approve before any provider-side send.


def assess_whatsapp_outbound(
    *,
    now: datetime,
    last_verified_customer_message_at: datetime | None,
    employee_approved: bool,
    recipient_opted_in: bool,
    recipient_opted_out: bool,
    meta_policy_cleared: bool,
    owner_verified: bool,
    target_country: str | None,
    alcohol_promotional: bool | None,
    recipient_age_verified: bool,
    template_name: str | None,
    template_status: str | None,
    template_purpose_matches: bool,
) -> MessageDecision:
    def block(reason: str) -> MessageDecision:
        return MessageDecision(False, "blocked", reason)

    if not _aware(now):
        return block("No trusted timezone-aware clock")
    if not meta_policy_cleared:
        return block("Written Meta policy clearance is absent")
    if not owner_verified:
        return block("Employee-controlled WhatsApp number is unverified")
    if not employee_approved:
        return block("Employee has not approved this specific message")
    if recipient_opted_out:
        return block("Recipient requested no WhatsApp messaging")
    if not recipient_opted_in:
        return block("Recipient WhatsApp opt-in has not been documented")
    if not target_country or len(target_country) != 2 or not target_country.isalpha():
        return block("Recipient jurisdiction is unverified")
    if alcohol_promotional is None:
        return block("Regulated-vertical content review is required")
    if alcohol_promotional:
        if target_country.upper() not in ALCOHOL_ALLOWED_COUNTRIES:
            return block("Alcohol-promotional WhatsApp messaging is not permitted for this jurisdiction")
        if not recipient_age_verified:
            return block("Recipient age must be verified for alcohol-promotional messaging")

    within_window = (
        _aware(last_verified_customer_message_at)
        and last_verified_customer_message_at <= now
        and now - last_verified_customer_message_at < timedelta(hours=24)
    )
    if within_window:
        return MessageDecision(True, "freeform", "Verified recipient message opened a current 24-hour reply window")
    if template_name and template_status == "APPROVED" and template_purpose_matches:
        return MessageDecision(True, "approved_template", "Approved purpose-matched template required outside window")
    return block("Outside customer-service window; an approved purpose-matched template is required")

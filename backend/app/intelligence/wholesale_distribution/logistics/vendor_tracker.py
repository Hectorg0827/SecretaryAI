"""
Stage 3 — PO Transmission & Vendor Response Tracking.

The system:
  1. Sends the PO via Gmail (tracked in Supabase)
  2. Monitors the inbox for supplier confirmation
  3. Parses the confirmation email to extract ETA, quantity, price changes
  4. Escalates if no response within the configured window
  5. Logs all communications with parsed summaries

Response parsing uses keyword/pattern matching — no LLM call needed for the
common cases.  For ambiguous emails, it flags for human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from .pipeline import (
    CommunicationDirection,
    PurchaseOrder,
    VendorCommunication,
)


# ─── Response Parsing ─────────────────────────────────────────────────────────


@dataclass
class ParsedVendorResponse:
    """Structured data extracted from a vendor's reply email."""
    raw_subject: str = ""
    raw_body: str = ""

    # Confirmation status
    order_confirmed: Optional[bool] = None    # None = ambiguous
    full_confirmation: bool = False
    partial_confirmation: bool = False
    rejection: bool = False

    # Extracted dates
    ship_date: Optional[str] = None           # ISO date
    arrival_date: Optional[str] = None        # ISO date

    # Price or quantity changes noted
    price_changes: list[dict] = None          # [{sku, old_price, new_price}]
    qty_changes: list[dict] = None            # [{sku, requested, confirmed}]
    supplier_ref: str = ""

    # Issues
    issues: list[str] = None
    requires_human_review: bool = False
    review_reason: str = ""

    def __post_init__(self):
        if self.price_changes is None:
            self.price_changes = []
        if self.qty_changes is None:
            self.qty_changes = []
        if self.issues is None:
            self.issues = []

    def to_dict(self) -> dict:
        return {
            "order_confirmed": self.order_confirmed,
            "full_confirmation": self.full_confirmation,
            "partial_confirmation": self.partial_confirmation,
            "rejection": self.rejection,
            "ship_date": self.ship_date,
            "arrival_date": self.arrival_date,
            "price_changes": self.price_changes,
            "qty_changes": self.qty_changes,
            "supplier_ref": self.supplier_ref,
            "issues": self.issues,
            "requires_human_review": self.requires_human_review,
            "review_reason": self.review_reason,
        }


class VendorResponseParser:
    """
    Extracts structured data from a vendor's email response.
    Uses regex + keyword matching — fast, deterministic, no API calls.
    """

    # Positive confirmation patterns (English + Spanish)
    _CONFIRM_PATTERNS = [
        r"\bconfirm(ed|amos|amos recibir|ation)\b",
        r"\bwe\s+accept\b",
        r"\borden\s+(confirmada|recibida|aceptada)\b",
        r"\bgood\s+to\s+go\b",
        r"\bwill\s+(ship|send|dispatch|process)\b",
        r"\benviar(emos|emos su pedido)\b",
        r"\bproceed(ing)?\b",
    ]

    # Rejection patterns
    _REJECT_PATTERNS = [
        r"\bcannot\s+(fulfil|accept|process)\b",
        r"\bunable\s+to\b",
        r"\bno\s+(stock|inventory|disponibilidad)\b",
        r"\brechazamos\b",
        r"\bno podemos\b",
    ]

    # Date extraction patterns
    _DATE_PATTERNS = [
        # "April 15" / "15 April" / "15/04/2025" / "04-15-2025"
        r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b",
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
        r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)"
        r"[\s,]+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?\b",
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?"
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r",?\s*(\d{4})?\b",
    ]

    _MONTH_MAP = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
        "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }

    def parse(self, subject: str, body: str, po: PurchaseOrder) -> ParsedVendorResponse:
        result = ParsedVendorResponse(raw_subject=subject, raw_body=body)
        text = f"{subject}\n{body}".lower()

        # ── Confirmation detection ────────────────────────────────────────────
        conf_hits = sum(1 for p in self._CONFIRM_PATTERNS if re.search(p, text, re.I))
        rej_hits = sum(1 for p in self._REJECT_PATTERNS if re.search(p, text, re.I))

        if rej_hits > conf_hits:
            result.rejection = True
            result.order_confirmed = False
        elif conf_hits > 0:
            result.order_confirmed = True
            result.full_confirmation = True
        else:
            result.requires_human_review = True
            result.review_reason = "Could not determine confirmation status from email content"

        # ── Date extraction ───────────────────────────────────────────────────
        dates_found = self._extract_dates(body)
        if dates_found:
            # First date in body is typically ship date, second is arrival date
            result.ship_date = dates_found[0]
            if len(dates_found) > 1:
                result.arrival_date = dates_found[1]

        # ── Supplier reference ────────────────────────────────────────────────
        ref_match = re.search(r"\b(?:ref|reference|our\s+order|pedido)\s*[:#]?\s*([\w\-]+)", text, re.I)
        if ref_match:
            result.supplier_ref = ref_match.group(1)

        # ── Price change detection ────────────────────────────────────────────
        price_mentions = re.findall(r"\$[\d,]+\.?\d*", body)
        if price_mentions and len(price_mentions) > len(po.line_items):
            result.requires_human_review = True
            result.review_reason = (result.review_reason + " | " if result.review_reason else "") + \
                "Multiple price figures detected — verify against PO prices"

        # ── Issue keywords ────────────────────────────────────────────────────
        issue_keywords = [
            ("out of stock", "Supplier may not have sufficient stock"),
            ("sin stock", "Supplier may not have sufficient stock"),
            ("delay", "Potential delay mentioned"),
            ("retraso", "Potential delay mentioned"),
            ("customs", "Customs documentation query"),
            ("certificate", "Certification documentation requested"),
            ("certificado", "Certification documentation requested"),
            ("label", "Labeling requirement mentioned"),
            ("etiqueta", "Labeling requirement mentioned"),
        ]
        for keyword, issue_desc in issue_keywords:
            if keyword in text:
                result.issues.append(issue_desc)

        return result

    def _extract_dates(self, text: str) -> list[str]:
        """Extract ISO date strings from text. Returns up to 3 dates."""
        found: list[str] = []
        today = date.today()

        # Pattern: DD/MM/YYYY or MM/DD/YYYY
        for m in re.finditer(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b", text):
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
            # Determine if MM/DD or DD/MM by which is plausible
            try:
                if 1 <= mo <= 12 and 1 <= d <= 31:
                    dt = date(y, mo, d)
                    if dt > today:
                        found.append(dt.isoformat())
            except ValueError:
                try:
                    dt = date(y, d, mo)
                    if dt > today:
                        found.append(dt.isoformat())
                except ValueError:
                    pass

        # Pattern: "April 15" or "15 April"
        month_names = "|".join(self._MONTH_MAP.keys())
        for m in re.finditer(
            rf"\b({month_names})\s+(\d{{1,2}}),?\s*(\d{{4}})?\b|\b(\d{{1,2}})\s+({month_names}),?\s*(\d{{4}})?\b",
            text, re.I,
        ):
            try:
                if m.group(1):  # Month Day Year
                    month_name = m.group(1).lower()
                    day = int(m.group(2))
                    year = int(m.group(3)) if m.group(3) else today.year
                else:             # Day Month Year
                    day = int(m.group(4))
                    month_name = m.group(5).lower()
                    year = int(m.group(6)) if m.group(6) else today.year
                month = self._MONTH_MAP.get(month_name, 0)
                if month:
                    dt = date(year, month, day)
                    if dt > today:
                        found.append(dt.isoformat())
            except (ValueError, TypeError):
                pass

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for d in found:
            if d not in seen:
                seen.add(d)
                unique.append(d)
        return unique[:3]


# ─── Vendor Tracker ───────────────────────────────────────────────────────────


class VendorTracker:
    """
    Manages PO communication lifecycle:
      • Logs outbound PO emails
      • Parses inbound vendor responses
      • Detects unanswered POs and generates escalations
      • Returns communication log entries for Supabase persistence
    """

    def __init__(
        self,
        company_id: str,
        follow_up_hours_overseas: int = 72,
        follow_up_hours_domestic: int = 24,
    ):
        self.company_id = company_id
        self.follow_up_hours_overseas = follow_up_hours_overseas
        self.follow_up_hours_domestic = follow_up_hours_domestic
        self._parser = VendorResponseParser()

    def log_po_sent(self, po: PurchaseOrder) -> VendorCommunication:
        """Record that a PO was sent to the supplier."""
        return VendorCommunication(
            po_number=po.po_number,
            direction=CommunicationDirection.OUTBOUND,
            subject=f"Purchase Order {po.po_number} — {po.supplier_name}",
            body_summary=f"PO {po.po_number} sent to {po.supplier_name} for "
                         f"{po.total_cases} cases valued at ${po.total_value:,.2f}.",
            extracted_data={"status": "sent", "total_value": po.total_value},
        )

    def process_vendor_email(
        self,
        po: PurchaseOrder,
        subject: str,
        body: str,
    ) -> tuple[VendorCommunication, ParsedVendorResponse]:
        """
        Parse an incoming vendor email and return a structured communication record.
        """
        parsed = self._parser.parse(subject, body, po)

        summary_parts: list[str] = []
        if parsed.order_confirmed:
            summary_parts.append("Order confirmed.")
        elif parsed.rejection:
            summary_parts.append("Order REJECTED by supplier.")
        else:
            summary_parts.append("Response received — status unclear, needs review.")
        if parsed.ship_date:
            summary_parts.append(f"ETA ship date: {parsed.ship_date}.")
        if parsed.arrival_date:
            summary_parts.append(f"ETA arrival: {parsed.arrival_date}.")
        if parsed.issues:
            summary_parts.append(f"Issues noted: {'; '.join(parsed.issues)}.")

        comm = VendorCommunication(
            po_number=po.po_number,
            direction=CommunicationDirection.INBOUND,
            subject=subject,
            raw_body=body,
            body_summary=" ".join(summary_parts),
            extracted_data=parsed.to_dict(),
        )
        return comm, parsed

    def check_overdue_pos(
        self,
        pos_with_sent_times: list[dict],
        # [{po: PurchaseOrder, sent_at: ISO str, supply_chain_type: str,
        #   follow_up_count: int}]
    ) -> list[dict]:
        """
        Evaluate which POs are overdue for a vendor response.
        Returns list of escalation actions needed.
        """
        now = datetime.now(timezone.utc)
        escalations: list[dict] = []

        for item in pos_with_sent_times:
            po: PurchaseOrder = item["po"]
            sent_at_str: str = item["sent_at"]
            supply_type: str = item.get("supply_chain_type", "overseas")
            follow_up_count: int = item.get("follow_up_count", 0)

            try:
                sent_at = datetime.fromisoformat(sent_at_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue

            hours_elapsed = (now - sent_at).total_seconds() / 3600
            threshold = (
                self.follow_up_hours_overseas
                if supply_type == "overseas"
                else self.follow_up_hours_domestic
            )

            if hours_elapsed < threshold:
                continue  # Still within window

            if follow_up_count == 0:
                escalations.append({
                    "po_number": po.po_number,
                    "supplier_name": po.supplier_name,
                    "supplier_email": po.supplier_email,
                    "hours_since_sent": round(hours_elapsed, 1),
                    "action": "draft_follow_up",
                    "subject": f"Follow-up: Purchase Order {po.po_number}",
                    "body": self._follow_up_body(po, attempt=1),
                    "severity": "yellow",
                })
            elif follow_up_count == 1:
                escalations.append({
                    "po_number": po.po_number,
                    "supplier_name": po.supplier_name,
                    "supplier_email": po.supplier_email,
                    "hours_since_sent": round(hours_elapsed, 1),
                    "action": "send_final_follow_up",
                    "subject": f"URGENT Follow-up: Purchase Order {po.po_number}",
                    "body": self._follow_up_body(po, attempt=2),
                    "severity": "red",
                })
            else:
                escalations.append({
                    "po_number": po.po_number,
                    "supplier_name": po.supplier_name,
                    "hours_since_sent": round(hours_elapsed, 1),
                    "action": "manual_intervention_required",
                    "message": (
                        f"PO {po.po_number} to {po.supplier_name} sent "
                        f"{hours_elapsed:.0f} hours ago with no confirmation "
                        f"after {follow_up_count} follow-ups. Manual intervention required."
                    ),
                    "severity": "critical",
                })

        return escalations

    @staticmethod
    def _follow_up_body(po: PurchaseOrder, attempt: int) -> str:
        urgency = "URGENT — " if attempt >= 2 else ""
        if po.language == "es":
            return (
                f"Estimado equipo de {po.supplier_name},\n\n"
                f"{urgency}Queremos hacer seguimiento de nuestra Orden de Compra {po.po_number} "
                f"enviada anteriormente. Por favor confirme su recepción e indique la fecha "
                f"estimada de envío.\n\n"
                f"Total del pedido: ${po.total_value:,.2f} ({po.total_cases} cajas).\n\n"
                f"Muchas gracias,\nSecretaryAI Logistics"
            )
        return (
            f"Dear {po.supplier_name},\n\n"
            f"{urgency}We are following up on Purchase Order {po.po_number} sent previously. "
            f"Please confirm receipt and provide your estimated shipping date.\n\n"
            f"Order total: ${po.total_value:,.2f} ({po.total_cases} cases).\n\n"
            f"Thank you,\nSecretaryAI Logistics"
        )

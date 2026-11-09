"""
Logistics document intelligence: identify → extract → validate → link.

Supported documents: bills of lading, packing lists, commercial invoices,
freight invoices, customs entries / releases, broker notices, arrival notices,
warehouse receipts, delivery appointments, proof-of-delivery, damage reports,
supplier claims, booking and order confirmations.  Container numbers are
extracted from every document type and checked with the ISO 6346 check digit.

Extracted values are *evidence*, never authorization:
  • ``extract`` only reads text.
  • ``validate`` compares the reading with what the ledger already knows.
  • ``to_event`` produces a ledger "document" event whose milestone suggestion
    stays unverified until a named reviewer confirms it (see journey.py).
Nothing here posts inventory, issues credits, files customs entries or sends
messages.

The extractor is deterministic (regex + keyword scoring) so results are
repeatable and testable.  An LLM extractor can be plugged in later by
producing the same ``ExtractedDocument`` shape — its output would carry
``source=AI_INFERRED`` and therefore remain unverified.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional

from .journey import LogisticsEvent, Shipment, ShipmentLedger, SourceType


class DocumentType(str, Enum):
    BILL_OF_LADING = "bill_of_lading"
    PACKING_LIST = "packing_list"
    COMMERCIAL_INVOICE = "commercial_invoice"
    FREIGHT_INVOICE = "freight_invoice"
    CUSTOMS_ENTRY = "customs_entry"
    CUSTOMS_RELEASE = "customs_release"
    BROKER_NOTICE = "broker_notice"
    ARRIVAL_NOTICE = "arrival_notice"
    WAREHOUSE_RECEIPT = "warehouse_receipt"
    DELIVERY_APPOINTMENT = "delivery_appointment"
    PROOF_OF_DELIVERY = "proof_of_delivery"
    DAMAGE_REPORT = "damage_report"
    SUPPLIER_CLAIM = "supplier_claim"
    BOOKING_CONFIRMATION = "booking_confirmation"
    ORDER_CONFIRMATION = "order_confirmation"
    UNKNOWN = "unknown"


# Keyword → weight.  Highest total wins; ties broken by enum order.
_CLASSIFIER: dict[DocumentType, dict[str, int]] = {
    DocumentType.BILL_OF_LADING: {
        "bill of lading": 6, "b/l no": 5, "b/l number": 5, "shipper": 1, "consignee": 2,
        "notify party": 3, "port of loading": 3, "port of discharge": 3, "vessel": 1,
        "conocimiento de embarque": 6,
    },
    DocumentType.PACKING_LIST: {"packing list": 7, "lista de empaque": 7, "gross weight": 1,
                                "net weight": 1, "carton": 1, "dimensions": 1},
    DocumentType.COMMERCIAL_INVOICE: {"commercial invoice": 8, "factura comercial": 8,
                                      "incoterm": 2, "country of origin": 2, "hs code": 2},
    DocumentType.FREIGHT_INVOICE: {"freight invoice": 8, "ocean freight": 3, "baf": 2,
                                   "fuel surcharge": 2, "thc": 1, "drayage": 2,
                                   "amount due": 1, "invoice no": 1},
    DocumentType.CUSTOMS_ENTRY: {"entry summary": 7, "cbp form 7501": 8, "entry number": 3,
                                 "entry no": 3, "entry filed": 3},
    DocumentType.CUSTOMS_RELEASE: {"cargo release": 7, "release notification": 7,
                                   "cbp release": 7, "customs release": 7, "1c release": 6},
    DocumentType.BROKER_NOTICE: {"customs broker": 3, "broker": 2, "customs status": 4,
                                 "customs hold": 4, "exam": 2, "agente aduanal": 4},
    DocumentType.ARRIVAL_NOTICE: {"arrival notice": 8, "aviso de llegada": 8,
                                  "last free day": 3, "free time": 2, "available for pickup": 3},
    DocumentType.WAREHOUSE_RECEIPT: {"warehouse receipt": 8, "receiving report": 7,
                                     "received by": 1, "dock": 1, "putaway": 3, "wr#": 5},
    DocumentType.DELIVERY_APPOINTMENT: {"delivery appointment": 8, "appointment confirmation": 7,
                                        "dock door": 3, "appointment time": 4, "appt": 2},
    DocumentType.PROOF_OF_DELIVERY: {"proof of delivery": 8, "pod": 3, "delivery receipt": 5,
                                     "signed by": 3, "consignee signature": 4, "received in good": 3},
    DocumentType.DAMAGE_REPORT: {"damage report": 8, "os&d": 6, "over, short": 5,
                                 "damaged": 2, "crushed": 2, "photos": 1, "inspection report": 3},
    DocumentType.SUPPLIER_CLAIM: {"supplier claim": 8, "claim for credit": 6, "debit note": 5,
                                  "claim no": 3, "claim amount": 3},
    DocumentType.BOOKING_CONFIRMATION: {"booking confirmation": 8, "booking number": 3,
                                        "cut-off": 3, "cy cutoff": 4, "vgm": 2},
    DocumentType.ORDER_CONFIRMATION: {"order confirmation": 7, "sales order": 2,
                                      "confirmamos": 4, "we confirm": 3, "acknowledg": 3},
}

# What each validated document may *suggest* to the ledger (still unverified
# until reviewed).  Broker notices are handled specially (hold vs release).
_SUGGESTS: dict[DocumentType, str] = {
    DocumentType.CUSTOMS_RELEASE: "customs_released",
    DocumentType.CUSTOMS_ENTRY: "entry_filed",
    DocumentType.ARRIVAL_NOTICE: "vessel_arrived",
    DocumentType.WAREHOUSE_RECEIPT: "warehouse_received",
    DocumentType.PROOF_OF_DELIVERY: "pod_received",
    DocumentType.BOOKING_CONFIRMATION: "freight_booked",
    DocumentType.ORDER_CONFIRMATION: "supplier_confirmed",
    DocumentType.BILL_OF_LADING: "vessel_departed",
}


# ─── ISO 6346 container numbers ───────────────────────────────────────────────

_ISO6346_VALUES = {}
_v = 10
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    if _v % 11 == 0:
        _v += 1
    _ISO6346_VALUES[_ch] = _v
    _v += 1

_CONTAINER_RE = re.compile(r"\b([A-Z]{3}[UJZ])[\s-]?(\d{6})[\s-]?(\d)\b")


def container_check_digit(owner_serial: str) -> int:
    """Return the ISO 6346 check digit for the first 10 characters."""
    total = 0
    for i, ch in enumerate(owner_serial[:10]):
        val = _ISO6346_VALUES[ch] if ch.isalpha() else int(ch)
        total += val * (2 ** i)
    return total % 11 % 10


def is_valid_container_number(number: str) -> bool:
    n = re.sub(r"[\s-]", "", (number or "").upper())
    if not re.fullmatch(r"[A-Z]{3}[UJZ]\d{7}", n):
        return False
    return container_check_digit(n[:10]) == int(n[10])


# ─── Extraction ───────────────────────────────────────────────────────────────

_BOL_RE = re.compile(
    r"(?:bill\s+of\s+lading|b/l|bol|mbl|hbl)\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{5,24})",
    re.I)
_PO_RE = re.compile(r"\b(?:p\.?o\.?|purchase\s+order)\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{2,24})",
                    re.I)
_BOOKING_RE = re.compile(r"booking\s*(?:no\.?|number|ref(?:erence)?|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{4,24})", re.I)
_ENTRY_RE = re.compile(r"entry\s*(?:no\.?|number|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{5,20})", re.I)
_SEAL_RE = re.compile(r"seal\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,20})", re.I)
_SKU_RE = re.compile(r"\b(?:sku|item)\s*(?:no\.?|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9_-]{1,24})", re.I)
_LOT_RE = re.compile(r"\b(?:lot|batch)\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{1,24})", re.I)
_QTY_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)\s*(?:cases|case|cs|ctns?|cartons?)\b", re.I)
_DAMAGED_RE = re.compile(
    r"(\d{1,3}(?:,\d{3})+|\d+)\s*(?:cases|case|cs|ctns?|cartons?)?\s*(?:were\s+|are\s+)?(?:damaged|crushed|wet|broken)",
    re.I)
_SHORT_RE = re.compile(r"(?:short(?:age)?\s*(?:of)?\s*[:#]?\s*(\d+)|(\d+)\s*(?:cases|cs|ctns?)?\s*short)", re.I)
_TOTAL_RE = re.compile(
    r"(?:invoice\s+total|total(?:\s+due)?|amount\s+due|balance\s+due)[^\d$\n]*\$?\s*([\d,]+\.\d{2})", re.I)
_MONEY_LINE_RE = re.compile(r"\$?\s*([\d,]+\.\d{2})")
_SIGNED_RE = re.compile(r"(?:signed\s+by|received\s+by|consignee\s+signature)\s*[:\-]?\s*([A-Za-z][A-Za-z .'-]{1,60})",
                        re.I)
_DATE_PATTERNS = (
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), "%Y-%m-%d"),
    (re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"), "%m/%d/%Y"),
)
_LABELLED_DATE_RE = re.compile(
    r"(eta|etd|delivered(?:\s+on)?|delivery\s+date|appointment(?:\s+date)?|last\s+free\s+day|lfd|"
    r"released(?:\s+on)?|release\s+date|arrival(?:\s+date)?|received(?:\s+on)?|date)\s*[:\-]?\s*"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", re.I)
_TIME_WINDOW_RE = re.compile(r"(\d{1,2}:\d{2})\s*(?:-|to|–)\s*(\d{1,2}:\d{2})")
_NEGATION_RE = re.compile(
    r"\b(not|no|pending|awaiting|unable|cannot|has\s+not|have\s+not|yet\s+to)\b[^.\n]{0,40}"
    r"\b(release|released|clear|cleared|clearance)\b"
    r"|\b(release|clearance)\s+(?:is\s+|still\s+)?(?:pending|awaited|on\s+hold|not\s+(?:yet\s+)?issued)\b",
    re.I)
_RELEASE_RE = re.compile(r"\b(released|cleared|cargo\s+release|liberad[oa]|despachad[oa])\b", re.I)

_FREIGHT_COMPONENT_KEYWORDS = {
    "ocean_freight": ("ocean freight", "sea freight", "flete"),
    "fuel_surcharge": ("fuel surcharge", "baf", "bunker"),
    "origin_charges": ("origin", "thc origin", "documentation fee"),
    "port_charges": ("destination thc", "port charge", "terminal"),
    "broker_fees": ("broker", "customs clearance fee"),
    "customs_duty": ("duty", "duties", "tariff"),
    "drayage": ("drayage", "trucking", "cartage"),
    "demurrage": ("demurrage", "storage"),
    "detention": ("detention", "per diem"),
    "exam_fees": ("exam fee", "inspection fee"),
    "insurance": ("insurance",),
}


@dataclass
class ExtractedDocument:
    doc_id: str
    doc_type: DocumentType
    classification_confidence: float
    fields: dict = field(default_factory=dict)
    status: str = "extracted"            # extracted | validated | validation_failed
    checks: list = field(default_factory=list)   # [{check, passed, detail, critical}]
    linked_shipment_id: str = ""
    link_candidates: list = field(default_factory=list)
    link_basis: str = ""

    @property
    def failed_checks(self) -> list[str]:
        return [c["detail"] for c in self.checks if not c["passed"]]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["doc_type"] = self.doc_type.value
        d["failed_checks"] = self.failed_checks
        d["authorization"] = "none — extracted values are evidence pending review"
        return d


class DocumentIntelligence:
    """Deterministic classify / extract / validate / link for logistics documents."""

    def classify(self, text: str, hint: str = "") -> tuple[DocumentType, float]:
        low = f"{hint}\n{text}".lower()
        scores = {
            dt: sum(w for kw, w in kws.items() if kw in low)
            for dt, kws in _CLASSIFIER.items()
        }
        best = max(scores, key=lambda d: scores[d])
        top = scores[best]
        if top < 3:
            return DocumentType.UNKNOWN, 0.0
        runner_up = max([v for k, v in scores.items() if k != best] or [0])
        confidence = round(min(1.0, (top - runner_up + 2) / (top + 2)), 2)
        return best, confidence

    def extract(self, text: str, doc_id: str = "", hint: str = "") -> ExtractedDocument:
        doc_type, conf = self.classify(text, hint)
        doc_id = doc_id or "doc-" + hashlib.sha256(text.encode()).hexdigest()[:12]
        up = text.upper()

        containers, invalid_containers = [], []
        for m in _CONTAINER_RE.finditer(up):
            num = "".join(m.groups())
            (containers if is_valid_container_number(num) else invalid_containers).append(num)

        fields: dict = {
            "container_numbers": _uniq(containers),
            "invalid_container_numbers": _uniq(invalid_containers),
            "bol_number": _first(_BOL_RE, text),
            "po_numbers": _uniq(m.group(1).upper() for m in _PO_RE.finditer(text)
                                if not m.group(1).lower().startswith(("box", "rt"))),
            "booking_ref": _first(_BOOKING_RE, text),
            "entry_number": _first(_ENTRY_RE, text),
            "seal_numbers": _uniq(m.group(1).upper() for m in _SEAL_RE.finditer(text)),
            "skus": _uniq(m.group(1).upper() for m in _SKU_RE.finditer(text)),
            "lots": _uniq(m.group(1).upper() for m in _LOT_RE.finditer(text)),
            "dates": _labelled_dates(text),
        }

        quantities = [_int(m.group(1)) for m in _QTY_RE.finditer(text)]
        damaged = [_int(m.group(1)) for m in _DAMAGED_RE.finditer(text)]
        shorts = [_int(m.group(1) or m.group(2)) for m in _SHORT_RE.finditer(text)]
        fields["quantities_cases"] = quantities
        fields["damaged_cases"] = sum(damaged) if damaged else 0
        fields["short_cases"] = sum(shorts) if shorts else 0
        total = _first(_TOTAL_RE, text)
        fields["total_amount"] = float(total.replace(",", "")) if total else None

        if doc_type == DocumentType.FREIGHT_INVOICE:
            fields["cost_components"] = _freight_components(text)
        if doc_type == DocumentType.PROOF_OF_DELIVERY:
            sig = _SIGNED_RE.search(text)
            fields["signed_by"] = sig.group(1).strip() if sig else ""
            fields["delivery_exceptions"] = bool(
                damaged or shorts or re.search(r"\b(exception|refused|os&d|damaged|short)\b", text, re.I))
        if doc_type == DocumentType.DELIVERY_APPOINTMENT:
            tw = _TIME_WINDOW_RE.search(text)
            fields["window"] = {"start": tw.group(1), "end": tw.group(2)} if tw else {}
            dock = re.search(r"dock\s*(?:door)?\s*[:#]?\s*([A-Z0-9-]{1,6})", text, re.I)
            fields["dock"] = dock.group(1).upper() if dock else ""
        if doc_type == DocumentType.BROKER_NOTICE or doc_type == DocumentType.CUSTOMS_RELEASE:
            fields["customs_status"] = broker_status(text)

        return ExtractedDocument(doc_id=doc_id, doc_type=doc_type,
                                 classification_confidence=conf, fields=fields)

    # ── Linking ──────────────────────────────────────────────────────────────

    def link(self, doc: ExtractedDocument, ledger: ShipmentLedger) -> ExtractedDocument:
        """Link to exactly one shipment by container → BOL → booking → PO.  Ambiguity is not guessed."""
        f = doc.fields
        for basis, ids in (
            ("container", f.get("container_numbers", [])),
            ("bol", [f["bol_number"]] if f.get("bol_number") else []),
            ("booking", [f["booking_ref"]] if f.get("booking_ref") else []),
            ("po", f.get("po_numbers", [])),
        ):
            hits = {s.shipment_id for i in ids for s in ledger.find(i)}
            if len(hits) == 1:
                doc.linked_shipment_id, doc.link_basis = hits.pop(), basis
                return doc
            if len(hits) > 1:
                doc.link_candidates = sorted(hits)
                doc.link_basis = f"ambiguous_{basis}"
                return doc
        doc.link_basis = "no_match"
        return doc

    # ── Validation ───────────────────────────────────────────────────────────

    def validate(self, doc: ExtractedDocument, shipment: Optional[Shipment]) -> ExtractedDocument:
        f, checks = doc.fields, []

        def check(name, passed, detail, critical=True):
            checks.append({"check": name, "passed": bool(passed), "detail": detail, "critical": critical})

        check("classified", doc.doc_type != DocumentType.UNKNOWN, "document type identified")
        if f.get("invalid_container_numbers"):
            check("container_check_digit", False,
                  f"container number(s) fail ISO 6346 check digit: {', '.join(f['invalid_container_numbers'])}")
        check("linked", shipment is not None,
              "linked to a shipment" if shipment else f"could not link ({doc.link_basis or 'no identifiers'})")

        if shipment is not None:
            if f.get("container_numbers") and shipment.container_numbers:
                unknown = [c for c in f["container_numbers"] if c not in shipment.container_numbers]
                check("container_matches", not unknown,
                      "containers match shipment" if not unknown
                      else f"container(s) {', '.join(unknown)} not on shipment {shipment.shipment_id}")
            if f.get("bol_number") and shipment.bol_number:
                ok = f["bol_number"].upper() == shipment.bol_number.upper()
                check("bol_matches", ok, "B/L matches" if ok
                      else f"B/L {f['bol_number']} ≠ shipment B/L {shipment.bol_number}")
            if f.get("po_numbers") and shipment.po_numbers:
                ok = any(p in shipment.po_numbers for p in f["po_numbers"])
                check("po_matches", ok, "PO matches" if ok
                      else f"PO(s) {', '.join(f['po_numbers'])} not on shipment", critical=False)
            if doc.doc_type in (DocumentType.PACKING_LIST, DocumentType.COMMERCIAL_INVOICE) \
                    and shipment.total_cases and f.get("quantities_cases"):
                stated = max(f["quantities_cases"])
                ok = stated == shipment.total_cases
                check("quantity_matches_po", ok,
                      f"{stated} cases matches PO" if ok
                      else f"document states {stated} cases; PO expects {shipment.total_cases}")
            if f.get("skus") and shipment.lines:
                po_skus = {l.sku.upper() for l in shipment.lines}
                unknown = [s for s in f["skus"] if s not in po_skus]
                check("skus_on_po", not unknown, "SKUs match PO" if not unknown
                      else f"SKU(s) not on PO: {', '.join(unknown)}", critical=False)

        if doc.doc_type == DocumentType.PROOF_OF_DELIVERY:
            check("pod_signed", bool(f.get("signed_by")), "POD signed" if f.get("signed_by")
                  else "POD has no recipient signature/name")
            check("pod_dated", bool(_pick_date(f, "delivered", "delivery_date", "date")),
                  "POD has delivery date")
        if doc.doc_type == DocumentType.DELIVERY_APPOINTMENT:
            check("appointment_dated", bool(_pick_date(f, "appointment", "appointment_date", "date")),
                  "appointment has a date")
        if doc.doc_type == DocumentType.FREIGHT_INVOICE:
            comps = f.get("cost_components", {})
            total = f.get("total_amount")
            if comps and total is not None:
                s = round(sum(comps.values()), 2)
                check("invoice_lines_sum", abs(s - total) <= 1.0,
                      f"line items sum {s:.2f} vs stated total {total:.2f}", critical=False)
            check("invoice_has_amount", bool(comps) or total is not None, "invoice amounts found")

        doc.checks = checks
        doc.status = "validated" if all(c["passed"] for c in checks if c["critical"]) else "validation_failed"
        return doc

    # ── Ledger integration ───────────────────────────────────────────────────

    def to_events(self, doc: ExtractedDocument, company_id: str, reviewed_by: str = "",
                  received_at: str = "", source_ref: str = "") -> list[LogisticsEvent]:
        """
        Turn a linked document into ledger events.  Without a named reviewer the
        document's milestone suggestion is stored as unverified evidence, and
        freight-invoice amounts are recorded as unverified cost lines.
        """
        if not doc.linked_shipment_id:
            return []
        f = doc.fields
        suggests = _SUGGESTS.get(doc.doc_type)
        if doc.doc_type in (DocumentType.BROKER_NOTICE, DocumentType.CUSTOMS_RELEASE):
            status = f.get("customs_status")
            suggests = {"released": "customs_released", "hold": "customs_hold",
                        "exam": "customs_exam"}.get(status)
        if doc.doc_type == DocumentType.PROOF_OF_DELIVERY and doc.status != "validated":
            suggests = "pod_received"   # still evidence only
        reviewed = bool(reviewed_by) and doc.status == "validated"
        source = SourceType.DOCUMENT_VALIDATED if reviewed else SourceType.DOCUMENT_EXTRACTED
        when = received_at or _pick_date(f, "delivered", "released", "arrival", "received", "date") or ""
        events = [LogisticsEvent(
            company_id=company_id, shipment_id=doc.linked_shipment_id, kind="document",
            source=source, occurred_at=when, source_ref=source_ref or doc.doc_id,
            confirmed_by=reviewed_by if reviewed else "",
            payload={
                "doc_id": doc.doc_id, "doc_type": doc.doc_type.value,
                "status": "reviewed" if reviewed else doc.status,
                "fields": _jsonable(f), "failed_checks": doc.failed_checks,
                **({"suggests_milestone": suggests,
                    "milestone_date": _pick_date(f, "delivered", "released", "arrival", "received")}
                   if suggests else {}),
            },
        )]
        if doc.doc_type == DocumentType.FREIGHT_INVOICE:
            for comp, amt in (f.get("cost_components") or {}).items():
                events.append(LogisticsEvent(
                    company_id=company_id, shipment_id=doc.linked_shipment_id, kind="cost",
                    source=source, occurred_at=when, source_ref=doc.doc_id,
                    confirmed_by=reviewed_by if reviewed else "",
                    payload={"component": comp, "amount_usd": amt, "basis": "invoiced"},
                ))
        if doc.doc_type == DocumentType.ARRIVAL_NOTICE and _pick_date(f, "last_free_day", "lfd"):
            events.append(LogisticsEvent(
                company_id=company_id, shipment_id=doc.linked_shipment_id, kind="port_terms",
                source=source, occurred_at=when, source_ref=doc.doc_id,
                confirmed_by=reviewed_by if reviewed else "",
                payload={"last_free_day": _pick_date(f, "last_free_day", "lfd")},
            ))
        return events

    def process(self, text: str, ledger: ShipmentLedger, *, doc_id: str = "", hint: str = "",
                reviewed_by: str = "", received_at: str = "") -> tuple[ExtractedDocument, list[LogisticsEvent]]:
        """Convenience: extract → link → validate → events (not yet ingested)."""
        doc = self.extract(text, doc_id=doc_id, hint=hint)
        self.link(doc, ledger)
        self.validate(doc, ledger.get(doc.linked_shipment_id) if doc.linked_shipment_id else None)
        return doc, self.to_events(doc, ledger.company_id, reviewed_by=reviewed_by, received_at=received_at)


def broker_status(text: str) -> str:
    """
    Read a broker / customs message conservatively.

    Negated release language ("NOT released", "pending release", "awaiting
    clearance") and any hold/exam language win over the word "released".
    Returns "hold" | "exam" | "not_released" | "released" | "unknown".
    """
    low = text.lower()
    has_hold = bool(re.search(r"\b(hold|on hold|detained|retenci[oó]n)\b", low))
    has_exam = bool(re.search(r"\b(exam|examination|inspection|intensive)\b", low))
    if has_hold:
        return "hold"
    if has_exam:
        return "exam"
    if _NEGATION_RE.search(text):
        return "not_released"
    if _RELEASE_RE.search(text):
        return "released"
    return "unknown"


# ─── helpers ──────────────────────────────────────────────────────────────────


def _first(rx: re.Pattern, text: str) -> str:
    m = rx.search(text)
    return m.group(1).strip().upper() if m else ""


def _uniq(it) -> list:
    out = []
    for x in it:
        if x and x not in out:
            out.append(x)
    return out


def _int(s: str) -> int:
    try:
        return int(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return 0


def _to_iso(s: str) -> str:
    for rx, fmt in _DATE_PATTERNS:
        if rx.fullmatch(s):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                return ""
    return ""


def _labelled_dates(text: str) -> dict:
    out: dict[str, str] = {}
    for m in _LABELLED_DATE_RE.finditer(text):
        label = re.sub(r"\s+on$", "", m.group(1).lower().strip())
        label = label.replace(" ", "_")
        if label == "lfd":
            label = "last_free_day"
        iso = _to_iso(m.group(2))
        if iso and label not in out:
            out[label] = iso
    if "date" not in out:
        for rx, _ in _DATE_PATTERNS:
            m = rx.search(text)
            if m and _to_iso(m.group(1)):
                out["date"] = _to_iso(m.group(1))
                break
    return out


def _pick_date(fields: dict, *labels: str) -> str:
    dates = fields.get("dates", {}) or {}
    for lbl in labels:
        for k, v in dates.items():
            if k == lbl or k.startswith(lbl):
                return v
    return ""


def _freight_components(text: str) -> dict:
    comps: dict[str, float] = {}
    for line in text.splitlines():
        low = line.lower()
        if re.search(r"\b(total|amount due|balance)\b", low):
            continue
        amounts = _MONEY_LINE_RE.findall(line)
        if not amounts:
            continue
        for comp, kws in _FREIGHT_COMPONENT_KEYWORDS.items():
            if any(k in low for k in kws):
                comps[comp] = round(comps.get(comp, 0.0) + float(amounts[-1].replace(",", "")), 2)
                break
    return comps


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v

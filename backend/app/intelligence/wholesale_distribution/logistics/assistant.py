"""
Lola's logistics question answering.

``LogisticsAssistant.ask(question)`` → ``AssistantAnswer`` with a plain-English
answer, the structured data behind it, the provenance of every fact, and links
to the related POs, customer orders, SKUs and communications.

Design
──────
• Deterministic: intent detection is keyword/regex based and every number in an
  answer is computed from the ledger.  The shared chat orchestrator can call
  this as a tool and let the LLM phrase the result — the LLM never invents the
  facts.
• Permission-first: every handler works on ``viewer.visible(...)`` shipments
  and ``viewer.project(...)`` dicts, so a sales rep can never be answered with
  another customer's allocation or with cost data.
• Verified vs reported: answers distinguish verified facts from unverified
  reports (emails, AI readings) and say which is which.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from .documents import _CONTAINER_RE
from .journey import REQUIRED_CUSTOMS_DOCUMENTS, Shipment, ShipmentLedger, _parse_date
from .landed_cost import landed_cost
from .monitoring import LogisticsMonitor, MonitorConfig
from .visibility import LogisticsViewer


@dataclass
class AssistantAnswer:
    intent: str
    answer: str
    data: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)       # provenance notes
    links: list = field(default_factory=list)         # related records
    restricted: list = field(default_factory=list)    # what was withheld and why
    suggested_actions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_ID_PATTERNS = (
    re.compile(r"\b(?:b/?l|bol|bill of lading)\s*#?\s*([A-Z0-9-]{6,24})", re.I),
    re.compile(r"\b(?:po|p\.o\.|purchase order)\s*#?\s*([A-Z0-9-]{3,24})", re.I),
    re.compile(r"\border\s*#?\s*([A-Z0-9-]{3,24})", re.I),
    re.compile(r"\bshipment\s*#?\s*([A-Z0-9-]{3,40})", re.I),
)

INTENTS: list[tuple[str, Callable[[str], bool]]] = [
    ("landed_cost", lambda q: "landed cost" in q or "cost per case" in q),
    ("supplier_responsibility", lambda q: "supplier" in q and any(
        w in q for w in ("responsible", "shortage", "short", "damage", "blame", "fault"))),
    ("missing_customs_docs", lambda q: "missing" in q and ("doc" in q or "customs" in q)),
    ("broker_released", lambda q: ("release" in q or "cleared" in q or "clear" in q) and any(
        w in q for w in ("broker", "customs", "cargo", "container", "shipment"))),
    ("demurrage", lambda q: any(w in q for w in ("demurrage", "detention", "free time", "storage",
                                                 "last free day"))),
    ("overdue_pos", lambda q: ("purchase order" in q or re.search(r"\bpos?\b", q) is not None)
     and any(w in q for w in ("overdue", "late", "behind"))),
    ("proof_of_delivery", lambda q: "proof of delivery" in q or re.search(r"\bpods?\b", q) is not None),
    ("customer_not_received", lambda q: ("customer" in q or "order" in q) and any(
        w in q for w in ("received", "receive", "get their", "gotten", "delivered yet", "hasn't", "not received"))),
    ("in_transit_inventory", lambda q: "in transit" in q and any(
        w in q for w in ("inventory", "stock", "cases", "what", "units"))),
    ("arriving_this_week", lambda q: "arriv" in q and any(w in q for w in ("this week", "next 7", "next seven"))),
    ("delayed", lambda q: "delay" in q or ("late" in q and "shipment" in q)),
    ("where_is", lambda q: "where" in q or "status" in q or "track" in q),
]


class LogisticsAssistant:
    def __init__(self, ledger: ShipmentLedger, viewer: LogisticsViewer,
                 monitor_config: Optional[MonitorConfig] = None):
        viewer.require_access()
        if viewer.company_id != ledger.company_id:
            raise PermissionError("viewer and ledger belong to different companies")
        self.ledger = ledger
        self.viewer = viewer
        self.monitor = LogisticsMonitor(ledger, monitor_config)

    # ── entry point ──────────────────────────────────────────────────────────

    def ask(self, question: str, as_of: Optional[date] = None) -> AssistantAnswer:
        today = as_of or date.today()
        q = question.lower()
        intent = next((name for name, test in INTENTS if test(q)), None)
        ident = self._identifier(question)
        if intent is None and ident:
            intent = "where_is"
        if intent is None:
            return AssistantAnswer(
                "unknown",
                "I can answer questions about container location, delays, arrivals, demurrage, customs "
                "documents and release, overdue POs, customer deliveries, in-transit inventory, proof of "
                "delivery, landed cost and supplier responsibility. Which would you like?")
        return getattr(self, f"_{intent}")(question, ident, today)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _identifier(self, question: str) -> str:
        m = _CONTAINER_RE.search(question.upper())
        if m:
            return "".join(m.groups())
        # Prefer any token that is a known identifier in this company's ledger
        # (handles "PO-0990", "SO-502", B/L numbers… exactly as written).
        for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9/_-]{2,39}", question):
            tok = tok.strip("-/_")
            if any(self.viewer.can_see_shipment(s) for s in self.ledger.find(tok)):
                return tok
        for rx in _ID_PATTERNS:
            m = rx.search(question)
            if m:
                return m.group(1)
        return ""

    def _visible(self) -> list[Shipment]:
        return self.viewer.visible(self.ledger.shipments())

    def _resolve(self, ident: str) -> list[Shipment]:
        return [s for s in self.ledger.find(ident) if self.viewer.can_see_shipment(s)]

    def _link(self, s: Shipment) -> dict:
        d = self.viewer.project(s)
        comms = sorted({e["source_ref"] for e in d.get("pending_evidence", []) if e.get("source_ref")})
        return {
            "shipment_id": s.shipment_id,
            "containers": d.get("container_numbers", []),
            "po_numbers": d.get("po_numbers", []),
            "orders": sorted({a["order_id"] for a in d.get("allocations", [])}),
            "skus": sorted({l["sku"] for l in d.get("lines", [])}),
            "communications": comms,
        }

    def _alerts(self, today: date, codes: set[str]) -> list[dict]:
        alerts = [a for a in self.monitor.scan(today) if a.code in codes]
        return self.viewer.filter_alerts(alerts, {s.shipment_id: s for s in self.ledger.shipments()})

    @staticmethod
    def _eta_text(d: dict) -> str:
        eta = d["eta"]
        cur, rep = eta["current"], eta["reported_unverified"]
        parts = []
        if cur:
            parts.append(f"verified ETA {cur['eta']} (from {cur['source']})")
        else:
            parts.append("no verified ETA on file")
        if eta.get("delay_days"):
            parts.append(f"{eta['delay_days']:+d} day(s) vs original")
        if rep:
            parts.append(f"an unverified report says {rep['eta']} (via {rep['source']})")
        return "; ".join(parts)

    def _not_found(self, intent: str, ident: str) -> AssistantAnswer:
        return AssistantAnswer(intent, f"I couldn't find a shipment you have access to matching '{ident}'.")

    # ── handlers ─────────────────────────────────────────────────────────────

    def _where_is(self, question, ident, today) -> AssistantAnswer:
        if not ident:
            return AssistantAnswer("where_is", "Which container, B/L, PO or order number should I look up?")
        hits = self._resolve(ident)
        if not hits:
            return self._not_found("where_is", ident)
        lines, data, sources = [], [], []
        for s in hits:
            d = self.viewer.project(s)
            verified = sorted(
                (m for st in d["stages"] for m in st["verified_milestones"]
                 if st["stage"] != "11_customer_order_allocation"),
                key=lambda m: m["occurred_at"] or "")
            last = verified[-1] if verified else None
            txt = f"{ident} is at stage '{d['current_stage_label']}'"
            if last:
                txt += f"; last verified event: {last['label']} on {last['occurred_at'][:10]} ({last['source']})"
                sources.append(f"{last['label']}: {last['source']}")
            txt += f"; {self._eta_text(d)}."
            pend = d.get("pending_evidence") or []
            if pend:
                txt += f" {len(pend)} unverified report(s) await confirmation."
            if any(x["code"] == "CUSTOMS_HOLD" for x in d["exceptions"]):
                txt += " It is on a CUSTOMS HOLD."
            lines.append(txt)
            data.append({"shipment_id": s.shipment_id, "stage": d["current_stage"],
                         "eta": d["eta"], "last_verified_milestone": last,
                         "destination": d["destination"], "carrier": d["carrier"]})
        return AssistantAnswer("where_is", " ".join(lines), {"shipments": data}, sources,
                               [self._link(s) for s in hits])

    def _delayed(self, question, ident, today) -> AssistantAnswer:
        alerts = self._alerts(today, {"SHIPMENT_DELAYED", "MILESTONE_OVERDUE"})
        if not alerts:
            return AssistantAnswer("delayed", "No shipments are delayed based on verified data.",
                                   sources=["verified carrier/forwarder ETAs and milestone dates"])
        txt = f"{len(alerts)} delay(s): " + "; ".join(a["title"] for a in alerts[:10]) + "."
        ships = {a["shipment_id"] for a in alerts}
        return AssistantAnswer("delayed", txt, {"alerts": alerts},
                               ["delays use verified ETAs only; unverified emails do not count"],
                               [self._link(self.ledger.get(i)) for i in ships if self.ledger.get(i)],
                               suggested_actions=sorted({x for a in alerts for x in a["recommended_actions"]}))

    def _arriving_this_week(self, question, ident, today) -> AssistantAnswer:
        end = today + timedelta(days=6 - today.weekday()) if "this week" in question.lower() \
            else today + timedelta(days=7)
        arriving, reported = [], []
        for s in self._visible():
            if s.has("vessel_arrived") or s.has("arrived_at_warehouse"):
                continue
            cur, rep = s.current_eta(), s.reported_eta()
            if cur and today <= _parse_date(cur.eta) <= end:
                arriving.append({"shipment_id": s.shipment_id, "eta": cur.eta, "source": cur.source.value,
                                 "containers": s.container_numbers, "destination": s.destination})
            elif rep and today <= _parse_date(rep.eta) <= end:
                reported.append({"shipment_id": s.shipment_id, "eta": rep.eta, "source": rep.source.value})
        arriving.sort(key=lambda x: x["eta"])
        txt = (f"{len(arriving)} shipment(s) have a verified ETA between {today} and {end}: "
               + ", ".join(f"{', '.join(a['containers']) or a['shipment_id']} on {a['eta']}" for a in arriving)
               if arriving else f"No shipments have a verified ETA between {today} and {end}.")
        if reported:
            txt += (f" {len(reported)} more are *reported* (unverified) to arrive in that window: "
                    + ", ".join(f"{r['shipment_id']} on {r['eta']} via {r['source']}" for r in reported) + ".")
        return AssistantAnswer("arriving_this_week", txt,
                               {"window": [today.isoformat(), end.isoformat()], "verified": arriving,
                                "reported_unverified": reported},
                               ["verified ETAs from carrier/terminal/forwarder feeds or confirmed by staff"],
                               [self._link(self.ledger.get(a["shipment_id"])) for a in arriving])

    def _demurrage(self, question, ident, today) -> AssistantAnswer:
        alerts = self._alerts(today, {"DEMURRAGE_ACCRUING", "DEMURRAGE_RISK", "DETENTION_RISK",
                                      "DETENTION_ACCRUING", "LFD_UNKNOWN"})
        restricted = [] if self.viewer.can_view_commercial else ["dollar exposure requires financial access"]
        if not alerts:
            return AssistantAnswer("demurrage", "No containers are at risk of demurrage or detention.",
                                   restricted=restricted)
        parts = []
        for a in alerts:
            p = f"{a['title']} — {a['message']}"
            if a.get("financial_exposure_usd"):
                p += f" (exposure ~${a['financial_exposure_usd']:,.0f})"
            parts.append(p)
        return AssistantAnswer("demurrage", f"{len(alerts)} container(s) need attention: " + "; ".join(parts),
                               {"alerts": alerts}, ["last free day from terminal feed / reviewed arrival notice"],
                               [self._link(self.ledger.get(a["shipment_id"])) for a in alerts
                                if self.ledger.get(a["shipment_id"])],
                               restricted, sorted({x for a in alerts for x in a["recommended_actions"]}))

    def _missing_customs_docs(self, question, ident, today) -> AssistantAnswer:
        targets = self._resolve(ident) if ident else self._visible()
        rows = []
        for s in targets:
            if not s.is_import or s.has("customs_released"):
                continue
            missing = s.missing_documents(REQUIRED_CUSTOMS_DOCUMENTS)
            failed = [d.doc_type for d in s.documents if d.status == "validation_failed"]
            if missing or failed:
                rows.append({"shipment_id": s.shipment_id, "containers": s.container_numbers,
                             "missing": missing, "failed_validation": failed,
                             "eta": s.current_eta().eta if s.current_eta() else None})
        if not rows:
            return AssistantAnswer("missing_customs_docs",
                                   "All shipments awaiting customs have their B/L, commercial invoice and "
                                   "packing list on file.")
        txt = "; ".join(
            f"{', '.join(r['containers']) or r['shipment_id']}: missing {', '.join(r['missing']) or 'none'}"
            + (f", failed validation {', '.join(r['failed_validation'])}" if r["failed_validation"] else "")
            for r in rows)
        return AssistantAnswer("missing_customs_docs", f"{len(rows)} shipment(s) have customs document gaps: {txt}.",
                               {"shipments": rows}, ["document checklist from the ledger"],
                               [self._link(self.ledger.get(r["shipment_id"])) for r in rows],
                               suggested_actions=["Request missing documents from the supplier (draft available)"])

    def _broker_released(self, question, ident, today) -> AssistantAnswer:
        targets = self._resolve(ident) if ident else [
            s for s in self._visible() if s.is_import and s.has("vessel_arrived") and not s.has("gate_out_port")]
        if ident and not targets:
            return self._not_found("broker_released", ident)
        if not targets:
            return AssistantAnswer("broker_released", "No arrived shipments are waiting on customs right now.")
        out, data = [], []
        for s in targets:
            name = ", ".join(s.container_numbers) or s.shipment_id
            rel = s.milestones.get("customs_released")
            hold = next((e for e in s.open_exceptions() if e.code == "CUSTOMS_HOLD"), None)
            rep = next((e for e in s.pending_evidence() if e.code == "customs_released"), None)
            if rel:
                status = f"{name}: YES — released {rel.occurred_at[:10]} (verified via {rel.source.value})"
            elif hold:
                status = f"{name}: NO — on customs hold ({hold.message})"
            elif rep:
                status = (f"{name}: NOT CONFIRMED — a {rep.source.value.replace('_', ' ')} says it was released, "
                          f"but this has not been verified. Ask the broker or confirm in the evidence queue")
            else:
                status = f"{name}: not released yet"
            out.append(status)
            data.append({"shipment_id": s.shipment_id, "released": bool(rel),
                         "release_source": rel.source.value if rel else None,
                         "on_hold": bool(hold), "unverified_release_report": rep.to_dict() if rep else None})
        return AssistantAnswer("broker_released", ". ".join(out) + ".", {"shipments": data},
                               ["release is verified only from broker EDI / customs system or staff confirmation"],
                               [self._link(s) for s in targets])

    def _overdue_pos(self, question, ident, today) -> AssistantAnswer:
        if self.viewer.tier == "sales":
            return AssistantAnswer("overdue_pos", "Purchase-order status is not available for your role.",
                                   restricted=["purchasing data requires logistics or management access"])
        alerts = self._alerts(today, {"PO_OVERDUE", "SUPPLIER_CONFIRMATION_LATE"})
        if not alerts:
            return AssistantAnswer("overdue_pos", "No purchase orders are overdue.")
        return AssistantAnswer("overdue_pos", f"{len(alerts)} PO issue(s): " + "; ".join(
            f"{a['title']} — {a['message']}" for a in alerts),
            {"alerts": alerts}, ["requested ship dates vs verified supplier/forwarder milestones"],
            [self._link(self.ledger.get(a["shipment_id"])) for a in alerts if self.ledger.get(a["shipment_id"])],
            suggested_actions=["Review the supplier follow-up drafts"])

    def _customer_not_received(self, question, ident, today) -> AssistantAnswer:
        q = question.lower()
        matches = []
        for s in self._visible():
            d = self.viewer.project(s)
            for a in d["allocations"]:
                name = (a.get("customer_name") or "").lower()
                if (ident and a["order_id"].upper() == ident.upper()) or (
                        name and name != "[redacted]" and name in q):
                    matches.append((s, a))
        if not matches:
            return AssistantAnswer("customer_not_received",
                                   "I couldn't find that customer order among the shipments you can see. "
                                   "Please give me the order number.")
        out, data = [], []
        for s, a in matches:
            reasons = []
            if a["status"] == "delivered":
                pod = "with proof of delivery on file" if a.get("pod_doc_id") else "but NO proof of delivery yet"
                out.append(f"Order {a['order_id']} was delivered on {a['delivered_at'][:10]} "
                           f"(source: {a['delivery_source']}) {pod}.")
                data.append({"order_id": a["order_id"], "status": "delivered", "pod": bool(a.get("pod_doc_id"))})
                continue
            if any(e.code == "CUSTOMS_HOLD" for e in s.open_exceptions()):
                reasons.append("the inbound container is on a customs hold")
            elif s.is_import and s.has("vessel_arrived") and not s.has("customs_released"):
                reasons.append("the inbound container is waiting for customs release")
            if not s.has("vessel_arrived") and not s.has("warehouse_received"):
                cur = s.current_eta()
                reasons.append(f"the inbound shipment has not arrived (verified ETA {cur.eta})" if cur
                               else "the inbound shipment has not arrived and has no verified ETA")
                if s.eta_delay_days() > 0:
                    reasons.append(f"that shipment is running {s.eta_delay_days()} day(s) late")
            elif not s.has("warehouse_received"):
                reasons.append("the goods have not yet been received at the warehouse")
            if any(e.code in ("RECEIPT_DAMAGE", "RECEIPT_SHORTAGE") for e in s.open_exceptions()):
                reasons.append("the receipt had damage/shortage that may affect this allocation")
            if a["status"] == "out_for_delivery":
                reasons.append(f"it is out for delivery (tracking {a.get('tracking_number') or 'n/a'})")
            if not reasons:
                reasons.append("it is allocated but not yet dispatched")
            late = a.get("promised_date") and _parse_date(a["promised_date"]) and \
                _parse_date(a["promised_date"]) < today
            out.append(f"Order {a['order_id']} ({a['qty_cases']} cs {a['sku']}) is not delivered because "
                       + "; ".join(reasons) + "."
                       + (f" It was promised for {a['promised_date']}." if late else ""))
            data.append({"order_id": a["order_id"], "status": a["status"], "reasons": reasons,
                         "promised_date": a.get("promised_date")})
        return AssistantAnswer("customer_not_received", " ".join(out), {"orders": data},
                               ["verified milestones and carrier/WMS delivery updates"],
                               [self._link(s) for s, _ in matches],
                               suggested_actions=["Send the customer update draft after review"])

    def _in_transit_inventory(self, question, ident, today) -> AssistantAnswer:
        by_sku: dict[str, dict] = defaultdict(lambda: {"in_transit": 0, "at_port": 0, "on_order": 0})
        detail = []
        for s in self._visible():
            if s.cancelled or s.has("warehouse_received"):
                continue
            d = self.viewer.project(s)
            if s.has("vessel_arrived") or s.has("gate_out_port"):
                bucket = "at_port"
            elif s.has("vessel_departed") or s.has("gate_in_origin"):
                bucket = "in_transit"
            else:
                bucket = "on_order"
            for l in d["lines"]:
                by_sku[l["sku"]][bucket] += l["qty_cases"]
            if bucket != "on_order":
                detail.append({"shipment_id": s.shipment_id, "bucket": bucket,
                               "eta": s.current_eta().eta if s.current_eta() else None,
                               "cases": sum(l["qty_cases"] for l in d["lines"])})
        moving = {k: v for k, v in by_sku.items() if v["in_transit"] or v["at_port"]}
        if not moving:
            return AssistantAnswer("in_transit_inventory", "Nothing is currently in transit (verified departures).",
                                   {"by_sku": dict(by_sku)})
        txt = "In transit: " + "; ".join(
            f"{sku} {v['in_transit']} cs on the water" + (f", {v['at_port']} cs at port" if v["at_port"] else "")
            for sku, v in sorted(moving.items())) + "."
        return AssistantAnswer("in_transit_inventory", txt, {"by_sku": dict(by_sku), "shipments": detail},
                               ["in transit = verified departure and not yet received into the warehouse"],
                               [self._link(self.ledger.get(x["shipment_id"])) for x in detail])

    def _proof_of_delivery(self, question, ident, today) -> AssistantAnswer:
        with_pod, without = [], []
        for s in self._visible():
            d = self.viewer.project(s)
            for a in d["allocations"]:
                if a["status"] != "delivered":
                    continue
                row = {"order_id": a["order_id"], "customer": a.get("customer_name"),
                       "delivered_at": a["delivered_at"], "shipment_id": s.shipment_id,
                       "pod_doc_id": a.get("pod_doc_id")}
                (with_pod if a.get("pod_doc_id") else without).append(row)
        txt = (f"{len(with_pod)} delivered order(s) have proof of delivery"
               + (f" ({', '.join(r['order_id'] for r in with_pod)})" if with_pod else "")
               + f"; {len(without)} delivered order(s) are missing POD"
               + (f" ({', '.join(r['order_id'] for r in without)})" if without else "") + ".")
        return AssistantAnswer("proof_of_delivery", txt, {"with_pod": with_pod, "missing_pod": without},
                               ["POD = carrier ePOD or a reviewed POD document"],
                               suggested_actions=["Request missing PODs from the delivery carrier"] if without else [])

    def _landed_cost(self, question, ident, today) -> AssistantAnswer:
        if not self.viewer.can_view_commercial:
            return AssistantAnswer("landed_cost", "Landed cost is restricted to users with financial access.",
                                   restricted=["landed cost requires view_financials"])
        targets = self._resolve(ident) if ident else [s for s in self._visible() if s.costs]
        if not targets:
            return AssistantAnswer("landed_cost", "I don't have cost data for that shipment yet."
                                   if ident else "No shipments have cost data yet.")
        rows = [landed_cost(s) for s in targets]
        parts = []
        for r in rows:
            p = f"PO {', '.join(r['po_numbers']) or r['shipment_id']}: "
            p += (f"${r['landed_cost_per_case_usd']:,.2f}/case" if r["landed_cost_per_case_usd"] is not None
                  else "n/a")
            if r["landed_cost_per_saleable_case_usd"]:
                p += f" (${r['landed_cost_per_saleable_case_usd']:,.2f} per saleable case received)"
            p += f" — {r['status'].upper()}"
            if r["missing_components"]:
                p += f"; missing {', '.join(r['missing_components'])}"
            if r["unverified_components"]:
                p += f"; unverified {', '.join(r['unverified_components'])}"
            parts.append(p)
        return AssistantAnswer("landed_cost", "; ".join(parts) + ".", {"landed_costs": rows},
                               ["PO unit cost + freight/customs cost lines on the ledger"],
                               [self._link(s) for s in targets])

    def _supplier_responsibility(self, question, ident, today) -> AssistantAnswer:
        if self.viewer.tier == "sales":
            return AssistantAnswer("supplier_responsibility",
                                   "Supplier claims and performance are not available for your role.",
                                   restricted=["supplier data requires logistics or management access"])
        rows = []
        for s in self._visible():
            r = s.receipt
            if not r or not (r.get("damaged_cases") or r.get("short_cases")):
                continue
            rows.append({"shipment_id": s.shipment_id, "supplier": s.supplier_name or s.supplier_id,
                         "po_numbers": s.po_numbers, "damaged_cases": r.get("damaged_cases", 0),
                         "short_cases": r.get("short_cases", 0), "likely_responsible": r.get("liability"),
                         "claim_ids": r.get("claim_ids", [])})
        if not rows:
            return AssistantAnswer("supplier_responsibility", "No receipts have recorded shortages or damage.")
        txt = "; ".join(
            f"PO {', '.join(r['po_numbers'])} from {r['supplier']}: {r['damaged_cases']} damaged, "
            f"{r['short_cases']} short — likely responsible: {r['likely_responsible']}"
            for r in rows)
        restricted = [] if self.viewer.can_view_commercial else ["claim values require financial access"]
        return AssistantAnswer("supplier_responsibility", txt + ".", {"receipts": rows},
                               ["receiving reconciliation + liability assessment (evidence-based, reviewable)"],
                               [self._link(self.ledger.get(r["shipment_id"])) for r in rows], restricted,
                               ["Review the claim drafts before anything is sent"])

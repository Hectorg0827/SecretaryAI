"""
§25 — Returns Management & Reverse Logistics.

Returns intelligence dashboard: categorization, root cause, financial impact,
supplier quality claim automation, and restocking disposition decisions.

Returns taxonomy (wholesale distribution)
──────────────────────────────────────────
  DEFECTIVE        — product arrived damaged or failed during normal use
  WRONG_ITEM       — distributor shipped incorrect SKU or quantity
  OVERSTOCK        — customer over-ordered; returning excess
  SEASONAL_CLOSE   — end of season / seasonal item no longer needed
  CUSTOMER_CANCEL  — customer cancelled order after delivery
  QUALITY_CONCERN  — customer identified quality issue (not yet confirmed defective)
  EXPIRED          — product expired before sale
  DAMAGED_IN_TRANSIT — carrier damage

Disposition options
───────────────────
  RESTOCK          — inspect + return to saleable inventory
  VENDOR_RETURN    — ship back to supplier for credit
  LIQUIDATE        — sell below cost through secondary channel
  DESTROY          — dispose (biohazard, expired, unrecoverable)
  REFURBISH        — repair for resale (where applicable)

Supplier Quality Claim
──────────────────────
  Triggered automatically when DEFECTIVE or DAMAGED_IN_TRANSIT returns
  exceed supplier's agreed defect threshold.
  Includes: return units, dollar value, lot numbers, photos (if available),
            reference to purchase order(s).
"""

from __future__ import annotations

import hashlib
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional


class ReturnCategory(str, Enum):
    UNCLASSIFIED = "unclassified"
    DEFECTIVE = "defective"
    WRONG_ITEM = "wrong_item"
    OVERSTOCK = "overstock"
    SEASONAL_CLOSE = "seasonal_close"
    CUSTOMER_CANCEL = "customer_cancel"
    QUALITY_CONCERN = "quality_concern"
    EXPIRED = "expired"
    DAMAGED_IN_TRANSIT = "damaged_in_transit"


class DispositionDecision(str, Enum):
    HOLD_FOR_INSPECTION = "hold_for_inspection"
    RESTOCK = "restock"
    VENDOR_RETURN = "vendor_return"
    LIQUIDATE = "liquidate"
    DESTROY = "destroy"
    REFURBISH = "refurbish"


@dataclass
class ReturnRecord:
    """A single return event."""
    return_id: str
    customer_id: str
    supplier_id: str
    sku: str
    units: int
    unit_cost: float
    return_reason: str          # raw string from customer
    category: Optional[ReturnCategory] = None
    lot_number: Optional[str] = None
    po_number: Optional[str] = None
    return_date: str = ""
    disposition: Optional[DispositionDecision] = None
    credit_issued: bool = False
    supplier_claim_filed: bool = False

    @property
    def return_value(self) -> float:
        return self.units * self.unit_cost

    def to_dict(self) -> dict:
        return {
            "return_id": self.return_id,
            "customer_id": self.customer_id,
            "supplier_id": self.supplier_id,
            "sku": self.sku,
            "units": self.units,
            "unit_cost": self.unit_cost,
            "return_value": round(self.return_value, 2),
            "return_reason": self.return_reason,
            "category": self.category.value if self.category else None,
            "lot_number": self.lot_number,
            "po_number": self.po_number,
            "return_date": self.return_date,
            "disposition": self.disposition.value if self.disposition else None,
            "credit_issued_claimed": self.credit_issued,
            "supplier_claim_filed_claimed": self.supplier_claim_filed,
            "credit_status_verified": False,
            "supplier_claim_status_verified": False,
            "disposition_status_verified": False,
            "source_evidence_verified": False,
            "status": "analysis_only",
        }


@dataclass
class ReturnsMetrics:
    """Aggregate returns performance metrics for a period."""
    total_returns: int
    total_return_value: float
    return_rate_pct: Optional[float]    # unknown if revenue unavailable
    avg_processing_time_days: Optional[float]

    by_category: dict[str, dict]        # category → {count, value, pct_of_returns}
    by_supplier: dict[str, dict]        # supplier → {count, value, defect_rate}
    by_sku: dict[str, dict]             # sku → {count, value}
    by_customer: dict[str, dict]        # customer → {count, value}

    top_return_drivers: list[str]       # ranked causal explanations
    financial_impact: dict              # gross exposure, recovery, net cost

    def to_dict(self) -> dict:
        return {
            "total_returns": self.total_returns,
            "total_return_value": round(self.total_return_value, 2),
            "return_rate_pct": round(self.return_rate_pct, 2) if self.return_rate_pct is not None else None,
            "avg_processing_time_days": round(self.avg_processing_time_days, 1) if self.avg_processing_time_days is not None else None,
            "return_rate_basis_verified": False,
            "processing_time_observed": False,
            "status": "analysis_only",
            "source_data_verified": False,
            "requires_human_review": True,
            "by_category": self.by_category,
            "by_supplier": self.by_supplier,
            "by_sku": self.by_sku,
            "by_customer": self.by_customer,
            "top_return_drivers": self.top_return_drivers,
            "financial_impact": {
                k: round(v, 2) if type(v) in (int, float) else v
                for k, v in self.financial_impact.items()
            },
        }


@dataclass
class SupplierQualityClaim:
    """Automated supplier quality claim document."""
    claim_id: str
    supplier_id: str
    supplier_name: str
    claim_date: str
    period_start: str
    period_end: str
    defective_units: int
    defective_value: float
    defect_rate_pct: float
    agreed_defect_threshold_pct: float
    excess_defect_units: int
    po_numbers: list[str]
    lot_numbers: list[str]
    return_records: list[str]           # return_ids
    claim_amount: float                 # what we're asking the supplier to credit
    claim_basis: str                    # credit | replacement | discount
    narrative: str

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "supplier_id": self.supplier_id,
            "supplier_name": self.supplier_name,
            "claim_date": self.claim_date,
            "period": {"start": self.period_start, "end": self.period_end},
            "defective_units": self.defective_units,
            "defective_value": round(self.defective_value, 2),
            "defect_rate_pct": round(self.defect_rate_pct, 2),
            "agreed_threshold_pct": round(self.agreed_defect_threshold_pct, 2),
            "excess_defect_units": self.excess_defect_units,
            "po_numbers": self.po_numbers,
            "lot_numbers": self.lot_numbers,
            "claim_amount": round(self.claim_amount, 2),
            "claim_basis": self.claim_basis,
            "narrative": self.narrative,
            "status": "analysis_only",
            "source_evidence_verified": False,
            "supplier_terms_verified": False,
            "claim_filed": False,
            "credit_issued": False,
            "financial_write_performed": False,
            "requires_human_review": True,
            "idempotency_enforced_by_ledger": False,
        }


# ─── Returns Management Engine ────────────────────────────────────────────────


class ReturnsManagementEngine:
    """
    Classifies returns, determines disposition, computes metrics,
    and generates supplier quality claims.
    """

    # Keywords that map return reasons to categories
    CATEGORY_KEYWORDS: dict[ReturnCategory, list[str]] = {
        ReturnCategory.DEFECTIVE: [
            "defect", "defects", "defective", "broken", "broke", "malfunction",
            "malfunctions", "malfunctioned", "malfunctioning",
            "fail", "fails", "failed", "failing", "failure", "doesn't work",
            "does not work", "not working", "isn't working", "stopped working",
            "won't work", "won't turn on", "will not turn on", "inoperable",
            "DOA", "dead on arrival", "faulty",
        ],
        ReturnCategory.WRONG_ITEM: [
            "wrong item", "wrong sku", "incorrect", "not what i ordered", "wrong product",
            "wrong size", "wrong flavor", "wrong color",
        ],
        ReturnCategory.OVERSTOCK: [
            "overstock", "over-order", "too much", "excess inventory", "surplus",
            "ordered too many",
        ],
        ReturnCategory.SEASONAL_CLOSE: [
            "season", "end of season", "seasonal", "no longer needed",
        ],
        ReturnCategory.CUSTOMER_CANCEL: [
            "cancel", "canceled", "cancelled", "canceling", "cancelling",
            "cancellation", "no longer want", "changed mind", "don't need",
        ],
        ReturnCategory.QUALITY_CONCERN: [
            "quality", "concern", "suspect", "looks off", "doesn't look right",
            "color", "smell", "texture",
        ],
        ReturnCategory.EXPIRED: [
            "expired", "expiry", "best by", "use by", "stale",
        ],
        ReturnCategory.DAMAGED_IN_TRANSIT: [
            "damaged", "transit", "shipping damage", "arrived damaged", "box crushed",
            "carrier", "UPS", "FedEx", "freight damage",
        ],
    }

    # A keyword preceded by one of these words in the same clause is negated
    # ("not defective", "no defects", "non-defective").
    NEGATION_CUES = frozenset({
        "not", "no", "non", "never", "without", "isn't", "wasn't", "aren't",
        "weren't", "don't", "doesn't", "didn't", "hasn't", "haven't",
    })
    NEGATION_WINDOW_WORDS = 3
    _CLAUSE_BOUNDARY = re.compile(
        r"[.,;:!?()\[\]]|\s-+\s|[–—]|\b(?:but|however|although|though)\b"
    )

    def __init__(self, company_id: str):
        self.company_id = company_id

    # ── Classification ───────────────────────────────────────────────────────

    def classify_return(self, record: ReturnRecord) -> ReturnCategory:
        """Classify a return record by matching reason text to category keywords.

        Whole words only; a longer phrase owns the words inside it ("wrong
        color" is not a colour complaint); negated phrases are ignored; and
        conflicting evidence stays UNCLASSIFIED for inspection instead of
        being settled by keyword-table order. A false DEFECTIVE label feeds
        supplier defect rates and supplier-claim candidates.
        """
        if not isinstance(record.return_reason, str):
            raise ValueError("Return reason must be text")
        reason = re.sub(r"[‘’ʼ`]", "'", record.return_reason.lower())

        matches: list[tuple[int, int, ReturnCategory, str]] = []
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            for kw in keywords:
                pattern = r"(?<!\w)" + re.escape(kw.lower()) + r"(?!\w)"
                for match in re.finditer(pattern, reason):
                    matches.append((match.start(), match.end(), category, kw.lower()))

        claimed: list[tuple[int, int]] = []
        evidence: dict[ReturnCategory, set[str]] = {}
        for start, end, category, kw in sorted(matches, key=lambda m: (m[0] - m[1], m[0])):
            if any(start < used_end and used_start < end for used_start, used_end in claimed):
                continue
            claimed.append((start, end))
            if not self._is_negated(reason, start):
                evidence.setdefault(category, set()).add(kw)

        if not evidence:
            return ReturnCategory.UNCLASSIFIED  # no inferred commercial justification
        best = max(len(kws) for kws in evidence.values())
        leaders = [category for category, kws in evidence.items() if len(kws) == best]
        if len(leaders) != 1:
            return ReturnCategory.UNCLASSIFIED  # e.g. "defective" vs "cancelled"
        return leaders[0]

    def _is_negated(self, reason: str, start: int) -> bool:
        clause_start = 0
        for boundary in self._CLAUSE_BOUNDARY.finditer(reason, 0, start):
            clause_start = boundary.end()
        preceding = re.findall(r"[a-z0-9']+", reason[clause_start:start])
        return any(word in self.NEGATION_CUES for word in preceding[-self.NEGATION_WINDOW_WORDS:])

    # ── Disposition ───────────────────────────────────────────────────────────

    def recommend_disposition(
        self,
        record: ReturnRecord,
        product_type: str = "general",
        restocking_fee_pct: float = 15.0,
        days_since_sale: int = 0,
    ) -> DispositionDecision:
        """
        Recommend what to do with a returned item.
        """
        category = record.category or self.classify_return(record)

        if category == ReturnCategory.UNCLASSIFIED:
            return DispositionDecision.HOLD_FOR_INSPECTION

        # Expired product — destroy
        if category == ReturnCategory.EXPIRED:
            return DispositionDecision.DESTROY

        # Defective → try vendor return first; else destroy
        if category == ReturnCategory.DEFECTIVE:
            return DispositionDecision.VENDOR_RETURN

        # Damaged in transit → vendor return or destroy depending on extent
        if category == ReturnCategory.DAMAGED_IN_TRANSIT:
            return DispositionDecision.VENDOR_RETURN

        # Food / perishable — can't restock after customer had it
        perishable_types = {"food", "beverage", "perishable", "produce"}
        if any(pt in product_type.lower() for pt in perishable_types):
            return DispositionDecision.DESTROY

        # Overstock / seasonal / cancel: restock if recent, liquidate if old
        if category in (
            ReturnCategory.OVERSTOCK,
            ReturnCategory.SEASONAL_CLOSE,
            ReturnCategory.CUSTOMER_CANCEL,
        ):
            if days_since_sale > 180:
                return DispositionDecision.LIQUIDATE
            return DispositionDecision.RESTOCK

        # Wrong item — restock (it's our error)
        if category == ReturnCategory.WRONG_ITEM:
            return DispositionDecision.RESTOCK

        # Quality concern — inspect before decision; default to vendor return
        if category == ReturnCategory.QUALITY_CONCERN:
            return DispositionDecision.HOLD_FOR_INSPECTION

        return DispositionDecision.RESTOCK

    # ── Returns Dashboard Metrics ─────────────────────────────────────────────

    def compute_metrics(
        self,
        returns: list[ReturnRecord],
        total_revenue: float,
        avg_processing_days: Optional[float] = None,
        supplier_names: dict[str, str] | None = None,
    ) -> ReturnsMetrics:
        """Aggregate returns data into dashboard metrics."""
        self._validate_records(returns)
        if type(total_revenue) not in (int, float) or not math.isfinite(total_revenue) or total_revenue < 0:
            raise ValueError("Total revenue must be a finite nonnegative amount")
        if avg_processing_days is not None and (
            type(avg_processing_days) not in (int, float)
            or not math.isfinite(avg_processing_days) or avg_processing_days < 0
        ):
            raise ValueError("Processing duration must be finite and nonnegative")
        if not returns:
            return ReturnsMetrics(
                total_returns=0, total_return_value=0.0,
                return_rate_pct=0.0 if total_revenue > 0 else None,
                avg_processing_time_days=avg_processing_days,
                by_category={}, by_supplier={}, by_sku={}, by_customer={},
                top_return_drivers=[], financial_impact={},
            )

        total_value = sum(r.return_value for r in returns)
        return_rate = (total_value / total_revenue * 100) if total_revenue > 0 else None

        # ── By category ──────────────────────────────────────────────────────
        by_cat: dict[str, dict] = {}
        for r in returns:
            cat = (r.category or self.classify_return(r)).value
            if cat not in by_cat:
                by_cat[cat] = {"count": 0, "value": 0.0}
            by_cat[cat]["count"] += 1
            by_cat[cat]["value"] += r.return_value
        total_count = len(returns)
        for cat, data in by_cat.items():
            data["pct_of_returns"] = round(data["count"] / total_count * 100, 1)
            data["value"] = round(data["value"], 2)

        # ── By supplier ──────────────────────────────────────────────────────
        by_sup: dict[str, dict] = {}
        for r in returns:
            sid = r.supplier_id
            if sid not in by_sup:
                by_sup[sid] = {
                    "name": (supplier_names or {}).get(sid, sid),
                    "count": 0, "value": 0.0, "defective_count": 0
                }
            by_sup[sid]["count"] += 1
            by_sup[sid]["value"] += r.return_value
            if r.category in (ReturnCategory.DEFECTIVE, ReturnCategory.DAMAGED_IN_TRANSIT):
                by_sup[sid]["defective_count"] += 1
        for sid, data in by_sup.items():
            data["defect_rate_pct"] = round(
                data["defective_count"] / max(data["count"], 1) * 100, 1
            )
            data["defect_rate_basis"] = "share_of_return_records_not_received_units"
            data["value"] = round(data["value"], 2)

        # ── By SKU ───────────────────────────────────────────────────────────
        by_sku: dict[str, dict] = {}
        for r in returns:
            if r.sku not in by_sku:
                by_sku[r.sku] = {"count": 0, "value": 0.0}
            by_sku[r.sku]["count"] += 1
            by_sku[r.sku]["value"] += r.return_value
        for sku, data in by_sku.items():
            data["value"] = round(data["value"], 2)

        # ── By customer ──────────────────────────────────────────────────────
        by_cust: dict[str, dict] = {}
        for r in returns:
            if r.customer_id not in by_cust:
                by_cust[r.customer_id] = {"count": 0, "value": 0.0}
            by_cust[r.customer_id]["count"] += 1
            by_cust[r.customer_id]["value"] += r.return_value
        for cid, data in by_cust.items():
            data["value"] = round(data["value"], 2)

        # ── Top return drivers ────────────────────────────────────────────────
        sorted_cats = sorted(by_cat.items(), key=lambda kv: kv[1]["count"], reverse=True)
        drivers = []
        for cat_name, data in sorted_cats[:3]:
            readable = cat_name.replace("_", " ").title()
            drivers.append(
                f"{readable}: {data['count']} returns (${data['value']:,.0f}) — "
                f"{data['pct_of_returns']:.0f}% of all returns"
            )

        # ── Financial impact ─────────────────────────────────────────────────
        # Gross exposure = full return value
        # Recovery = value of restocked + vendor-credited items
        restockable_value = sum(
            r.return_value for r in returns
            if r.disposition in (DispositionDecision.RESTOCK, DispositionDecision.VENDOR_RETURN)
        )
        net_cost = total_value - restockable_value * 0.85  # assume 85% recovery on restocked
        handling_cost = len(returns) * 15  # $15/return handling estimate

        financial_impact = {
            "gross_return_exposure": total_value,
            "estimated_recovery": restockable_value * 0.85,
            "estimated_net_cost": net_cost + handling_cost,
            "handling_cost_estimate": handling_cost,
            "actual_recovery_amount": None,
            "actual_recovery_verified": False,
            "recovery_assumption": "illustrative_85pct_of_proposed_restock_or_vendor_return",
        }

        return ReturnsMetrics(
            total_returns=total_count,
            total_return_value=total_value,
            return_rate_pct=return_rate,
            avg_processing_time_days=avg_processing_days,
            by_category=by_cat,
            by_supplier=by_sup,
            by_sku=by_sku,
            by_customer=by_cust,
            top_return_drivers=drivers,
            financial_impact=financial_impact,
        )

    @staticmethod
    def _validate_records(returns: list[ReturnRecord]) -> None:
        """Reject duplicate, impossible or malformed caller observations."""
        ids: set[str] = set()
        for item in returns:
            if not isinstance(item, ReturnRecord):
                raise ValueError("Return observations must be typed records")
            if not isinstance(item.return_id, str) or not item.return_id.strip():
                raise ValueError("Return ID is required")
            if item.return_id in ids:
                raise ValueError("Duplicate return ID in the snapshot")
            ids.add(item.return_id)
            if type(item.units) is not int or item.units <= 0:
                raise ValueError("Return units must be a positive integer")
            if type(item.unit_cost) not in (int, float) or not math.isfinite(item.unit_cost) or item.unit_cost < 0:
                raise ValueError("Return unit cost must be finite and nonnegative")
            for label in ("customer_id", "supplier_id", "sku", "return_reason"):
                value = getattr(item, label)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"Return {label} must be nonempty text")

    # ── Supplier Quality Claim ────────────────────────────────────────────────

    def generate_supplier_claim(
        self,
        supplier_id: str,
        supplier_name: str,
        returns: list[ReturnRecord],
        total_units_received: int,
        period_start: str,
        period_end: str,
        agreed_defect_threshold_pct: float = 1.0,
        claim_basis: str = "credit",
    ) -> Optional[SupplierQualityClaim]:
        """
        Auto-generate a supplier quality claim if defect rate exceeds threshold.
        Returns None if no claim is warranted.
        """
        self._validate_records(returns)
        if type(total_units_received) is not int or total_units_received <= 0:
            raise ValueError("Positive verified-basis received units are required")
        if (type(agreed_defect_threshold_pct) not in (int, float)
                or not math.isfinite(agreed_defect_threshold_pct)
                or not 0 <= agreed_defect_threshold_pct <= 100):
            raise ValueError("Defect threshold must be a percentage in [0, 100]")
        if claim_basis not in {"credit", "replacement", "discount"}:
            raise ValueError("Unknown supplier claim basis")
        try:
            start = date.fromisoformat(period_start)
            end = date.fromisoformat(period_end)
        except (TypeError, ValueError) as exc:
            raise ValueError("Claim period must contain valid ISO dates") from exc
        if start > end:
            raise ValueError("Claim period start exceeds end")
        if not isinstance(supplier_id, str) or not supplier_id.strip():
            raise ValueError("Supplier ID is required")
        if not isinstance(supplier_name, str) or not supplier_name.strip():
            raise ValueError("Supplier name is required")
        for item in returns:
            try:
                observed_date = date.fromisoformat(item.return_date)
            except (TypeError, ValueError) as exc:
                raise ValueError("Return date must be independently checked as a valid ISO date") from exc
            if not start <= observed_date <= end:
                raise ValueError("Return event is outside the selected claim period")

        defective_returns = [
            r for r in returns
            if r.supplier_id == supplier_id
            and r.category in (ReturnCategory.DEFECTIVE, ReturnCategory.DAMAGED_IN_TRANSIT)
        ]

        if not defective_returns:
            return None

        defective_units = sum(r.units for r in defective_returns)
        if defective_units > total_units_received:
            raise ValueError("Defect units exceed received units; reconcile units and returns")
        defective_value = sum(r.return_value for r in defective_returns)
        defect_rate = (defective_units / max(total_units_received, 1)) * 100

        if defect_rate <= agreed_defect_threshold_pct:
            return None  # Within acceptable range — no claim

        excess_units = defective_units - int(total_units_received * agreed_defect_threshold_pct / 100)
        claim_amount = sum(r.return_value for r in defective_returns)

        po_numbers = sorted({r.po_number for r in defective_returns if r.po_number})
        lot_numbers = sorted({r.lot_number for r in defective_returns if r.lot_number})
        return_ids = sorted(r.return_id for r in defective_returns)
        candidate_key = "|".join([
            self.company_id, supplier_id, period_start, period_end,
            str(total_units_received), str(agreed_defect_threshold_pct), claim_basis,
            *(f"{r.return_id}:{r.units}:{r.unit_cost}:{r.po_number}:{r.lot_number}"
              for r in sorted(defective_returns, key=lambda item: item.return_id)),
        ])
        claim_id = "candidate-" + hashlib.sha256(candidate_key.encode("utf-8")).hexdigest()[:24]

        narrative = (
            f"During the period {period_start} to {period_end}, we received {defective_units} "
            f"defective units from {supplier_name} (supplier ID: {supplier_id}), representing a "
            f"defect rate of {defect_rate:.2f}%. This exceeds the agreed threshold of "
            f"{agreed_defect_threshold_pct:.1f}% by {excess_units} units. "
            f"We are requesting a {claim_basis} of ${claim_amount:,.2f} covering the full return "
            f"value of affected inventory. "
            f"Affected purchase orders: {', '.join(po_numbers) if po_numbers else 'see attached'}. "
            f"Please confirm receipt and advise on resolution timeline."
        )

        return SupplierQualityClaim(
            claim_id=claim_id,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            claim_date=datetime.now(timezone.utc).date().isoformat(),
            period_start=period_start,
            period_end=period_end,
            defective_units=defective_units,
            defective_value=defective_value,
            defect_rate_pct=defect_rate,
            agreed_defect_threshold_pct=agreed_defect_threshold_pct,
            excess_defect_units=max(0, excess_units),
            po_numbers=po_numbers,
            lot_numbers=lot_numbers,
            return_records=return_ids,
            claim_amount=claim_amount,
            claim_basis=claim_basis,
            narrative=narrative,
        )

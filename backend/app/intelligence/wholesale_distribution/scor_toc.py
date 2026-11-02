"""
§22 — SCOR Model Integration + Theory of Constraints.

SCOR (Supply Chain Operations Reference) organizes all supply chain activities
into six process domains: Plan, Source, Make, Deliver, Return, Enable.

Theory of Constraints (ToC) applies Goldratt's 5 Focusing Steps to identify
and eliminate bottlenecks in the distribution chain.

Together they provide:
  1. Structured performance metrics across all supply chain domains
  2. Automated bottleneck identification from observable queue depths
  3. Prioritized improvement recommendations

SCOR Metrics (Level 1)
──────────────────────
  Perfect Order Fulfillment (POF)
  Order Fulfillment Cycle Time (OFCT)
  Upside Supply Chain Flexibility
  Supply Chain Management Cost
  Return on Working Capital (ROWC)
  Cash-to-Cash Cycle Time (C2C)

ToC 5 Focusing Steps (automated)
─────────────────────────────────
  1. IDENTIFY   — find the constraint (highest queue depth / slowest throughput)
  2. EXPLOIT    — maximize throughput at the constraint without extra investment
  3. SUBORDINATE — subordinate all other processes to the constraint's pace
  4. ELEVATE    — invest to increase constraint capacity if needed
  5. REPEAT     — find the next constraint after resolving the current one
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SCORDomain(str, Enum):
    PLAN = "plan"
    SOURCE = "source"
    MAKE = "make"           # less relevant for pure distributors; kept for completeness
    DELIVER = "deliver"
    RETURN = "return"
    ENABLE = "enable"


class ConstraintType(str, Enum):
    SUPPLIER_LEAD_TIME = "supplier_lead_time"
    WAREHOUSE_THROUGHPUT = "warehouse_throughput"
    ORDER_PROCESSING = "order_processing"
    CASH_FLOW = "cash_flow"
    DEMAND_PLANNING = "demand_planning"
    CARRIER_CAPACITY = "carrier_capacity"
    CUSTOMS_CLEARANCE = "customs_clearance"
    NONE_IDENTIFIED = "none_identified"


# ─── SCOR Metrics ────────────────────────────────────────────────────────────


@dataclass
class SCORMetrics:
    """
    Level-1 SCOR metrics computed from observable data.
    All percentages are 0–100.
    """
    # Reliability
    perfect_order_fulfillment_pct: float = 0.0      # % orders complete, on-time, undamaged
    order_fill_rate_pct: float = 0.0                # % lines filled on first attempt

    # Responsiveness
    order_fulfillment_cycle_time_days: float = 0.0  # avg days from order to delivery
    supplier_lead_time_days: float = 0.0            # avg supplier lead time

    # Agility
    upside_flexibility_days: Optional[int] = None   # cannot infer from supplier sample size

    # Cost
    supply_chain_cost_pct_revenue: float = 0.0      # total SC cost as % of revenue
    cost_per_order_usd: float = 0.0

    # Assets
    cash_to_cash_cycle_days: float = 0.0            # DIO + DSO - DPO
    days_inventory_outstanding: float = 0.0         # DIO
    days_sales_outstanding: float = 0.0             # DSO
    days_payable_outstanding: float = 0.0           # DPO
    return_on_working_capital_pct: float = 0.0
    order_count: int = 0
    verified_delivery_orders: int = 0
    verified_line_orders: int = 0
    supplier_lead_time_observations: int = 0
    financial_ratios_supported: bool = False

    def to_dict(self) -> dict:
        return {
            "reliability": {
                "perfect_order_fulfillment_pct": round(self.perfect_order_fulfillment_pct, 1),
                "order_fill_rate_pct": round(self.order_fill_rate_pct, 1),
            },
            "responsiveness": {
                "order_fulfillment_cycle_time_days": round(self.order_fulfillment_cycle_time_days, 1),
                "supplier_lead_time_days": round(self.supplier_lead_time_days, 1),
            },
            "agility": {
                "upside_flexibility_days": self.upside_flexibility_days,
            },
            "cost": {
                "supply_chain_cost_pct_revenue": round(self.supply_chain_cost_pct_revenue, 2),
                "cost_per_order_usd": round(self.cost_per_order_usd, 2),
            },
            "assets": {
                "cash_to_cash_cycle_days": round(self.cash_to_cash_cycle_days, 1),
                "days_inventory_outstanding": round(self.days_inventory_outstanding, 1),
                "days_sales_outstanding": round(self.days_sales_outstanding, 1),
                "days_payable_outstanding": round(self.days_payable_outstanding, 1),
                "return_on_working_capital_pct": round(self.return_on_working_capital_pct, 1),
            },
            "evidence": {
                "order_count": self.order_count,
                "verified_delivery_orders": self.verified_delivery_orders,
                "verified_line_orders": self.verified_line_orders,
                "supplier_lead_time_observations": self.supplier_lead_time_observations,
                "financial_ratios_supported": self.financial_ratios_supported,
                "data_source_verified": False,
                "requires_human_review": True,
            },
        }

    def benchmark_vs_industry(self) -> dict[str, str]:
        """
        Rough industry benchmarks for wholesale distributors.
        Returns rating per metric: excellent | good | fair | poor
        """
        ratings: dict[str, str] = {}

        def rate(value: float, excellent: float, good: float, fair: float) -> str:
            if value >= excellent:
                return "excellent"
            if value >= good:
                return "good"
            if value >= fair:
                return "fair"
            return "poor"

        def rate_low(value: float, excellent: float, good: float, fair: float) -> str:
            """Lower is better."""
            if value <= excellent:
                return "excellent"
            if value <= good:
                return "good"
            if value <= fair:
                return "fair"
            return "poor"

        # Benchmarks need enough dated, complete observations; default zero is
        # not evidence of either outstanding or poor performance.
        delivery_ready = self.verified_delivery_orders >= 10 and (
            self.verified_delivery_orders / max(1, self.order_count) >= 0.8
        )
        line_ready = self.verified_line_orders >= 10 and (
            self.verified_line_orders / max(1, self.order_count) >= 0.8
        )
        ratings["perfect_order_fulfillment"] = (
            rate(self.perfect_order_fulfillment_pct, 97, 92, 85)
            if delivery_ready else "insufficient_data"
        )
        ratings["order_fill_rate"] = (
            rate(self.order_fill_rate_pct, 98, 95, 90)
            if line_ready else "insufficient_data"
        )
        ratings["order_cycle_time"] = (
            rate_low(self.order_fulfillment_cycle_time_days, 2, 5, 10)
            if delivery_ready else "insufficient_data"
        )
        ratings["cash_to_cash"] = (
            rate_low(self.cash_to_cash_cycle_days, 30, 45, 60)
            if self.financial_ratios_supported else "insufficient_data"
        )
        ratings["supply_chain_cost"] = (
            rate_low(self.supply_chain_cost_pct_revenue, 8, 12, 18)
            if self.financial_ratios_supported else "insufficient_data"
        )
        return ratings


# ─── ToC Constraint Model ─────────────────────────────────────────────────────


@dataclass
class ProcessNode:
    """A process node in the supply chain — could be a supplier, warehouse step, etc."""
    node_id: str
    name: str
    domain: SCORDomain
    throughput_per_day: float       # units or orders processed per day
    queue_depth: float              # current backlog (units or orders waiting)
    utilization_pct: float          # 0–100 — % of capacity in use
    avg_cycle_time_hours: float     # time to process one unit/order


@dataclass
class Constraint:
    """The identified bottleneck node."""
    node: ProcessNode
    constraint_type: ConstraintType
    severity: str               # low | medium | high | critical
    throughput_loss_pct: float  # how much system throughput is being lost (%)

    # ToC Step 2: Exploit recommendations (no extra investment)
    exploit_actions: list[str]

    # ToC Step 3: Subordination recommendations
    subordinate_actions: list[str]

    # ToC Step 4: Elevation recommendations (may require investment)
    elevate_actions: list[str]

    estimated_throughput_gain_pct: float    # if all actions are taken

    def to_dict(self) -> dict:
        return {
            "node_id": self.node.node_id,
            "node_name": self.node.name,
            "domain": self.node.domain.value,
            "constraint_type": self.constraint_type.value,
            "severity": self.severity,
            "throughput_loss_pct": round(self.throughput_loss_pct, 1),
            "utilization_pct": round(self.node.utilization_pct, 1),
            "queue_depth": self.node.queue_depth,
            "exploit_actions": self.exploit_actions,
            "subordinate_actions": self.subordinate_actions,
            "elevate_actions": self.elevate_actions,
            "estimated_throughput_gain_pct": round(self.estimated_throughput_gain_pct, 1),
            "gain_is_measured": False,
            "throughput_loss_is_measured": False,
            "confidence": "low_unverified_heuristic",
            "requires_human_review": True,
        }


@dataclass
class SCORToCReport:
    """Combined SCOR metrics + ToC constraint analysis report."""
    metrics: SCORMetrics
    benchmarks: dict[str, str]
    constraints: list[Constraint]           # ordered by severity
    primary_constraint: Optional[Constraint]
    improvement_priority: list[str]         # ordered list of recommended focus areas
    scor_domain_scores: dict[str, Optional[float]]    # 0–100 or unknown per domain
    process_node_observations: int = 0

    def to_dict(self) -> dict:
        return {
            "status": "analysis_only",
            "data_source_verified": False,
            "requires_human_review": True,
            "scor_metrics": self.metrics.to_dict(),
            "benchmarks": self.benchmarks,
            "scor_domain_scores": {k: round(v, 1) if v is not None else None
                                   for k, v in self.scor_domain_scores.items()},
            "primary_constraint": self.primary_constraint.to_dict() if self.primary_constraint else None,
            "all_constraints": [c.to_dict() for c in self.constraints],
            "improvement_priority": self.improvement_priority,
            "constraint_evidence": {
                "process_node_observations": self.process_node_observations,
                "queue_observations_verified": False,
                "cross_node_throughput_units_verified": False,
            },
        }


# ─── SCOR + ToC Engine ───────────────────────────────────────────────────────


class SCORToCEngine:
    """
    Computes SCOR Level-1 metrics and applies Theory of Constraints to identify
    and sequence improvement opportunities.
    """

    def __init__(self, company_id: str):
        self.company_id = company_id

    # ── SCOR Metric Computation ──────────────────────────────────────────────

    def compute_metrics(
        self,
        orders: list[dict],         # [{id, ordered_at, delivered_at, lines_filled, lines_ordered, damaged}]
        inventory_value: float,
        cogs: float,
        revenue: float,
        ar_balance: float,          # accounts receivable
        ap_balance: float,          # accounts payable
        supply_chain_costs: float,  # warehousing + freight + handling
        supplier_lead_times: list[float],  # days per PO
    ) -> SCORMetrics:
        """Compute all Level-1 SCOR metrics from raw business data."""
        m = SCORMetrics()
        financial_values = (
            inventory_value, cogs, revenue, ar_balance, ap_balance, supply_chain_costs
        )
        if any(not math.isfinite(value) or value < 0 for value in financial_values):
            raise ValueError("Financial observations must be finite and nonnegative")
        if any(not math.isfinite(v) or v <= 0 for v in supplier_lead_times):
            raise ValueError("Supplier lead-time observations must be finite and positive")
        m.order_count = len(orders)
        m.supplier_lead_time_observations = len(supplier_lead_times)
        m.financial_ratios_supported = revenue > 0 and cogs > 0

        if orders:
            # Perfect order fulfillment: on time + complete + undamaged
            perfect = sum(
                1 for o in orders
                if self._is_on_time(o)
                and self._valid_order_lines(o)
                and o["lines_filled"] >= o["lines_ordered"]
                and o.get("damaged") is False
            )
            m.perfect_order_fulfillment_pct = (perfect / len(orders)) * 100

            # Fill rate
            valid_lines = [o for o in orders if self._valid_order_lines(o)]
            m.verified_line_orders = len(valid_lines)
            total_lines = sum(o["lines_ordered"] for o in valid_lines)
            filled_lines = sum(o["lines_filled"] for o in valid_lines)
            if total_lines:
                m.order_fill_rate_pct = (filled_lines / total_lines) * 100

            # Cycle time
            cycle_times = [days for o in orders if self._is_on_time_evaluable(o)
                           if (days := self._cycle_days(o)) is not None]
            m.verified_delivery_orders = len(cycle_times)
            if cycle_times:
                m.order_fulfillment_cycle_time_days = statistics.mean(cycle_times)

        if supplier_lead_times:
            m.supplier_lead_time_days = statistics.mean(supplier_lead_times)

        if revenue and revenue > 0:
            m.supply_chain_cost_pct_revenue = (supply_chain_costs / revenue) * 100
            if orders:
                m.cost_per_order_usd = supply_chain_costs / len(orders)

        if cogs and cogs > 0:
            m.days_inventory_outstanding = (inventory_value / cogs) * 365

        if revenue and revenue > 0:
            m.days_sales_outstanding = (ar_balance / revenue) * 365

        if cogs and cogs > 0:
            m.days_payable_outstanding = (ap_balance / cogs) * 365

        m.cash_to_cash_cycle_days = (
            m.days_inventory_outstanding + m.days_sales_outstanding - m.days_payable_outstanding
        )

        # ROWC = (Revenue - COGS - SC costs) / Working Capital
        gross_profit = revenue - cogs - supply_chain_costs
        working_capital = inventory_value + ar_balance - ap_balance
        if working_capital > 0 and m.financial_ratios_supported:
            m.return_on_working_capital_pct = (gross_profit / working_capital) * 100

        # Agility needs tested surge capacity; lead-time sample count is not a proxy.
        m.upside_flexibility_days = None

        return m

    # ── ToC Bottleneck Identification ────────────────────────────────────────

    def identify_constraints(
        self, process_nodes: list[ProcessNode]
    ) -> list[Constraint]:
        """
        Apply ToC Step 1: Identify constraints from process node data.
        Returns constraints sorted by severity (critical first).
        """
        constraints: list[Constraint] = []

        for node in process_nodes:
            observations = (
                node.throughput_per_day, node.queue_depth,
                node.utilization_pct, node.avg_cycle_time_hours,
            )
            if any(not math.isfinite(value) for value in observations):
                raise ValueError("Process measurements must be finite")
            if not 0 <= node.utilization_pct <= 100 or node.queue_depth < 0:
                raise ValueError("Utilization and backlog must be within valid bounds")
            if node.throughput_per_day <= 0 or node.avg_cycle_time_hours < 0:
                raise ValueError("Process throughput must be positive and cycle time nonnegative")

            if node.utilization_pct < 80 or node.queue_depth == 0:
                continue  # Not a constraint

            severity = self._severity(node.utilization_pct)
            ct = self._map_constraint_type(node)
            throughput_loss = max(0, (node.utilization_pct - 85) / 15 * 100)  # 0–100

            exploit, subordinate, elevate = self._generate_toc_actions(node, ct)

            estimated_gain = min(throughput_loss * 0.6, 40)  # conservative: recover 60% of loss

            constraints.append(Constraint(
                node=node,
                constraint_type=ct,
                severity=severity,
                throughput_loss_pct=throughput_loss,
                exploit_actions=exploit,
                subordinate_actions=subordinate,
                elevate_actions=elevate,
                estimated_throughput_gain_pct=estimated_gain,
            ))

        constraints.sort(
            key=lambda c: {"critical": 4, "high": 3, "medium": 2, "low": 1}[c.severity],
            reverse=True,
        )
        return constraints

    def full_analysis(
        self,
        orders: list[dict],
        inventory_value: float,
        cogs: float,
        revenue: float,
        ar_balance: float,
        ap_balance: float,
        supply_chain_costs: float,
        supplier_lead_times: list[float],
        process_nodes: list[ProcessNode],
    ) -> SCORToCReport:
        metrics = self.compute_metrics(
            orders, inventory_value, cogs, revenue,
            ar_balance, ap_balance, supply_chain_costs, supplier_lead_times
        )
        benchmarks = metrics.benchmark_vs_industry()
        constraints = self.identify_constraints(process_nodes)
        primary = constraints[0] if constraints else None
        domain_scores = self._compute_domain_scores(metrics, benchmarks)
        priority = self._build_priority_list(benchmarks, constraints)

        return SCORToCReport(
            metrics=metrics,
            benchmarks=benchmarks,
            constraints=constraints,
            primary_constraint=primary,
            improvement_priority=priority,
            scor_domain_scores=domain_scores,
            process_node_observations=len(process_nodes),
        )

    # ── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _valid_order_lines(order: dict) -> bool:
        ordered = order.get("lines_ordered")
        filled = order.get("lines_filled")
        return (
            type(ordered) is int and type(filled) is int
            and ordered > 0 and 0 <= filled <= ordered
        )

    @staticmethod
    def _is_on_time_evaluable(order: dict) -> bool:
        """A missing, impossible or future-dated observation is not a delivered order."""
        try:
            from datetime import date
            ordered = date.fromisoformat(order["ordered_at"][:10])
            promised = date.fromisoformat(order["promised_at"][:10])
            delivered = date.fromisoformat(order["delivered_at"][:10])
            return ordered <= delivered and ordered <= promised
        except (KeyError, ValueError, TypeError):
            return False

    @staticmethod
    def _is_on_time(order: dict) -> bool:
        if not SCORToCEngine._is_on_time_evaluable(order):
            return False
        return order["delivered_at"][:10] <= order["promised_at"][:10]

    @staticmethod
    def _cycle_days(order: dict) -> Optional[float]:
        try:
            from datetime import date
            ordered = date.fromisoformat(order["ordered_at"][:10])
            delivered = date.fromisoformat(order["delivered_at"][:10])
            return max(0, (delivered - ordered).days)
        except (KeyError, ValueError, TypeError):
            return None

    @staticmethod
    def _severity(utilization_pct: float) -> str:
        if utilization_pct >= 98:
            return "critical"
        if utilization_pct >= 90:
            return "high"
        if utilization_pct >= 85:
            return "medium"
        return "low"

    @staticmethod
    def _map_constraint_type(node: ProcessNode) -> ConstraintType:
        name_lower = node.name.lower()
        if "supplier" in name_lower or "vendor" in name_lower or "lead" in name_lower:
            return ConstraintType.SUPPLIER_LEAD_TIME
        if "warehouse" in name_lower or "pick" in name_lower or "pack" in name_lower:
            return ConstraintType.WAREHOUSE_THROUGHPUT
        if "order" in name_lower or "process" in name_lower or "entry" in name_lower:
            return ConstraintType.ORDER_PROCESSING
        if "carrier" in name_lower or "freight" in name_lower or "ship" in name_lower:
            return ConstraintType.CARRIER_CAPACITY
        if "customs" in name_lower or "import" in name_lower or "border" in name_lower:
            return ConstraintType.CUSTOMS_CLEARANCE
        # "ar" must be a whole word: as a substring it matches "staging area",
        # "clearance" or "quarantine" and mislabels them as cash-flow constraints.
        if ("cash" in name_lower or "payment" in name_lower or "receivable" in name_lower
                or re.search(r"\bar\b", name_lower)):
            return ConstraintType.CASH_FLOW
        if "forecast" in name_lower or "plan" in name_lower or "demand" in name_lower:
            return ConstraintType.DEMAND_PLANNING
        return ConstraintType.NONE_IDENTIFIED

    @staticmethod
    def _generate_toc_actions(
        node: ProcessNode, ct: ConstraintType
    ) -> tuple[list[str], list[str], list[str]]:
        """Generate Step 2 (exploit), Step 3 (subordinate), Step 4 (elevate) actions."""
        exploit: list[str] = []
        subordinate: list[str] = []
        elevate: list[str] = []

        if ct == ConstraintType.SUPPLIER_LEAD_TIME:
            exploit = [
                "Place orders earlier to buffer within existing lead times",
                "Request partial shipments from supplier to reduce batch delay",
                "Identify alternative supplier for emergency coverage",
            ]
            subordinate = [
                "Set purchasing cycle to match supplier's optimal shipping schedule",
                "Align demand planning forecasts to give supplier 4-week advance notice",
            ]
            elevate = [
                "Dual-source the top 20% of SKUs by revenue",
                "Negotiate VMI (Vendor-Managed Inventory) agreement with primary supplier",
                "Invest in pre-positioning inventory in supplier's country (bonded warehouse)",
            ]

        elif ct == ConstraintType.WAREHOUSE_THROUGHPUT:
            exploit = [
                "Batch pick orders by zone to reduce travel time",
                "Schedule receiving during off-peak picking hours",
                "Move fastest-moving SKUs to most accessible bin locations",
            ]
            subordinate = [
                "Gate order releases to match warehouse throughput capacity",
                "Communicate warehouse schedule to sales team to set realistic promises",
            ]
            elevate = [
                "Add pick-to-light or voice-directed picking technology",
                "Add weekend shift or evening shift during peak periods",
                "Consider 3PL overflow arrangement for peak capacity",
            ]

        elif ct == ConstraintType.ORDER_PROCESSING:
            exploit = [
                "Automate order entry for top 5 customers via EDI or API",
                "Create order templates for repeat customers",
                "Eliminate approval steps for orders within pre-approved limits",
            ]
            subordinate = [
                "Batch manual order entry to dedicated focus time blocks",
                "Prioritize high-value orders and expedite requests in queue",
            ]
            elevate = [
                "Implement customer self-service portal for order placement",
                "Integrate ERP with customer purchasing systems",
            ]

        elif ct == ConstraintType.CARRIER_CAPACITY:
            exploit = [
                "Consolidate shipments going to same region",
                "Shift non-urgent shipments to off-peak carrier windows",
                "Use backup carriers for overflow capacity",
            ]
            subordinate = [
                "Communicate realistic lead times to customers based on carrier availability",
                "Stage orders at warehouse to match carrier pick-up schedule",
            ]
            elevate = [
                "Negotiate dedicated capacity agreement with primary carrier",
                "Add second primary carrier to reduce single-carrier dependency",
            ]

        elif ct == ConstraintType.CUSTOMS_CLEARANCE:
            exploit = [
                "Pre-file customs documents before shipment arrival",
                "Ensure all commercial invoices are complete and accurate",
                "Use a licensed customs broker for all imports",
            ]
            subordinate = [
                "Align purchase orders to provide broker with 48-hour lead time before arrival",
                "Build customs clearance time into customer delivery promises",
            ]
            elevate = [
                "Apply for C-TPAT or AEO trusted trader status",
                "Pursue bonded warehouse license to defer duty payment",
                "Implement automated HS code classification to reduce errors",
            ]

        elif ct == ConstraintType.CASH_FLOW:
            exploit = [
                "Accelerate AR collection — call customers with invoices > 30 days",
                "Negotiate extended payment terms (Net 60) with top suppliers",
                "Offer early payment discounts (2/10 net 30) to accelerate cash",
            ]
            subordinate = [
                "Align purchase order timing to cash flow forecast",
                "Limit new inventory purchases when AR > 45 days average",
            ]
            elevate = [
                "Establish revolving credit facility",
                "Explore invoice factoring or supply chain financing for large orders",
            ]

        else:
            exploit = [f"Review {node.name} process for quick-win optimizations"]
            subordinate = [f"Coordinate upstream/downstream processes to match {node.name} pace"]
            elevate = [f"Invest in capacity expansion at {node.name} if bottleneck persists"]

        return exploit, subordinate, elevate

    @staticmethod
    def _compute_domain_scores(
        metrics: SCORMetrics, benchmarks: dict[str, str]
    ) -> dict[str, Optional[float]]:
        rating_to_score = {"excellent": 95, "good": 75, "fair": 55, "poor": 30}

        plan_score = rating_to_score.get(benchmarks.get("order_fill_rate"))
        source_score = rating_to_score.get(benchmarks.get("order_cycle_time"))
        deliver_score = rating_to_score.get(benchmarks.get("perfect_order_fulfillment"))
        enable_score = rating_to_score.get(benchmarks.get("cash_to_cash"))
        cost_score = rating_to_score.get(benchmarks.get("supply_chain_cost"))
        valid_enable = [s for s in (enable_score, cost_score) if s is not None]

        return {
            "plan": plan_score,
            "source": source_score,
            "make": None,       # not measured for pure distributors
            "deliver": deliver_score,
            "return": None,     # no verified return-rate observation supplied
            "enable": statistics.mean(valid_enable) if valid_enable else None,
        }

    @staticmethod
    def _build_priority_list(
        benchmarks: dict[str, str], constraints: list[Constraint]
    ) -> list[str]:
        priority: list[str] = []

        # Lead with active constraints
        for c in constraints[:3]:
            priority.append(
                f"[CONSTRAINT] Resolve {c.node.name} bottleneck "
                f"({c.severity} severity, {c.estimated_throughput_gain_pct:.0f}% gain potential)"
            )

        # Then weak benchmark areas
        rating_to_score = {"excellent": 4, "good": 3, "fair": 2, "poor": 1}
        sorted_benchmarks = sorted(
            benchmarks.items(), key=lambda kv: rating_to_score.get(kv[1], 2)
        )
        for key, rating in sorted_benchmarks:
            if rating in ("fair", "poor"):
                readable = key.replace("_", " ").title()
                priority.append(f"[METRIC] Improve {readable} (currently: {rating})")

        incomplete = [k for k, v in benchmarks.items() if v == "insufficient_data"]
        if incomplete:
            priority.append(
                "[EVIDENCE] Collect verified order, financial and supplier observations for "
                + ", ".join(incomplete[:3])
            )
        return priority or ["Observed SCOR metrics within current benchmark bands; continue validating."]

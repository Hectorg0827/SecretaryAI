"""
IndustryModule — abstract base class that every industry module must implement.

The core platform (secretary, scoring engines, digest generator, alert engine)
never contains industry-specific logic. It reads everything it needs from the
loaded IndustryModule instance.

Swapping industries = loading a different module. Zero core code changes.

The 10 components from the product spec:
  1.  get_profile()            → Industry identity, business model, regulatory env
  2.  get_entity_schema()      → Entity types and field definitions
  3.  get_kpi_definitions()    → KPI names, formulas, thresholds, alert levels
  4.  get_scoring_config()     → Composite score weights and status boundaries
  5.  get_causal_trees()       → Decision trees for root-cause analysis
  6.  get_workflow_definitions()→ Step-sequence workflow templates
  7.  get_pricing_config()     → Landed cost, margin method, tier structure
  8.  get_compliance_calendar()→ License types, renewal schedules
  9.  get_terminology()        → Generic → industry-specific term mapping
  10. get_benchmarks()         → Industry-standard comparison metrics
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


# ─── Shared data structures ───────────────────────────────────────────────────

@dataclass
class IndustryProfile:
    name: str                        # e.g. "Wholesale Distribution"
    slug: str                        # e.g. "wholesale_distribution"
    description: str
    business_model: str              # e.g. "B2B", "B2B2C", "three-tier"
    unit_of_measure: str             # e.g. "cases", "units", "pallets", "tons"
    typical_order_cycle_days: int    # baseline for dormant detection
    primary_data_source: str         # e.g. "quickbooks", "netsuite", "sage"
    regulatory_bodies: list[str]
    default_language: str = "English"
    supported_languages: list[str] = field(default_factory=lambda: ["English"])


@dataclass
class KPIDefinition:
    name: str
    description: str
    formula: str                     # human-readable formula description
    unit: str                        # "days", "weeks", "percent", "dollars", "count"
    # Alert thresholds — values depend on unit
    warning_threshold: float
    critical_threshold: float
    # For range-based KPIs: which direction is bad
    alert_direction: str             # "above" | "below"
    # Which roles see this KPI in the digest
    applicable_roles: list[str]
    calculation_frequency: str       # "realtime" | "daily" | "weekly"


@dataclass
class ScoringConfig:
    """
    Defines how a composite 0-100 score is built.
    Each component has a weight and a set of penalty/bonus rules.
    """
    score_name: str
    components: list[dict]           # [{name, weight, rules: [{condition, penalty}]}]
    status_boundaries: dict[str, int]  # {"healthy": 75, "slowing": 50, "at_risk": 25}
    # Score boundaries are LOWER bounds: score >= boundary → that status
    # Listed highest-to-lowest: first match wins


@dataclass
class CausalNode:
    """One node in a causal decision tree."""
    id: str
    condition: str                   # human-readable condition description
    condition_key: str               # data field key to evaluate
    condition_op: str                # "gt", "lt", "eq", "contains", "missing"
    condition_value: Any
    # If condition is True:
    true_branch: Optional[str]       # id of next node, or None if terminal
    true_conclusion: Optional[str]   # root cause label if terminal
    true_action: Optional[str]       # recommended action if terminal
    # If condition is False:
    false_branch: Optional[str]
    false_conclusion: Optional[str]
    false_action: Optional[str]


@dataclass
class CausalTree:
    trigger: str                     # e.g. "account_health_drop"
    entity_type: str                 # e.g. "account", "inventory_item"
    root_node_id: str
    nodes: dict[str, CausalNode]     # id → node


@dataclass
class WorkflowStep:
    step_id: str
    name: str
    description: str
    trigger: str                     # what kicks off this step
    action_type: str                 # maps to ActionEngine action types
    autonomy_tier: int               # 1=inform, 2=draft, 3=act, 4=autonomous
    next_step_on_success: Optional[str]
    next_step_on_failure: Optional[str]
    timeout_hours: Optional[int]


@dataclass
class WorkflowDefinition:
    name: str
    slug: str
    description: str
    steps: list[WorkflowStep]


@dataclass
class PricingConfig:
    margin_method: str               # "gross_up" | "markup" | "margin_pct"
    standard_tiers: list[dict]       # [{name, min_revenue, discount_pct}]
    landed_cost_components: list[str]  # ["product_cost", "freight", "duties", "insurance"]
    excise_tax_applicable: bool
    price_elasticity_model: str      # "none" | "linear" | "tiered"


@dataclass
class ComplianceItem:
    name: str
    license_type: str
    issuing_body: str
    renewal_period_months: int
    alert_days_before: list[int]     # e.g. [90, 60, 30, 7]
    applicable_states: list[str]     # empty = federal/all states
    documentation_required: list[str]


@dataclass
class IndustryBenchmark:
    metric_name: str
    description: str
    industry_average: float
    top_quartile: float
    unit: str
    source: str
    # Impact statement template: use {company_value} and {impact_value}
    impact_template: str


# ─── Abstract base ────────────────────────────────────────────────────────────

class IndustryModule(ABC):
    """
    Abstract base class for all industry modules.

    Every method returns plain Python data structures (dataclasses or dicts)
    that the core platform reads to drive its behavior.
    The core platform never imports from a specific module — it only calls
    this interface.
    """

    # ── Component 1: Industry Profile ─────────────────────────────────────────

    @abstractmethod
    def get_profile(self) -> IndustryProfile:
        """Return the fundamental identity and parameters of this industry."""
        ...

    # ── Component 2: Entity Schema ────────────────────────────────────────────

    @abstractmethod
    def get_entity_schema(self) -> dict[str, dict]:
        """
        Return entity type definitions.
        Format: {entity_type: {field: description}}
        Example: {"account": {"name": "str", "tier": "str", "territory": "str"}}
        """
        ...

    # ── Component 3: KPI Definitions ─────────────────────────────────────────

    @abstractmethod
    def get_kpi_definitions(self) -> dict[str, KPIDefinition]:
        """
        Return all KPI definitions keyed by KPI name.
        The KPI engine reads these to evaluate alerts.
        """
        ...

    # ── Component 4: Scoring Config ───────────────────────────────────────────

    @abstractmethod
    def get_scoring_config(self) -> dict[str, ScoringConfig]:
        """
        Return scoring configurations keyed by score name.
        Example keys: "account_health", "brand_vitality", "supplier_score"
        """
        ...

    # ── Component 5: Causal Trees ─────────────────────────────────────────────

    @abstractmethod
    def get_causal_trees(self) -> dict[str, CausalTree]:
        """
        Return causal decision trees keyed by trigger name.
        The CausalDecompositionEngine walks these to find root causes.
        """
        ...

    # ── Component 6: Workflow Definitions ────────────────────────────────────

    @abstractmethod
    def get_workflow_definitions(self) -> dict[str, WorkflowDefinition]:
        """
        Return workflow definitions keyed by slug.
        The WorkflowEngine executes these step sequences.
        """
        ...

    # ── Component 7: Pricing Config ───────────────────────────────────────────

    @abstractmethod
    def get_pricing_config(self) -> PricingConfig:
        """Return how pricing works in this industry."""
        ...

    # ── Component 8: Compliance Calendar ────────────────────────────────────

    @abstractmethod
    def get_compliance_calendar(self) -> list[ComplianceItem]:
        """
        Return all compliance and licensing requirements.
        The compliance tracker uses these to generate renewal alerts.
        """
        ...

    # ── Component 9: Terminology Dictionary ──────────────────────────────────

    @abstractmethod
    def get_terminology(self) -> dict[str, str]:
        """
        Map generic platform terms → industry-specific language.
        The TerminologyTranslator uses this to localize all text output.

        Standard generic keys the platform uses:
          unit_of_measure, account, supplier, product, order, invoice,
          dormant_threshold_label, critical_stock_label, low_stock_label,
          sell_rate_label, lead_time_label, reorder_label
        """
        ...

    # ── Component 10: Benchmarks ──────────────────────────────────────────────

    @abstractmethod
    def get_benchmarks(self) -> list[IndustryBenchmark]:
        """
        Return industry-standard benchmark metrics.
        The BenchmarkComparator uses these to contextualize company performance.
        """
        ...

    # ── Convenience helpers (non-abstract, built from abstract methods) ───────

    def get_kpi(self, name: str) -> Optional[KPIDefinition]:
        return self.get_kpi_definitions().get(name)

    def translate(self, generic_term: str) -> str:
        """Translate a generic term to this industry's language."""
        return self.get_terminology().get(generic_term, generic_term)

    def get_status_boundary(self, score_name: str, status: str) -> int:
        """Return the lower score boundary for a given status label."""
        config = self.get_scoring_config().get(score_name)
        if not config:
            # Sensible defaults if module doesn't define this score
            defaults = {"healthy": 75, "slowing": 50, "at_risk": 25}
            return defaults.get(status, 0)
        return config.status_boundaries.get(status, 0)

    def get_system_prompt_context(self) -> str:
        """
        Return the industry expertise block injected into the AI system prompt.
        Default implementation builds from profile and terminology.
        Modules can override for richer prompts.
        """
        profile = self.get_profile()
        terms = self.get_terminology()
        compliance = self.get_compliance_calendar()
        compliance_names = ", ".join(c.name for c in compliance[:3])

        return f"""- {profile.description}
- {profile.business_model} business model, primary data source: {profile.primary_data_source}
- Units tracked in: {profile.unit_of_measure}
- Key terminology: {terms.get('account', 'account')} (customer), {terms.get('supplier', 'supplier')} (vendor), {terms.get('order', 'order')} (transaction)
- Regulatory environment: {', '.join(profile.regulatory_bodies) if profile.regulatory_bodies else 'Standard commercial'}
- Key compliance areas: {compliance_names if compliance_names else 'Standard business compliance'}"""

    def get_intent_categories(self) -> dict[str, str]:
        """
        Return intent classification categories for this industry.
        Keys are category slugs, values are descriptions for the classifier prompt.
        Default provides universal categories; override to add industry-specific ones.
        """
        return {
            "inventory_check": f"asking about {self.translate('unit_of_measure')} levels, supply, or product quantities",
            "account_status": f"asking about a specific {self.translate('account')} or customer",
            "sales_report": "requesting sales data, trends, or comparisons",
            "order_inquiry": f"asking about {self.translate('order')}s or {self.translate('supplier')} orders",
            "alert_check": "asking about alerts or issues flagged by the system",
            "action_request": "asking the AI to DO something (draft email, create PO, etc.)",
            "compliance_check": "asking about licenses, permits, regulatory status",
            "general_question": "general business questions, advice, or anything else",
        }

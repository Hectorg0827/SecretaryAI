"""
Wholesale Distribution Industry Module — Module #1.

Encodes the operational intelligence for small-to-mid-size wholesale
distributors and importers (beverage, food, general goods).

This module IS the product's intelligence for this vertical.
Swap it for a different module to serve a different industry.
"""
from __future__ import annotations

from app.industry_modules.base import (
    IndustryModule,
    IndustryProfile,
    KPIDefinition,
    ScoringConfig,
    CausalTree,
    CausalNode,
    WorkflowDefinition,
    WorkflowStep,
    PricingConfig,
    ComplianceItem,
    IndustryBenchmark,
)


class WholesaleDistributionModule(IndustryModule):
    """
    Industry intelligence for wholesale distribution and importing.
    Covers beverage importers, food distributors, and general goods wholesalers.
    """

    # ── Component 1: Profile ──────────────────────────────────────────────────

    def get_profile(self) -> IndustryProfile:
        return IndustryProfile(
            name="Wholesale Distribution",
            slug="wholesale_distribution",
            description=(
                "Wholesale distribution and import/export operations. "
                "Covers beverage importers, food distributors, and general goods wholesalers "
                "operating in B2B sales to retailers, restaurants, and other businesses."
            ),
            business_model="B2B / Three-tier distribution",
            unit_of_measure="cases",
            typical_order_cycle_days=30,
            primary_data_source="quickbooks",
            regulatory_bodies=["TTB", "FDA", "CBP", "State Liquor Authorities"],
            default_language="English",
            supported_languages=["English", "Spanish"],
        )

    # ── Component 2: Entity Schema ────────────────────────────────────────────

    def get_entity_schema(self) -> dict[str, dict]:
        return {
            "account": {
                "name": "str — retailer, restaurant, or distributor name",
                "tier": "str — A/B/C account tier based on volume",
                "territory": "str — geographic sales territory",
                "license_number": "str — retail liquor license (if applicable)",
                "credit_limit": "decimal — approved credit line",
                "payment_terms": "str — net30, net15, COD, etc.",
            },
            "product": {
                "name": "str — product name",
                "sku": "str — stock keeping unit",
                "brand": "str — brand name",
                "supplier": "str — importer or manufacturer",
                "case_pack": "int — bottles/units per case",
                "category": "str — beer/wine/spirits/non-alc",
            },
            "order": {
                "customer_name": "str",
                "order_date": "date",
                "total_amount": "decimal",
                "items": "list[{name, quantity, unit_price, amount}]",
                "status": "str — open/paid/overdue",
            },
            "container": {
                "container_number": "str",
                "supplier": "str",
                "eta": "date",
                "customs_status": "str",
                "contents": "list[{product, cases}]",
            },
        }

    # ── Component 3: KPI Definitions ─────────────────────────────────────────

    def get_kpi_definitions(self) -> dict[str, KPIDefinition]:
        return {
            "dormant_account_days": KPIDefinition(
                name="Dormant Account",
                description="Number of days since an account last placed an order",
                formula="today - last_order_date",
                unit="days",
                warning_threshold=45.0,    # Pre-dormant warning
                critical_threshold=60.0,   # Fully dormant
                alert_direction="above",
                applicable_roles=["owner", "manager", "sales_rep"],
                calculation_frequency="daily",
            ),
            "inventory_critical_weeks": KPIDefinition(
                name="Critical Inventory Threshold",
                description="Weeks of supply at which inventory is flagged CRITICAL",
                formula="quantity_on_hand / weekly_sell_rate",
                unit="weeks",
                warning_threshold=4.0,     # LOW status
                critical_threshold=2.0,    # CRITICAL status
                alert_direction="below",
                applicable_roles=["owner", "manager", "back_office"],
                calculation_frequency="daily",
            ),
            "high_balance_threshold": KPIDefinition(
                name="High Outstanding Balance",
                description="Customer balance that triggers a payment flag",
                formula="sum(open_invoice_balances)",
                unit="dollars",
                warning_threshold=5000.0,
                critical_threshold=10000.0,
                alert_direction="above",
                applicable_roles=["owner", "manager", "back_office"],
                calculation_frequency="daily",
            ),
            "order_decline_pct": KPIDefinition(
                name="Order Value Decline",
                description="Percentage drop in recent order values vs historical average",
                formula="(recent_avg - historical_avg) / historical_avg * 100",
                unit="percent",
                warning_threshold=15.0,    # 15% decline = warning
                critical_threshold=30.0,   # 30% decline = critical
                alert_direction="above",
                applicable_roles=["owner", "manager", "sales_rep"],
                calculation_frequency="daily",
            ),
            "days_sales_outstanding": KPIDefinition(
                name="Days Sales Outstanding (DSO)",
                description="Average number of days to collect payment after invoice",
                formula="(accounts_receivable / annual_revenue) * 365",
                unit="days",
                warning_threshold=45.0,
                critical_threshold=60.0,
                alert_direction="above",
                applicable_roles=["owner", "manager", "back_office"],
                calculation_frequency="weekly",
            ),
            "inventory_turns": KPIDefinition(
                name="Inventory Turns",
                description="Number of times inventory is sold and replaced per year",
                formula="annual_cogs / average_inventory_value",
                unit="turns/year",
                warning_threshold=6.0,     # below 6 = warning
                critical_threshold=4.0,    # below 4 = critical
                alert_direction="below",
                applicable_roles=["owner", "manager"],
                calculation_frequency="weekly",
            ),
            "lead_time_weeks": KPIDefinition(
                name="Supplier Lead Time",
                description="Weeks from purchase order to delivery. warning_threshold = typical/expected lead time used for reorder planning.",
                formula="delivery_date - po_date",
                unit="weeks",
                warning_threshold=6.0,   # Typical lead time — used by demand forecasting as planning default
                critical_threshold=10.0, # Unusually slow — will likely cause stock gap
                alert_direction="above",
                applicable_roles=["owner", "manager", "back_office"],
                calculation_frequency="per_order",
            ),
            "reorder_quantity_weeks": KPIDefinition(
                name="Reorder Quantity Target",
                description="How many weeks of supply to order at a time. critical_threshold = target weeks stock.",
                formula="order_qty / weekly_sell_rate",
                unit="weeks",
                warning_threshold=6.0,   # Minimum acceptable order size
                critical_threshold=8.0,  # Target weeks of stock per order (used by demand forecasting)
                alert_direction="below",
                applicable_roles=["owner", "manager", "back_office"],
                calculation_frequency="per_order",
            ),
        }

    # ── Component 4: Scoring Config ───────────────────────────────────────────

    def get_scoring_config(self) -> dict[str, ScoringConfig]:
        return {
            "account_health": ScoringConfig(
                score_name="account_health",
                components=[
                    {
                        "name": "recency",
                        "description": "Days since last order relative to average cycle",
                        "rules": [
                            {"condition": "days_since > severe_threshold", "penalty": 35},
                            {"condition": "days_since > overdue_threshold", "penalty": 15},
                            {"condition": "no_orders_ever", "penalty": 40},
                        ],
                    },
                    {
                        "name": "trend",
                        "description": "Order value trend over last 4 orders",
                        "rules": [
                            {"condition": "decline_pct > 30", "penalty": 20},
                            {"condition": "decline_pct > 15", "penalty": 10},
                        ],
                    },
                    {
                        "name": "balance",
                        "description": "Outstanding balance relative to threshold",
                        "rules": [
                            {"condition": "balance > critical_threshold", "penalty": 10},
                        ],
                    },
                ],
                status_boundaries={
                    "healthy": 75,
                    "slowing": 50,
                    "at_risk": 25,
                    "dormant": 0,
                },
            ),
            "inventory_health": ScoringConfig(
                score_name="inventory_health",
                components=[
                    {
                        "name": "weeks_of_supply",
                        "description": "Weeks of supply at current sell rate",
                        "rules": [
                            {"condition": "weeks == 0", "status": "out_of_stock"},
                            {"condition": "weeks <= critical_threshold", "status": "critical"},
                            {"condition": "weeks <= warning_threshold", "status": "low"},
                        ],
                    }
                ],
                status_boundaries={
                    "healthy": 4,    # weeks
                    "low": 2,
                    "critical": 0,
                },
            ),
        }

    # ── Component 5: Causal Trees ─────────────────────────────────────────────

    def get_causal_trees(self) -> dict[str, CausalTree]:
        return {
            "account_health_drop": CausalTree(
                trigger="account_health_drop",
                entity_type="account",
                root_node_id="check_recency",
                nodes={
                    "check_recency": CausalNode(
                        id="check_recency",
                        condition="Is the recency score the primary driver of the drop?",
                        condition_key="recency_score_drop",
                        condition_op="gt",
                        condition_value=10,
                        true_branch="check_seasonal",
                        false_branch="check_trend",
                        true_conclusion=None,
                        true_action=None,
                        false_conclusion=None,
                        false_action=None,
                    ),
                    "check_seasonal": CausalNode(
                        id="check_seasonal",
                        condition="Does the order gap match a known seasonal pattern?",
                        condition_key="matches_seasonal_pattern",
                        condition_op="eq",
                        condition_value=True,
                        true_branch=None,
                        true_conclusion="seasonal_gap",
                        true_action="Monitor — likely natural recovery. Check in 2 weeks.",
                        false_branch="check_competitive",
                        false_conclusion=None,
                        false_action=None,
                    ),
                    "check_competitive": CausalNode(
                        id="check_competitive",
                        condition="Has the account dropped SKUs or reduced breadth?",
                        condition_key="sku_breadth_declining",
                        condition_op="eq",
                        condition_value=True,
                        true_branch=None,
                        true_conclusion="competitive_displacement",
                        true_action="Schedule visit. Bring competitive pricing analysis and new product samples.",
                        false_branch=None,
                        false_conclusion="lapsed_contact",
                        false_action="Reach out to account. May have changed buyers or ownership.",
                    ),
                    "check_trend": CausalNode(
                        id="check_trend",
                        condition="Are order values shrinking but orders still regular?",
                        condition_key="order_value_trend",
                        condition_op="lt",
                        condition_value=-0.15,
                        true_branch="check_substitution",
                        false_branch=None,
                        true_conclusion=None,
                        true_action=None,
                        false_conclusion="payment_risk",
                        false_action="Review outstanding balance and payment history.",
                    ),
                    "check_substitution": CausalNode(
                        id="check_substitution",
                        condition="Is the account buying lower-margin products?",
                        condition_key="margin_trend",
                        condition_op="lt",
                        condition_value=-0.10,
                        true_branch=None,
                        true_conclusion="product_substitution",
                        true_action="Review product mix. Recommend higher-margin alternatives.",
                        false_branch=None,
                        false_conclusion="volume_decline",
                        false_action="Understand account's own sales trends. May be a market-level issue.",
                    ),
                },
            ),
            "inventory_critical": CausalTree(
                trigger="inventory_critical",
                entity_type="inventory_item",
                root_node_id="check_po_exists",
                nodes={
                    "check_po_exists": CausalNode(
                        id="check_po_exists",
                        condition="Is there an open PO for this item?",
                        condition_key="open_po_qty",
                        condition_op="gt",
                        condition_value=0,
                        true_branch="check_po_timing",
                        false_branch=None,
                        true_conclusion=None,
                        true_action=None,
                        false_conclusion="no_po_critical",
                        false_action="Draft purchase order immediately. Estimated stockout in {weeks_remaining} weeks.",
                    ),
                    "check_po_timing": CausalNode(
                        id="check_po_timing",
                        condition="Will the PO arrive before stockout?",
                        condition_key="po_arrives_before_stockout",
                        condition_op="eq",
                        condition_value=True,
                        true_branch=None,
                        true_conclusion="po_adequate",
                        true_action="PO covers the gap. Monitor delivery confirmation.",
                        false_branch=None,
                        false_conclusion="po_too_late",
                        false_action="PO arrives after stockout. Consider emergency order or substitute product.",
                    ),
                },
            ),
        }

    # ── Component 6: Workflow Definitions ────────────────────────────────────

    def get_workflow_definitions(self) -> dict[str, WorkflowDefinition]:
        return {
            "new_account_onboarding": WorkflowDefinition(
                name="New Account Onboarding",
                slug="new_account_onboarding",
                description="Step-by-step process for onboarding a new wholesale account",
                steps=[
                    WorkflowStep(
                        step_id="credit_check",
                        name="Credit Application Review",
                        description="Review and approve/deny credit application",
                        trigger="new_account_created",
                        action_type="generate_internal_report",
                        autonomy_tier=1,
                        next_step_on_success="welcome_email",
                        next_step_on_failure=None,
                        timeout_hours=48,
                    ),
                    WorkflowStep(
                        step_id="welcome_email",
                        name="Welcome Communication",
                        description="Draft and send welcome email with first order info",
                        trigger="credit_approved",
                        action_type="draft_customer_email",
                        autonomy_tier=2,
                        next_step_on_success="first_order_follow_up",
                        next_step_on_failure=None,
                        timeout_hours=24,
                    ),
                    WorkflowStep(
                        step_id="first_order_follow_up",
                        name="First Order Follow-Up",
                        description="48-hour check-in after first order is placed",
                        trigger="first_order_placed",
                        action_type="send_dormant_account_alert",
                        autonomy_tier=3,
                        next_step_on_success="30_day_check",
                        next_step_on_failure=None,
                        timeout_hours=72,
                    ),
                    WorkflowStep(
                        step_id="30_day_check",
                        name="30-Day Account Review",
                        description="Review account health at 30 days",
                        trigger="days_since_first_order >= 30",
                        action_type="calculate_health_score",
                        autonomy_tier=1,
                        next_step_on_success=None,
                        next_step_on_failure=None,
                        timeout_hours=None,
                    ),
                ],
            ),
            "ar_collection": WorkflowDefinition(
                name="AR Collection Sequence",
                slug="ar_collection",
                description="Progressive AR collection communications for overdue accounts",
                steps=[
                    WorkflowStep(
                        step_id="reminder_1",
                        name="Friendly Reminder (Day 1 past due)",
                        description="Automated first reminder on due date",
                        trigger="invoice_past_due_days >= 1",
                        action_type="draft_customer_email",
                        autonomy_tier=3,
                        next_step_on_success="reminder_2",
                        next_step_on_failure=None,
                        timeout_hours=None,
                    ),
                    WorkflowStep(
                        step_id="reminder_2",
                        name="Follow-Up (Day 7 past due)",
                        description="Second notice with urgency",
                        trigger="invoice_past_due_days >= 7",
                        action_type="draft_customer_email",
                        autonomy_tier=2,
                        next_step_on_success="escalate",
                        next_step_on_failure=None,
                        timeout_hours=None,
                    ),
                    WorkflowStep(
                        step_id="escalate",
                        name="Escalation (Day 15 past due)",
                        description="Escalate to owner/manager with hold recommendation",
                        trigger="invoice_past_due_days >= 15",
                        action_type="send_sync_break_alert",
                        autonomy_tier=2,
                        next_step_on_success=None,
                        next_step_on_failure=None,
                        timeout_hours=None,
                    ),
                ],
            ),
        }

    # ── Component 7: Pricing Config ───────────────────────────────────────────

    def get_pricing_config(self) -> PricingConfig:
        return PricingConfig(
            margin_method="gross_up",
            standard_tiers=[
                {"name": "A", "min_annual_revenue": 50000, "discount_pct": 5.0},
                {"name": "B", "min_annual_revenue": 20000, "discount_pct": 2.5},
                {"name": "C", "min_annual_revenue": 0,     "discount_pct": 0.0},
            ],
            landed_cost_components=[
                "product_cost",
                "ocean_freight",
                "customs_duties",
                "customs_broker_fee",
                "port_handling",
                "inland_freight",
                "insurance",
                "storage",
                "federal_excise_tax",
            ],
            excise_tax_applicable=True,
            price_elasticity_model="tiered",
        )

    # ── Component 8: Compliance Calendar ────────────────────────────────────

    def get_compliance_calendar(self) -> list[ComplianceItem]:
        return [
            ComplianceItem(
                name="TTB Federal Basic Permit",
                license_type="federal_import_permit",
                issuing_body="TTB (Alcohol and Tobacco Tax and Trade Bureau)",
                renewal_period_months=0,     # does not expire, but requires annual filing
                alert_days_before=[30],
                applicable_states=[],
                documentation_required=["Annual operations report", "Bond renewal"],
            ),
            ComplianceItem(
                name="State Liquor Importer License",
                license_type="state_import_license",
                issuing_body="State Liquor Authority",
                renewal_period_months=12,
                alert_days_before=[90, 60, 30, 7],
                applicable_states=[],  # Varies — configure per company
                documentation_required=["Renewal application", "Fee payment", "Bond"],
            ),
            ComplianceItem(
                name="FDA Food Facility Registration",
                license_type="fda_registration",
                issuing_body="FDA",
                renewal_period_months=24,
                alert_days_before=[60, 30],
                applicable_states=[],
                documentation_required=["Online renewal via FDA portal"],
            ),
            ComplianceItem(
                name="Certificate of Label Approval (COLA)",
                license_type="label_approval",
                issuing_body="TTB",
                renewal_period_months=0,   # Per-product, doesn't expire but changes require reapplication
                alert_days_before=[],
                applicable_states=[],
                documentation_required=["Label artwork", "Formula approval (if applicable)"],
            ),
        ]

    # ── Component 9: Terminology ─────────────────────────────────────────────

    def get_terminology(self) -> dict[str, str]:
        return {
            # Entity names
            "account":           "account",
            "supplier":          "supplier",
            "product":           "product",
            "order":             "order",
            "invoice":           "invoice",
            "purchase_order":    "purchase order",
            # Units
            "unit_of_measure":   "cases",
            "unit_singular":     "case",
            # Stock terms
            "sell_rate_label":   "depletion rate",
            "lead_time_label":   "container lead time",
            "reorder_label":     "reorder point",
            "critical_stock_label": "critically low",
            "low_stock_label":   "running low",
            # Business terms
            "dormant_threshold_label": "dormant (60+ days since last order)",
            "revenue_label":     "sales revenue",
            "margin_label":      "gross margin",
            "territory_label":   "territory",
            # Distribution-specific
            "depletion_rate":    "depletion rate",
            "3pl":               "3PL warehouse",
            "customs_clearance": "customs clearance",
            "landed_cost":       "landed cost",
            "excise_tax":        "federal excise tax (FET)",
            "compliance_body":   "TTB",
        }

    # ── Component 10: Benchmarks ─────────────────────────────────────────────

    def get_benchmarks(self) -> list[IndustryBenchmark]:
        return [
            IndustryBenchmark(
                metric_name="days_sales_outstanding",
                description="Average days to collect payment after invoicing",
                industry_average=32.0,
                top_quartile=21.0,
                unit="days",
                source="NBWA Distributor Financial Survey",
                impact_template=(
                    "Your DSO is {company_value} days vs industry average of 32. "
                    "Reducing to 28 days would free approximately ${impact_value} in working capital."
                ),
            ),
            IndustryBenchmark(
                metric_name="inventory_turns",
                description="Number of times total inventory is sold per year",
                industry_average=12.0,
                top_quartile=18.0,
                unit="turns/year",
                source="Beverage Wholesaler Benchmarking Study",
                impact_template=(
                    "Your inventory turns {company_value}x/year vs industry average of 12x. "
                    "Improving by 2 turns would reduce average inventory carry by ${impact_value}."
                ),
            ),
            IndustryBenchmark(
                metric_name="gross_margin_pct",
                description="Gross margin as a percentage of net sales",
                industry_average=24.0,
                top_quartile=28.0,
                unit="percent",
                source="NBWA Industry Survey",
                impact_template=(
                    "Your gross margin is {company_value}% vs industry average of 24%. "
                    "Each 1% margin improvement on your revenue base is worth ${impact_value}."
                ),
            ),
            IndustryBenchmark(
                metric_name="delivery_cost_pct",
                description="Delivery/logistics cost as a percentage of revenue",
                industry_average=5.5,
                top_quartile=3.8,
                unit="percent",
                source="Distributor Operations Survey",
                impact_template=(
                    "Your delivery cost is {company_value}% of revenue vs industry average of 5.5%. "
                    "Reducing to 4.5% would save approximately ${impact_value} annually."
                ),
            ),
            IndustryBenchmark(
                metric_name="active_skus_per_account",
                description="Average number of distinct SKUs ordered by an active account",
                industry_average=8.0,
                top_quartile=14.0,
                unit="SKUs",
                source="Distribution Sales Analytics Benchmark",
                impact_template=(
                    "Your accounts average {company_value} SKUs each vs top performers at 14. "
                    "Growing breadth by 2 SKUs per account represents ${impact_value} in annual revenue."
                ),
            ),
        ]

    # ── Override: richer system prompt context ───────────────────────────────

    def get_system_prompt_context(self) -> str:
        return """- Wholesale distribution and import/export operations (beverage, food, general goods)
- B2B sales to retailers, restaurants, bars, hotels, and other businesses
- Federal Excise Tax (FET), TTB compliance, customs clearance, and COLA label approvals
- Container sourcing, 3PL warehouse operations, distributor depletion tracking
- Purchase order management from overseas suppliers with 6–12 week lead times
- Account health monitoring: dormant accounts, order frequency, SKU breadth, payment behavior
- Landed cost calculation: product cost + freight + duties + broker fees + FET
- Fluent in industry terminology: depletion rate, pull-through, on-premise/off-premise"""

    def get_intent_categories(self) -> dict[str, str]:
        return {
            "inventory_check": "asking about case levels, supply, stock, or depletion rates",
            "account_status": "asking about a specific account, retailer, or customer",
            "sales_report": "requesting sales data, revenue trends, or period comparisons",
            "po_inquiry": "asking about purchase orders, containers, or supplier orders",
            "customs_status": "asking about shipments, container status, or customs clearance",
            "alert_check": "asking about alerts, warnings, or issues flagged by the system",
            "action_request": "asking the AI to DO something (draft email, create PO, generate report)",
            "compliance_check": "asking about licenses, TTB, permits, or regulatory status",
            "general_question": "general business questions, advice, or anything else",
        }

"""Exact-cent shared logistics and warehousing allocation, no accounting writes.

A scenario is an estimate, even when invoice references are supplied. Source
reconciliation and human approval happen outside this pure calculation.
Costs are assigned ONCE from invoice line to SKU and conserved to the cent.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Literal

from .waterfall import INBOUND_COMPONENTS, REQUIRED_COMPONENTS, WAREHOUSE_COMPONENTS


CENT = Decimal("0.01")
UNIT = Decimal("0.000001")
ZERO = Decimal("0")
WEIGHTS = ("cases", "weight_kg", "volume_cbm", "pallet_days", "picks")
ExpenseBasis = Literal["cases", "weight_kg", "volume_cbm", "pallet_days", "picks"]


def positive_or_zero(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative exact Decimal")
    if value > Decimal("100000000000"):
        raise ValueError(f"{name} exceeds supported amount")


@dataclass(frozen=True)
class ProductLine:
    sku: str
    accepted_cases: Decimal
    product_cost_per_case: Decimal
    weight_kg: Decimal = ZERO
    volume_cbm: Decimal = ZERO
    pallet_days: Decimal = ZERO
    picks: Decimal = ZERO
    discount_per_case: Decimal = ZERO
    returns_allowance_per_case: Decimal = ZERO
    commission_per_case: Decimal = ZERO
    marketing_per_case: Decimal = ZERO


@dataclass(frozen=True)
class ExpenseLine:
    expense_line_ref: str
    component: str
    amount: Decimal
    currency: str
    allocation_basis: ExpenseBasis
    evidence_ref: str
    evidence_version: str


@dataclass(frozen=True)
class AllocationScenario:
    company_id: str
    shipment_ref: str
    currency: str
    target_contribution_margin_pct: Decimal
    products: tuple[ProductLine, ...]
    expenses: tuple[ExpenseLine, ...]


def _money(amount: Decimal) -> Decimal:
    return amount.quantize(CENT)


def _split_exact_cents(amount: Decimal, weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """Largest remainder with deterministic SKU tie-break; no missing cents."""
    denom = sum(weights.values(), ZERO)
    if denom <= 0:
        raise ValueError("Every charge needs a positive allocation denominator")
    raw = {sku: amount * weight / denom for sku, weight in weights.items()}
    rounded_down = {
        sku: (value / CENT).to_integral_value(rounding=ROUND_DOWN) * CENT
        for sku, value in raw.items()
    }
    pennies = int((amount - sum(rounded_down.values(), ZERO)) / CENT)
    rank = sorted(weights, key=lambda sku: (-(raw[sku] - rounded_down[sku]), sku))
    for sku in rank[:pennies]:
        rounded_down[sku] += CENT
    if sum(rounded_down.values(), ZERO) != amount:
        raise ValueError("Allocation did not conserve the invoice amount")
    return rounded_down


def allocate_shipment(scenario: AllocationScenario) -> dict:
    if not scenario.company_id or not scenario.shipment_ref:
        raise ValueError("Tenant and shipment identity are mandatory")
    if not isinstance(scenario.currency, str) or len(scenario.currency) != 3 or not scenario.currency.isascii() or not scenario.currency.isalpha() or not scenario.currency.isupper():
        raise ValueError("ISO currency code is mandatory")
    positive_or_zero(scenario.target_contribution_margin_pct, "target contribution margin")
    if scenario.target_contribution_margin_pct >= 100:
        raise ValueError("Contribution margin target must be below 100%")
    if not 1 <= len(scenario.products) <= 100 or len(scenario.expenses) > 200:
        raise ValueError("Unsupported number of SKU or expense lines")

    items: dict[str, ProductLine] = {}
    for p in scenario.products:
        if not isinstance(p.sku, str) or not p.sku.strip() or len(p.sku) > 120 or p.sku in items:
            raise ValueError("SKU identities must be distinct and nonempty")
        if not isinstance(p.accepted_cases, Decimal) or p.accepted_cases <= 0:
            raise ValueError("Only saleable accepted cases may be priced")
        if p.accepted_cases != p.accepted_cases.quantize(Decimal("0.001")):
            raise ValueError("Accepted cases must not exceed 3 decimals")
        for field in (
            "accepted_cases", "product_cost_per_case", "weight_kg", "volume_cbm",
            "pallet_days", "picks", "discount_per_case",
            "returns_allowance_per_case", "commission_per_case", "marketing_per_case",
        ):
            positive_or_zero(getattr(p, field), field)
        items[p.sku] = p

    assigned: dict[str, dict[str, Decimal]] = {sku: {} for sku in items}
    allocation_details: list[dict] = []
    seen_charges: set[str] = set()
    present = {"product"}
    total_expenses = ZERO
    for charge in scenario.expenses:
        if (not charge.expense_line_ref or charge.expense_line_ref in seen_charges
            or len(charge.expense_line_ref) > 160):
            raise ValueError("Expense-line identifiers must be unique")
        seen_charges.add(charge.expense_line_ref)
        if charge.component not in (INBOUND_COMPONENTS | WAREHOUSE_COMPONENTS) - {"product"}:
            raise ValueError("Unknown shared cost component")
        if charge.currency != scenario.currency:
            raise ValueError("Mixed-currency expenses require a sourced FX conversion first")
        if not charge.evidence_ref.strip() or not charge.evidence_version.strip():
            raise ValueError("Expense source and revision must be explicitly recorded")
        if charge.allocation_basis not in WEIGHTS:
            raise ValueError("Unsupported allocation basis")
        if charge.component == "warehouse_storage" and charge.allocation_basis != "pallet_days":
            raise ValueError("Warehouse storage must be allocated by documented pallet-days")
        positive_or_zero(charge.amount, "invoice line amount")
        if _money(charge.amount) != charge.amount:
            raise ValueError("Invoice line amount must be exact to cents")
        weights = {sku: p.accepted_cases if charge.allocation_basis == "cases"
                   else getattr(p, charge.allocation_basis) for sku, p in items.items()}
        distribution = _split_exact_cents(charge.amount, weights)
        for sku, value in distribution.items():
            per_component = assigned[sku]
            per_component[charge.component] = per_component.get(charge.component, ZERO) + value
        allocation_details.append({
            "expense_line_ref": charge.expense_line_ref,
            "component": charge.component,
            "source": charge.evidence_ref,
            "revision": charge.evidence_version,
            "basis": charge.allocation_basis,
            "invoice_amount": str(charge.amount),
            "allocated": {sku: str(amount) for sku, amount in distribution.items()},
            "rounding_residual": "0.00",
        })
        total_expenses += charge.amount
        present.add(charge.component)

    results: list[dict] = []
    for sku, item in sorted(items.items()):
        allocated = assigned[sku]
        inbound_total = item.product_cost_per_case * item.accepted_cases + sum(
            (amount for comp, amount in allocated.items() if comp in INBOUND_COMPONENTS), ZERO
        )
        warehouse_total = sum(
            (amount for comp, amount in allocated.items() if comp in WAREHOUSE_COMPONENTS), ZERO
        )
        fully_loaded = inbound_total + warehouse_total
        loaded_per_case = fully_loaded / item.accepted_cases
        landed_per_case = inbound_total / item.accepted_cases
        warehouse_per_case = warehouse_total / item.accepted_cases
        operating_per_case = item.commission_per_case + item.marketing_per_case
        minimum_net_price = (loaded_per_case + operating_per_case) / (
            Decimal("1") - scenario.target_contribution_margin_pct / Decimal("100")
        )
        minimum_list = minimum_net_price + item.discount_per_case + item.returns_allowance_per_case
        results.append({
            "sku": sku,
            "accepted_cases": str(item.accepted_cases),
            "inbound_total": str(inbound_total.quantize(UNIT)),
            "warehouse_total": str(warehouse_total.quantize(UNIT)),
            "fully_loaded_total": str(fully_loaded.quantize(UNIT)),
            "inbound_cost_per_case": str(landed_per_case.quantize(UNIT)),
            "warehouse_cost_per_case": str(warehouse_per_case.quantize(UNIT)),
            "fully_loaded_cost_per_case": str(loaded_per_case.quantize(UNIT)),
            "minimum_list_price_at_target_contribution": str(minimum_list.quantize(CENT, rounding=ROUND_UP)),
            "allocated_expenses": {key: str(value) for key, value in sorted(allocated.items())},
            "source_status": "user_supplied_unreconciled",
        })

    assert all(
        sum((Decimal(line["allocated"][sku]) for sku in items), ZERO) == Decimal(line["invoice_amount"])
        for line in allocation_details
    )
    return {
        "company_id": scenario.company_id, "shipment_ref": scenario.shipment_ref,
        "currency": scenario.currency,
        "target_contribution_margin_pct": str(scenario.target_contribution_margin_pct),
        "products": results, "charges": allocation_details,
        "expenses_total": str(total_expenses),
        "missing_inbound_components": sorted(REQUIRED_COMPONENTS - present),
        "missing_warehouse_components": sorted(WAREHOUSE_COMPONENTS - present),
        "costs_reconciled": False, "approved_price": False,
        "prices_updated": False, "accounting_entries_posted": False,
    }

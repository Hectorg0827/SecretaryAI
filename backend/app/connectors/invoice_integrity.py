"""Pure read-side invoice validation for QuickBooks source projections.

These helpers are not persistence contracts and do not authorize vendor writes.
Provider-specific line formats are interpreted by the owning adapters.
"""

from decimal import Decimal, InvalidOperation


def decimal_field(value, field: str, *, signed: bool = False) -> Decimal:
    """Keep observed decimal precision; never impute an absent value as zero."""
    if value is None or value == "" or isinstance(value, bool):
        raise ValueError(f"{field} is missing or invalid")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} is missing or invalid") from exc
    if not amount.is_finite() or (not signed and amount < 0):
        raise ValueError(f"{field} is missing or invalid")
    return amount


def optional_decimal_field(value, field: str) -> Decimal | None:
    """An unobserved rate stays unknown; a supplied but bad rate is rejected."""
    if value is None or value == "":
        return None
    return decimal_field(value, field, signed=True)


def invoice_balance(total: Decimal, balance: Decimal, label: str) -> None:
    if balance > total:
        raise ValueError(f"{label} balance exceeds invoice total")


def require_invoice_lines(raw_lines, total: Decimal, label: str) -> list[dict]:
    if not isinstance(raw_lines, list):
        raise ValueError(f"{label} invoice line items are missing or invalid")
    if total != 0 and not raw_lines:
        raise ValueError(f"{label} positive invoice has no observed line items")
    if any(not isinstance(line, dict) for line in raw_lines):
        raise ValueError(f"{label} invoice line item is not a mapping")
    return raw_lines


def unique_line_id(line: dict, seen: set[str], label: str, *, key: str):
    """Check provider line identifiers when present; don't invent IDs."""
    value = line.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} invoice line has invalid id")
    if value in seen:
        raise ValueError(f"{label} invoice contains duplicate line id")
    seen.add(value)
    return value

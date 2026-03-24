"""
Industry Logic Registry — the "Lego set" architecture.

Swap the intelligence module based on the company's industry vertical.
Each vertical is a self-contained module that implements the same interface.

Currently registered verticals:
  wholesale_distribution  — Importer / Distributor (§18–§25)

Planned verticals (register when built):
  retail                  — Retail store chains
  manufacturing           — Product manufacturers
  logistics               — 3PL / freight brokers
  professional_services   — Service businesses

Usage:
    from app.intelligence.industry_registry import get_industry_module

    module = get_industry_module(
        vertical="wholesale_distribution",
        company_id="co-123",
        config={"annual_revenue": 15_000_000, "autonomy_tier": 2},
    )

    # Module exposes all capabilities for that vertical
    actions = module.reorder_actions(inventory_snapshot)
    analysis = module.customer_drop_analysis(...)
    info = module.describe()
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# ─── Registry ────────────────────────────────────────────────────────────────

# Map of vertical_id → fully-qualified module class path (lazy import)
_REGISTRY: dict[str, str] = {
    "wholesale_distribution": (
        "app.intelligence.wholesale_distribution.module.WholesaleDistributionModule"
    ),
    # Future verticals — register here when built:
    # "retail": "app.intelligence.retail.module.RetailModule",
    # "manufacturing": "app.intelligence.manufacturing.module.ManufacturingModule",
}

_DEFAULT_VERTICAL = "wholesale_distribution"


def get_industry_module(
    vertical: str | None,
    company_id: str,
    config: dict[str, Any] | None = None,
) -> Any:
    """
    Instantiate and return the industry module for the given vertical.
    Falls back to wholesale_distribution if vertical is None or unknown.

    Args:
        vertical: vertical ID string (e.g. "wholesale_distribution")
        company_id: company UUID
        config: vertical-specific configuration dict

    Returns:
        Instance of the appropriate industry module.
    """
    resolved = vertical or _DEFAULT_VERTICAL

    if resolved not in _REGISTRY:
        log.warning(
            "Unknown industry vertical '%s' — falling back to '%s'",
            resolved,
            _DEFAULT_VERTICAL,
        )
        resolved = _DEFAULT_VERTICAL

    class_path = _REGISTRY[resolved]
    module_path, class_name = class_path.rsplit(".", 1)

    try:
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        instance = cls(company_id=company_id, config=config or {})
        log.info(
            "Loaded industry module '%s' (%s) for company %s",
            resolved, class_name, company_id,
        )
        return instance
    except Exception as exc:
        log.error("Failed to load industry module '%s': %s", resolved, exc)
        raise RuntimeError(
            f"Could not load industry module for vertical '{resolved}': {exc}"
        ) from exc


def register_vertical(vertical_id: str, class_path: str) -> None:
    """
    Register a new industry vertical at runtime.
    Useful for plugins and tests.

    Args:
        vertical_id: unique identifier string
        class_path: fully qualified class path, e.g. "myapp.modules.Foo"
    """
    if vertical_id in _REGISTRY:
        log.warning("Overriding existing vertical '%s'", vertical_id)
    _REGISTRY[vertical_id] = class_path
    log.info("Registered industry vertical '%s' → %s", vertical_id, class_path)


def list_verticals() -> list[dict[str, str]]:
    """Return all registered verticals with their class paths."""
    return [
        {"vertical_id": vid, "class_path": cpath}
        for vid, cpath in _REGISTRY.items()
    ]

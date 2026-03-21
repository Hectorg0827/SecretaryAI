"""
IndustryModuleLoader — loads and caches the active industry module.

Usage:
    module = get_module("wholesale_distribution")
    # or, from company config:
    module = get_module_for_company(company_config)

Adding a new industry:
    1. Create app/industry_modules/<slug>/__init__.py with a class that subclasses IndustryModule
    2. Register it in _REGISTRY below
    3. Done — no other core code changes needed
"""
from __future__ import annotations

import importlib
from functools import lru_cache
from typing import Optional

from app.industry_modules.base import IndustryModule


# ─── Registry ─────────────────────────────────────────────────────────────────
# Maps slug → fully-qualified class path
# Add new industries here.

_REGISTRY: dict[str, str] = {
    "wholesale_distribution": "app.industry_modules.wholesale_distribution.WholesaleDistributionModule",
    # Future modules — add new industries here:
    # "food_import":        "app.industry_modules.food_import.FoodImportModule",
    # "auto_parts":         "app.industry_modules.auto_parts.AutoPartsModule",
    # "building_materials": "app.industry_modules.building_materials.BuildingMaterialsModule",
}

DEFAULT_MODULE = "wholesale_distribution"


# ─── Loader ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=32)
def get_module(slug: str) -> IndustryModule:
    """
    Load and return a cached IndustryModule instance by slug.
    Raises ValueError if the slug is not registered.
    Raises ImportError if the module class cannot be loaded.
    """
    class_path = _REGISTRY.get(slug)
    if not class_path:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(
            f"Unknown industry module '{slug}'. Available: {available}"
        )

    module_path, class_name = class_path.rsplit(".", 1)
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls()


def get_module_for_company(company_config: dict) -> IndustryModule:
    """
    Load the industry module configured for a specific company.
    Falls back to the default module if none is set.
    """
    slug = company_config.get("industry_module") or DEFAULT_MODULE
    return get_module(slug)


def list_available_modules() -> list[dict]:
    """Return metadata for all registered industry modules."""
    modules = []
    for slug in _REGISTRY:
        try:
            mod = get_module(slug)
            profile = mod.get_profile()
            modules.append({
                "slug": slug,
                "name": profile.name,
                "description": profile.description,
                "business_model": profile.business_model,
            })
        except Exception as exc:
            modules.append({"slug": slug, "error": str(exc)})
    return modules


def clear_cache() -> None:
    """Clear the module cache (useful in tests)."""
    get_module.cache_clear()

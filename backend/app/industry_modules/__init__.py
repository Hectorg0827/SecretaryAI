"""
Industry Modules — swappable intelligence packages.

Each module encodes the operational logic, terminology, KPIs, scoring formulas,
causal trees, and compliance requirements for a specific industry.

The core platform never imports a specific module directly.
It always goes through the loader:

    from app.industry_modules.loader import get_module_for_company
    module = get_module_for_company(company_config)
    kpis = module.get_kpi_definitions()
    terms = module.get_terminology()

To add a new industry: create a new subdirectory, implement IndustryModule,
and register it in loader._REGISTRY. That's it.
"""
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
from app.industry_modules.loader import get_module, get_module_for_company, list_available_modules

__all__ = [
    "IndustryModule",
    "IndustryProfile",
    "KPIDefinition",
    "ScoringConfig",
    "CausalTree",
    "CausalNode",
    "WorkflowDefinition",
    "WorkflowStep",
    "PricingConfig",
    "ComplianceItem",
    "IndustryBenchmark",
    "get_module",
    "get_module_for_company",
    "list_available_modules",
]

"""
Wholesale Distribution Industry Logic Layer.

This is the interchangeable "Lego piece" for the importer/distributor vertical.
It can be swapped out for other industry verticals (retail, manufacturing, etc.)
by registering a different vertical in the industry registry.

Modules:
    action_schema     — Action Object + Progressive Autonomy engine (§20)
    causal_reasoning  — Root cause attribution + counterfactual analysis (§18)
    stochastic_inv    — Probabilistic inventory intelligence (§19)
    scor_toc          — SCOR model + Theory of Constraints (§22)
    dynamic_pricing   — Price elasticity + competitive response (§23)
    esg_tco           — ESG metrics + Total Cost of Ownership (§24)
    returns_mgmt      — Returns intelligence + reverse logistics (§25)
    governance        — Data classification + authority matrix + anomaly detection (§21)

Usage:
    from app.intelligence.wholesale_distribution import WholesaleDistributionModule
    module = WholesaleDistributionModule(company_id="...", config={...})
"""

from .module import WholesaleDistributionModule

__all__ = ["WholesaleDistributionModule"]

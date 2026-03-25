"""
SecretaryAI Compliance Engine

Tracks federal and 50-state alcohol beverage compliance for importers/wholesalers.
Four interconnected layers:
  1. EntityRegistry  — licenses, products, brand registrations, COLAs, distributors
  2. RulesEngine     — 50-state matrix, pre-shipment checks, fee calculator
  3. AlertSystem     — expiration warnings, deadline tracker, daily digest
  4. ComplianceModule — top-level orchestrator

Usage:
    from app.intelligence.compliance import ComplianceModule, ComplianceConfig
    module = ComplianceModule(company_id="acme-123", config=ComplianceConfig())
"""

from .compliance_module import ComplianceModule, ComplianceConfig

__all__ = ["ComplianceModule", "ComplianceConfig"]

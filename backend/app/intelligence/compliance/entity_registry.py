"""
EntityRegistry — In-memory store for all compliance entities.

Holds:
  - Federal permits (TTB, FDA, CBP)
  - COLA records (per product)
  - State licenses (per state × product type)
  - Brand registrations (per product × state)
  - Products (SKU catalog)
  - Distributor relationships (per state)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from .models import (
    AlcoholProduct,
    BrandRegistration,
    BrandRegStatus,
    COLARecord,
    DistributorRelationship,
    FederalPermit,
    FederalPermitType,
    LicenseStatus,
    ProductType,
    StateLicense,
)


class EntityRegistry:
    """
    Single source of truth for all compliance entities for one company.
    The API layer is responsible for loading/saving records to the DB.
    """

    def __init__(self, company_id: str) -> None:
        self.company_id = company_id
        self._federal_permits: dict[str, FederalPermit] = {}        # id → permit
        self._cola_records: dict[str, COLARecord] = {}              # id → COLA
        self._state_licenses: dict[str, StateLicense] = {}          # id → license
        self._brand_registrations: dict[str, BrandRegistration] = {} # id → reg
        self._products: dict[str, AlcoholProduct] = {}              # id → product
        self._distributors: dict[str, DistributorRelationship] = {} # id → dist

    # ── Federal permits ───────────────────────────────────────────────────────

    def upsert_federal_permit(self, permit: FederalPermit) -> FederalPermit:
        self._federal_permits[permit.id] = permit
        return permit

    def get_federal_permit(self, permit_type: FederalPermitType) -> Optional[FederalPermit]:
        for p in self._federal_permits.values():
            if p.permit_type == permit_type and p.status == LicenseStatus.ACTIVE:
                return p
        return None

    def list_federal_permits(self) -> list[FederalPermit]:
        return list(self._federal_permits.values())

    # ── COLA records ──────────────────────────────────────────────────────────

    def upsert_cola(self, cola: COLARecord) -> COLARecord:
        self._cola_records[cola.id] = cola
        return cola

    def get_cola(self, product_id: str) -> Optional[COLARecord]:
        """Return the active (or most recent) COLA for a product."""
        matches = [c for c in self._cola_records.values() if c.product_id == product_id]
        if not matches:
            return None
        # prefer active over expired
        active = [c for c in matches if c.status == LicenseStatus.ACTIVE and not c.is_expired]
        return active[0] if active else matches[-1]

    def list_colas(self) -> list[COLARecord]:
        return list(self._cola_records.values())

    # ── Products ──────────────────────────────────────────────────────────────

    def upsert_product(self, product: AlcoholProduct) -> AlcoholProduct:
        self._products[product.id] = product
        return product

    def get_product(self, product_id: str) -> Optional[AlcoholProduct]:
        return self._products.get(product_id)

    def get_product_by_sku(self, sku: str) -> Optional[AlcoholProduct]:
        for p in self._products.values():
            if p.sku == sku:
                return p
        return None

    def list_products(self) -> list[AlcoholProduct]:
        return list(self._products.values())

    def products_by_type(self, product_type: ProductType) -> list[AlcoholProduct]:
        return [p for p in self._products.values() if p.product_type == product_type]

    # ── State licenses ────────────────────────────────────────────────────────

    def upsert_state_license(self, license: StateLicense) -> StateLicense:
        self._state_licenses[license.id] = license
        return license

    def get_state_license(
        self,
        state_code: str,
        product_type: Optional[ProductType] = None,
    ) -> Optional[StateLicense]:
        """
        Return the active license for a state (optionally filtered by product type).
        Returns the broadest matching license first.
        """
        matches = [
            lic for lic in self._state_licenses.values()
            if lic.state_code == state_code
            and lic.status == LicenseStatus.ACTIVE
            and not lic.is_expired
        ]
        if product_type is not None:
            typed = [lic for lic in matches if product_type in lic.product_types]
            return typed[0] if typed else None
        return matches[0] if matches else None

    def list_state_licenses(self) -> list[StateLicense]:
        return list(self._state_licenses.values())

    def states_with_active_license(self) -> list[str]:
        return list({
            lic.state_code for lic in self._state_licenses.values()
            if lic.status == LicenseStatus.ACTIVE and not lic.is_expired
        })

    # ── Brand registrations ───────────────────────────────────────────────────

    def upsert_brand_registration(self, reg: BrandRegistration) -> BrandRegistration:
        self._brand_registrations[reg.id] = reg
        return reg

    def get_brand_registration(
        self, product_id: str, state_code: str
    ) -> Optional[BrandRegistration]:
        for reg in self._brand_registrations.values():
            if (
                reg.product_id == product_id
                and reg.state_code == state_code
                and reg.status == BrandRegStatus.ACTIVE
                and not reg.is_expired
            ):
                return reg
        return None

    def list_brand_registrations(self) -> list[BrandRegistration]:
        return list(self._brand_registrations.values())

    def registrations_for_state(self, state_code: str) -> list[BrandRegistration]:
        return [r for r in self._brand_registrations.values() if r.state_code == state_code]

    # ── Distributors ──────────────────────────────────────────────────────────

    def upsert_distributor(self, dist: DistributorRelationship) -> DistributorRelationship:
        self._distributors[dist.id] = dist
        return dist

    def get_distributor(
        self,
        state_code: str,
        product_type: Optional[ProductType] = None,
    ) -> Optional[DistributorRelationship]:
        matches = [
            d for d in self._distributors.values()
            if d.state_code == state_code
        ]
        if product_type is not None:
            typed = [d for d in matches if product_type in d.product_types]
            return typed[0] if typed else None
        return matches[0] if matches else None

    def list_distributors(self) -> list[DistributorRelationship]:
        return list(self._distributors.values())

    # ── Cross-entity helpers ──────────────────────────────────────────────────

    def all_expiring_within(self, days: int) -> dict[str, list]:
        """
        Return all items expiring within `days` days, keyed by entity type.
        """
        cutoff = date.today()
        result: dict[str, list] = {
            "federal_permits": [],
            "cola_records": [],
            "state_licenses": [],
            "brand_registrations": [],
            "distributor_contracts": [],
        }
        for p in self._federal_permits.values():
            if p.days_until_expiry is not None and 0 <= p.days_until_expiry <= days:
                result["federal_permits"].append(p)
        for c in self._cola_records.values():
            if c.days_until_expiry is not None and 0 <= c.days_until_expiry <= days:
                result["cola_records"].append(c)
        for lic in self._state_licenses.values():
            if lic.days_until_expiry is not None and 0 <= lic.days_until_expiry <= days:
                result["state_licenses"].append(lic)
        for reg in self._brand_registrations.values():
            if reg.days_until_expiry is not None and 0 <= reg.days_until_expiry <= days:
                result["brand_registrations"].append(reg)
        for dist in self._distributors.values():
            if dist.days_until_contract_end is not None and 0 <= dist.days_until_contract_end <= days:
                result["distributor_contracts"].append(dist)
        return result

    def summary(self) -> dict:
        return {
            "company_id": self.company_id,
            "federal_permits": len(self._federal_permits),
            "cola_records": len(self._cola_records),
            "products": len(self._products),
            "state_licenses": len(self._state_licenses),
            "brand_registrations": len(self._brand_registrations),
            "distributors": len(self._distributors),
            "active_states": self.states_with_active_license(),
        }

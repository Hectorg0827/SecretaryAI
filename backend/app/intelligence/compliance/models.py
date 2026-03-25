"""
Shared data models for the SecretaryAI Compliance Engine.

Plain dataclasses (no ORM). The API layer handles DB persistence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional


# ─── Enums ────────────────────────────────────────────────────────────────────


class ProductType(str, Enum):
    WINE = "wine"
    SPIRITS = "spirits"
    BEER = "beer"
    MALT = "malt"


class LicenseStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    PENDING = "pending"
    REVOKED = "revoked"
    SUSPENDED = "suspended"


class BrandRegStatus(str, Enum):
    ACTIVE = "active"
    PENDING = "pending"
    EXPIRED = "expired"
    NOT_REQUIRED = "not_required"


class IssueSeverity(str, Enum):
    BLOCKER = "blocker"    # cannot ship
    WARNING = "warning"    # can ship but action needed soon
    INFO = "info"          # informational only


class AlertPriority(str, Enum):
    CRITICAL = "critical"  # 14 days or less
    WARNING = "warning"    # 15–60 days
    INFO = "info"          # informational


class DeadlineType(str, Enum):
    TAX_REPORT = "tax_report"
    PRICE_POSTING = "price_posting"
    LICENSE_RENEWAL = "license_renewal"
    BRAND_REGISTRATION_RENEWAL = "brand_reg_renewal"
    COLA_RENEWAL = "cola_renewal"
    DISTRIBUTOR_RENEWAL = "distributor_renewal"


class FederalPermitType(str, Enum):
    IMPORTER = "importer"
    WHOLESALER = "wholesaler"
    IMPORTER_WHOLESALER = "importer_wholesaler"
    PRODUCER = "producer"


class TerminationRestriction(str, Enum):
    NONE = "none"
    NOTICE_ONLY = "notice_only"
    GOOD_CAUSE = "good_cause"
    PROHIBITED = "prohibited"


# ─── Products & Federal ───────────────────────────────────────────────────────


@dataclass
class AlcoholProduct:
    """A single SKU/product the company imports/sells."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sku: str = ""
    name: str = ""
    product_type: ProductType = ProductType.WINE
    abv_pct: float = 0.0
    container_size_ml: float = 750.0   # e.g. 750, 1000, 1500
    cases_per_container: int = 56      # standard 20-ft container fill
    unit_cost_fob: float = 0.0         # USD per 9L case
    country_of_origin: str = ""
    foreign_producer_id: Optional[str] = None  # for CBMA tracking
    requires_formula_approval: bool = False

    @property
    def gallons_per_case(self) -> float:
        """Convert 12×bottle_ml to US gallons."""
        bottles_per_case = 12
        ml_per_gallon = 3785.41
        return (bottles_per_case * self.container_size_ml) / ml_per_gallon


@dataclass
class FederalPermit:
    """TTB basic importer/wholesaler permit or FDA facility registration."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    permit_type: FederalPermitType = FederalPermitType.IMPORTER
    permit_number: str = ""
    issue_date: Optional[date] = None
    expiration_date: Optional[date] = None   # None = indefinite (TTB basic permit)
    status: LicenseStatus = LicenseStatus.ACTIVE
    notes: str = ""

    @property
    def is_expired(self) -> bool:
        if self.expiration_date is None:
            return False
        return self.expiration_date < date.today()

    @property
    def days_until_expiry(self) -> Optional[int]:
        if self.expiration_date is None:
            return None
        return (self.expiration_date - date.today()).days


@dataclass
class COLARecord:
    """TTB Certificate of Label Approval for a specific product."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    product_id: str = ""
    cola_number: str = ""
    product_type: ProductType = ProductType.WINE
    issue_date: Optional[date] = None
    expiration_date: Optional[date] = None
    status: LicenseStatus = LicenseStatus.ACTIVE
    formula_approved: bool = False       # formula approval on file
    lab_analysis_on_file: bool = False   # lab sample analysis

    @property
    def is_expired(self) -> bool:
        if self.expiration_date is None:
            return False
        return self.expiration_date < date.today()

    @property
    def days_until_expiry(self) -> Optional[int]:
        if self.expiration_date is None:
            return None
        return (self.expiration_date - date.today()).days


# ─── State-level entities ─────────────────────────────────────────────────────


@dataclass
class StateLicense:
    """State supplier/importer/nonresident seller permit."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state_code: str = ""                 # "NY", "CA", etc.
    license_type: str = ""               # "Supplier", "Importer", "NRS", etc.
    product_types: list[ProductType] = field(default_factory=list)  # which types covered
    license_number: str = ""
    issue_date: Optional[date] = None
    expiration_date: Optional[date] = None
    renewal_window_days: int = 90        # start renewal this many days before expiry
    annual_fee: float = 0.0
    status: LicenseStatus = LicenseStatus.ACTIVE
    notes: str = ""

    @property
    def is_expired(self) -> bool:
        if self.expiration_date is None:
            return False
        return self.expiration_date < date.today()

    @property
    def days_until_expiry(self) -> Optional[int]:
        if self.expiration_date is None:
            return None
        return (self.expiration_date - date.today()).days


@dataclass
class BrandRegistration:
    """Per-product, per-state brand registration (required in many states)."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    product_id: str = ""
    state_code: str = ""
    registration_number: str = ""
    registration_date: Optional[date] = None
    expiration_date: Optional[date] = None
    registration_fee: float = 0.0
    status: BrandRegStatus = BrandRegStatus.ACTIVE
    state_label_approval_number: str = ""   # if state requires beyond COLA

    @property
    def is_expired(self) -> bool:
        if self.expiration_date is None:
            return False
        return self.expiration_date < date.today()

    @property
    def days_until_expiry(self) -> Optional[int]:
        if self.expiration_date is None:
            return None
        return (self.expiration_date - date.today()).days


@dataclass
class DistributorRelationship:
    """A distributor appointment for a given state."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state_code: str = ""
    distributor_name: str = ""
    territory: str = ""                    # e.g. "Statewide" or county list
    product_types: list[ProductType] = field(default_factory=list)
    contract_start_date: Optional[date] = None
    contract_end_date: Optional[date] = None   # None = evergreen
    franchise_law_attached: bool = False       # calculated: first_sale made?
    franchise_attachment_date: Optional[date] = None
    termination_restriction: TerminationRestriction = TerminationRestriction.NONE
    contract_document_ref: str = ""
    notes: str = ""

    @property
    def days_until_contract_end(self) -> Optional[int]:
        if self.contract_end_date is None:
            return None
        return (self.contract_end_date - date.today()).days


# ─── Compliance check outputs ─────────────────────────────────────────────────


@dataclass
class ComplianceIssue:
    """A single issue found during a compliance check."""
    severity: IssueSeverity = IssueSeverity.BLOCKER
    issue: str = ""
    action: str = ""
    fee: float = 0.0
    state_code: str = ""
    category: str = ""  # "license", "brand_registration", "cola", "franchise", etc.


@dataclass
class ComplianceCheckResult:
    """Result of a pre-shipment compliance check."""
    approved: bool = False
    product_id: str = ""
    state_code: str = ""
    quantity_cases: int = 0
    issues: list[ComplianceIssue] = field(default_factory=list)
    fees: dict[str, float] = field(default_factory=dict)
    total_compliance_cost: float = 0.0
    checked_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def blockers(self) -> list[ComplianceIssue]:
        return [i for i in self.issues if i.severity == IssueSeverity.BLOCKER]

    @property
    def warnings(self) -> list[ComplianceIssue]:
        return [i for i in self.issues if i.severity == IssueSeverity.WARNING]


@dataclass
class ComplianceAlert:
    """An expiration or deadline alert."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    alert_type: str = ""        # "license_expiry", "cola_expiry", etc.
    priority: AlertPriority = AlertPriority.WARNING
    state_code: str = ""        # "" for federal
    item_name: str = ""         # e.g. "NY Supplier License"
    item_id: str = ""
    days_until: int = 0
    expiry_date: Optional[date] = None
    message: str = ""
    action_required: str = ""
    renewal_url: str = ""
    estimated_fee: float = 0.0
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ComplianceDeadline:
    """An upcoming reporting or filing deadline."""
    deadline_type: DeadlineType = DeadlineType.TAX_REPORT
    state_code: str = ""
    due_date: date = field(default_factory=date.today)
    frequency: str = ""   # "monthly", "quarterly", "annual"
    action: str = ""
    notes: str = ""


@dataclass
class ComplianceCostEstimate:
    """Annual compliance cost breakdown."""
    licenses: float = 0.0
    brand_registrations: float = 0.0
    label_approvals: float = 0.0
    estimated_excise: float = 0.0
    grand_total: float = 0.0
    by_state: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class DailyDigest:
    """Daily compliance digest for email/dashboard."""
    date: date = field(default_factory=date.today)
    critical_alerts: list[ComplianceAlert] = field(default_factory=list)
    warning_alerts: list[ComplianceAlert] = field(default_factory=list)
    upcoming_deadlines: list[ComplianceDeadline] = field(default_factory=list)
    state_status: dict[str, str] = field(default_factory=dict)  # state_code → "ok"|"warning"|"critical"
    cost_estimate_q: ComplianceCostEstimate = field(default_factory=ComplianceCostEstimate)

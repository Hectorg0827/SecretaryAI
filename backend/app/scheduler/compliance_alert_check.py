"""
Compliance alert check — runs daily at 8 AM UTC.
Generates expiration alerts for licenses, permits, brand registrations, and COLA
records, then stores the digest in report_snapshots for all employees to read
(no automatic sending — a human reviews and acts from the Compliance UI).
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


async def check_compliance_alerts(company_id: str, db) -> dict:
    """
    Run the compliance alert engine for one company and upsert the result
    to report_snapshots.  Returns a summary dict for Celery logging.

    Parameters
    ----------
    company_id : str
        UUID of the company row in Supabase.
    db : supabase.Client
        Service-role Supabase client (bypasses RLS).

    Returns
    -------
    dict with keys: critical, warning, info, deadlines, stored
    """
    from app.intelligence.compliance.compliance_module import ComplianceModule, ComplianceConfig
    from app.intelligence.compliance.entity_registry import EntityRegistry

    result: dict = {
        "critical": 0,
        "warning": 0,
        "info": 0,
        "deadlines": 0,
        "stored": False,
        "error": None,
    }

    try:
        # Build the module and load entity data from Supabase
        config = ComplianceConfig(company_id=company_id)
        module = ComplianceModule(company_id=company_id, config=config)
        _load_registry_from_db(module._registry, db, company_id)

        # Generate alerts
        alerts = module.get_alerts(days_ahead=90)
        deadlines = module.get_deadlines(days_ahead=30)

        for alert in alerts:
            priority = getattr(alert, "priority", None)
            if priority == "critical":
                result["critical"] += 1
            elif priority == "warning":
                result["warning"] += 1
            else:
                result["info"] += 1
        result["deadlines"] = len(deadlines)

        # Serialise for storage
        def _serialise_alert(a) -> dict:
            d = {}
            for field in (
                "id", "alert_type", "priority", "state_code", "item_name",
                "days_until", "message", "action_required", "estimated_fee",
            ):
                val = getattr(a, field, None)
                d[field] = val
            expiry = getattr(a, "expiry_date", None)
            d["expiry_date"] = expiry.isoformat() if expiry else None
            d["renewal_url"] = getattr(a, "renewal_url", None)
            return d

        def _serialise_deadline(dl) -> dict:
            d = {}
            for field in ("deadline_type", "state_code", "frequency", "action", "notes"):
                d[field] = getattr(dl, field, None)
            due = getattr(dl, "due_date", None)
            d["due_date"] = due.isoformat() if due else None
            return d

        payload = {
            "company_id": company_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "critical_count": result["critical"],
            "warning_count": result["warning"],
            "info_count": result["info"],
            "deadline_count": result["deadlines"],
            "alerts": [_serialise_alert(a) for a in alerts],
            "deadlines": [_serialise_deadline(d) for d in deadlines],
        }

        # Upsert to report_snapshots — one row per (company, report_type)
        db.table("report_snapshots").upsert(
            {
                "company_id": company_id,
                "report_type": "compliance_alerts",
                "payload": payload,
                "generated_at": payload["generated_at"],
                "generated_by": "scheduler",
            },
            on_conflict="company_id,report_type",
        ).execute()

        result["stored"] = True
        log.info(
            "Compliance alert check for %s: %d critical, %d warning, %d info, %d deadlines",
            company_id,
            result["critical"],
            result["warning"],
            result["info"],
            result["deadlines"],
        )

    except Exception as exc:
        result["error"] = str(exc)
        log.error("Compliance alert check failed for %s: %s", company_id, exc)

    return result


def _load_registry_from_db(registry, db, company_id: str) -> None:
    """
    Populate an EntityRegistry from the compliance_* Supabase tables.
    Each table row is mapped to the corresponding dataclass.
    """
    from datetime import date
    from app.intelligence.compliance.models import (
        AlcoholProduct,
        FederalPermit,
        StateLicense,
        BrandRegistration,
        DistributorRelationship,
        ProductType,
        LicenseStatus,
        FederalPermitType,
        BrandRegStatus,
    )

    def _parse_date(val) -> date | None:
        if not val:
            return None
        try:
            return date.fromisoformat(str(val)[:10])
        except ValueError:
            return None

    # Federal permits
    try:
        rows = (
            db.table("compliance_federal_permits")
            .select("*")
            .eq("company_id", company_id)
            .execute()
            .data or []
        )
        for row in rows:
            registry.add_federal_permit(FederalPermit(
                id=row["id"],
                permit_type=FederalPermitType(row.get("permit_type", "importer")),
                permit_number=row.get("permit_number", ""),
                issue_date=_parse_date(row.get("issue_date")) or date.today(),
                expiration_date=_parse_date(row.get("expiration_date")) or date.today(),
                status=LicenseStatus(row.get("status", "active")),
                company_id=company_id,
            ))
    except Exception as exc:
        log.warning("Failed to load federal permits for %s: %s", company_id, exc)

    # State licenses
    try:
        rows = (
            db.table("compliance_licenses")
            .select("*")
            .eq("company_id", company_id)
            .execute()
            .data or []
        )
        for row in rows:
            registry.add_license(StateLicense(
                id=row["id"],
                state_code=row.get("state_code", ""),
                license_type=row.get("license_type", "importer"),
                license_number=row.get("license_number", ""),
                product_types=[ProductType(pt) for pt in (row.get("product_types") or [])],
                issue_date=_parse_date(row.get("issue_date")) or date.today(),
                expiration_date=_parse_date(row.get("expiration_date")) or date.today(),
                annual_fee=float(row.get("annual_fee") or 0),
                status=LicenseStatus(row.get("status", "active")),
                company_id=company_id,
            ))
    except Exception as exc:
        log.warning("Failed to load state licenses for %s: %s", company_id, exc)

    # Products
    try:
        rows = (
            db.table("compliance_products")
            .select("*")
            .eq("company_id", company_id)
            .execute()
            .data or []
        )
        for row in rows:
            registry.add_product(AlcoholProduct(
                id=row["id"],
                sku=row.get("sku", ""),
                name=row.get("name", ""),
                product_type=ProductType(row.get("product_type", "spirits")),
                abv_pct=float(row.get("abv_pct") or 0),
                container_size_ml=int(row.get("container_size_ml") or 750),
                units_per_case=int(row.get("units_per_case") or 12),
                country_of_origin=row.get("country_of_origin", ""),
                company_id=company_id,
            ))
    except Exception as exc:
        log.warning("Failed to load compliance products for %s: %s", company_id, exc)

    # Brand registrations
    try:
        rows = (
            db.table("compliance_brand_registrations")
            .select("*")
            .eq("company_id", company_id)
            .execute()
            .data or []
        )
        for row in rows:
            registry.add_brand_registration(BrandRegistration(
                id=row["id"],
                product_id=row.get("product_id", ""),
                state_code=row.get("state_code", ""),
                registration_number=row.get("registration_number", ""),
                registration_date=_parse_date(row.get("registration_date")) or date.today(),
                expiration_date=_parse_date(row.get("expiration_date")),
                registration_fee=float(row.get("registration_fee") or 0),
                status=BrandRegStatus(row.get("status", "active")),
                company_id=company_id,
            ))
    except Exception as exc:
        log.warning("Failed to load brand registrations for %s: %s", company_id, exc)

    # Distributors
    try:
        rows = (
            db.table("compliance_distributors")
            .select("*")
            .eq("company_id", company_id)
            .execute()
            .data or []
        )
        for row in rows:
            registry.add_distributor(DistributorRelationship(
                id=row["id"],
                state_code=row.get("state_code", ""),
                distributor_name=row.get("distributor_name", ""),
                territory=row.get("territory", ""),
                contract_start_date=_parse_date(row.get("contract_start_date")) or date.today(),
                contract_renewal_date=_parse_date(row.get("contract_renewal_date")),
                franchise_law_applies=bool(row.get("franchise_law_applies", False)),
                termination_restriction=row.get("termination_restriction", ""),
                company_id=company_id,
            ))
    except Exception as exc:
        log.warning("Failed to load distributors for %s: %s", company_id, exc)

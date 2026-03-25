"""
50-State (+ DC) Alcohol Beverage Compliance Rules Matrix.

Each StateRules row encodes the key regulatory dimensions that a national
importer/wholesaler must track. This is a static knowledge base — regulators
change rules, so fields include a `last_reviewed` marker for auditing.

Sources: TTB state alcohol authority directory, NABCA control state list,
state regulator websites, FAA Act / 27 CFR.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FranchiseLaw:
    exists: bool = False
    attachment_trigger: str = "first_sale"   # "first_sale" | "written_agreement" | "statute_defined"
    termination: str = "none"                # "none" | "notice_only" | "good_cause" | "prohibited"
    notice_days: int = 0
    notes: str = ""


@dataclass
class ExciseTaxRates:
    """Per-US-gallon excise tax rates (state only, not federal)."""
    wine_per_gallon: float = 0.0
    spirits_per_gallon: float = 0.0
    beer_per_gallon: float = 0.0


@dataclass
class StateRules:
    state_code: str = ""
    state_name: str = ""
    regulator_name: str = ""
    regulator_url: str = ""

    # Control state flags
    is_control_spirits: bool = False
    is_control_wine: bool = False
    is_control_beer: bool = False

    # Permit requirements
    supplier_permit_required_wine: bool = True
    supplier_permit_required_spirits: bool = True
    supplier_permit_required_beer: bool = True
    permit_types: list[str] = field(default_factory=list)

    # Brand registration
    brand_registration_required: bool = False
    brand_reg_fee_wine: float = 0.0
    brand_reg_fee_spirits: float = 0.0
    brand_reg_fee_beer: float = 0.0
    brand_reg_per: str = "brand"   # "brand" | "brand_per_year" | "sku"

    # Label
    state_label_approval_required: bool = False   # beyond federal COLA

    # Price posting
    price_posting_required_wine: bool = False
    price_posting_required_spirits: bool = False
    price_posting_required_beer: bool = False
    post_and_hold_days: int = 0

    # Franchise law
    franchise_law: FranchiseLaw = field(default_factory=FranchiseLaw)

    # Self-distribution
    self_distribution_wine: str = "no"      # "yes" | "limited" | "no"
    self_distribution_spirits: str = "no"
    self_distribution_beer: str = "no"

    # Excise tax
    excise_tax: ExciseTaxRates = field(default_factory=ExciseTaxRates)

    # Reporting
    reporting_frequency: str = "monthly"   # "monthly" | "quarterly" | "annual"

    # Local restrictions
    local_restrictions: bool = False
    local_notes: str = ""

    # Audit
    record_retention_years: int = 3

    # Priority tier: 1=critical markets, 2=important, 3=standard
    priority_tier: int = 3

    last_reviewed: str = "2025-01"


# ─── 50-State + DC Matrix ─────────────────────────────────────────────────────
# Populated with best-available regulatory data.
# Fees and rates are approximations — verify with current state schedules.

STATE_MATRIX: dict[str, StateRules] = {

"AL": StateRules(
    state_code="AL", state_name="Alabama",
    regulator_name="Alabama Alcoholic Beverage Control Board",
    regulator_url="https://www.abc.alabama.gov",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=100, brand_reg_fee_beer=50,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=60),
    excise_tax=ExciseTaxRates(wine_per_gallon=1.70, spirits_per_gallon=18.00, beer_per_gallon=1.05),
    reporting_frequency="monthly", priority_tier=2,
),

"AK": StateRules(
    state_code="AK", state_name="Alaska",
    regulator_name="Alcohol & Marijuana Control Office",
    regulator_url="https://www.commerce.alaska.gov/web/amco",
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=50, brand_reg_fee_beer=25,
    local_restrictions=True, local_notes="Over 150 communities have local-option restrictions",
    excise_tax=ExciseTaxRates(wine_per_gallon=2.50, spirits_per_gallon=12.80, beer_per_gallon=1.07),
    reporting_frequency="monthly", priority_tier=3,
),

"AZ": StateRules(
    state_code="AZ", state_name="Arizona",
    regulator_name="Department of Liquor Licenses and Control",
    regulator_url="https://www.azliquor.gov",
    brand_registration_required=False,
    price_posting_required_spirits=False,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.84, spirits_per_gallon=3.00, beer_per_gallon=0.16),
    reporting_frequency="monthly", priority_tier=2,
),

"AR": StateRules(
    state_code="AR", state_name="Arkansas",
    regulator_name="Alcohol Beverage Control Division",
    regulator_url="https://www.dfa.arkansas.gov/offices/abc",
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=50, brand_reg_fee_beer=25,
    local_restrictions=True, local_notes="Significant dry county and local-option complexity",
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=60),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.75, spirits_per_gallon=2.50, beer_per_gallon=0.23),
    reporting_frequency="monthly", priority_tier=3,
),

"CA": StateRules(
    state_code="CA", state_name="California",
    regulator_name="Department of Alcoholic Beverage Control",
    regulator_url="https://www.abc.ca.gov",
    brand_registration_required=False,
    price_posting_required_spirits=False,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90,
        notes="Beer franchise law; wine/spirits lighter protections"),
    self_distribution_wine="limited",
    excise_tax=ExciseTaxRates(wine_per_gallon=0.20, spirits_per_gallon=3.30, beer_per_gallon=0.20),
    reporting_frequency="monthly", priority_tier=1,
),

"CO": StateRules(
    state_code="CO", state_name="Colorado",
    regulator_name="Liquor Enforcement Division",
    regulator_url="https://sbg.colorado.gov/led",
    brand_registration_required=False,
    excise_tax=ExciseTaxRates(wine_per_gallon=0.28, spirits_per_gallon=2.28, beer_per_gallon=0.08),
    reporting_frequency="monthly", priority_tier=2,
),

"CT": StateRules(
    state_code="CT", state_name="Connecticut",
    regulator_name="Department of Consumer Protection - Liquor Control",
    regulator_url="https://portal.ct.gov/DCP",
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=100, brand_reg_fee_beer=50,
    price_posting_required_wine=True, price_posting_required_spirits=True,
    post_and_hold_days=30,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.72, spirits_per_gallon=5.40, beer_per_gallon=0.24),
    reporting_frequency="monthly", priority_tier=2,
),

"DE": StateRules(
    state_code="DE", state_name="Delaware",
    regulator_name="Office of the Alcoholic Beverage Control Commissioner",
    regulator_url="https://date.delaware.gov/abc",
    brand_registration_required=False,
    excise_tax=ExciseTaxRates(wine_per_gallon=0.97, spirits_per_gallon=3.75, beer_per_gallon=0.16),
    reporting_frequency="monthly", priority_tier=3,
),

"DC": StateRules(
    state_code="DC", state_name="District of Columbia",
    regulator_name="Alcoholic Beverage Regulation Administration",
    regulator_url="https://abra.dc.gov",
    brand_registration_required=True,
    brand_reg_fee_wine=150, brand_reg_fee_spirits=150, brand_reg_fee_beer=75,
    excise_tax=ExciseTaxRates(wine_per_gallon=0.30, spirits_per_gallon=1.50, beer_per_gallon=0.09),
    reporting_frequency="monthly", priority_tier=2,
),

"FL": StateRules(
    state_code="FL", state_name="Florida",
    regulator_name="Division of Alcoholic Beverages & Tobacco",
    regulator_url="https://www.myfloridalicense.com/DBPR/alcoholic-beverages-and-tobacco",
    brand_registration_required=True,
    brand_reg_fee_wine=125, brand_reg_fee_spirits=125, brand_reg_fee_beer=50,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90,
        notes="Strong franchise protections; territory modifications require agreement"),
    excise_tax=ExciseTaxRates(wine_per_gallon=2.25, spirits_per_gallon=6.50, beer_per_gallon=0.48),
    reporting_frequency="monthly", priority_tier=1,
),

"GA": StateRules(
    state_code="GA", state_name="Georgia",
    regulator_name="Department of Revenue - Alcohol & Tobacco Tax Division",
    regulator_url="https://dor.georgia.gov/alcohol-tobacco",
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=50, brand_reg_fee_beer=25,
    local_restrictions=True, local_notes="County-level wet/dry restrictions",
    excise_tax=ExciseTaxRates(wine_per_gallon=1.51, spirits_per_gallon=3.79, beer_per_gallon=1.01),
    reporting_frequency="monthly", priority_tier=2,
),

"HI": StateRules(
    state_code="HI", state_name="Hawaii",
    regulator_name="County Liquor Authorities (Honolulu, Hawaii, Kauai, Maui)",
    regulator_url="https://www.honolulupolice.gov/services/liquor-commission",
    brand_registration_required=False,
    local_restrictions=True,
    local_notes="CRITICAL: compliance is county-based, not state-based. Each county has its own liquor authority.",
    excise_tax=ExciseTaxRates(wine_per_gallon=1.38, spirits_per_gallon=5.98, beer_per_gallon=0.93),
    reporting_frequency="monthly", priority_tier=3,
),

"ID": StateRules(
    state_code="ID", state_name="Idaho",
    regulator_name="Idaho State Liquor Dispensary",
    regulator_url="https://liquor.idaho.gov",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=0, brand_reg_fee_beer=25,
    excise_tax=ExciseTaxRates(wine_per_gallon=0.45, spirits_per_gallon=0.0, beer_per_gallon=0.15),
    reporting_frequency="monthly", priority_tier=2,
),

"IL": StateRules(
    state_code="IL", state_name="Illinois",
    regulator_name="Illinois Liquor Control Commission",
    regulator_url="https://www.illinois.gov/agencylcc",
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=100, brand_reg_fee_beer=50,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=1.39, spirits_per_gallon=8.55, beer_per_gallon=0.23),
    reporting_frequency="monthly", priority_tier=1,
),

"IN": StateRules(
    state_code="IN", state_name="Indiana",
    regulator_name="Alcohol and Tobacco Commission",
    regulator_url="https://www.in.gov/atc",
    brand_registration_required=True,
    brand_reg_fee_wine=75, brand_reg_fee_spirits=75, brand_reg_fee_beer=35,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.47, spirits_per_gallon=2.68, beer_per_gallon=0.12),
    reporting_frequency="monthly", priority_tier=2,
),

"IA": StateRules(
    state_code="IA", state_name="Iowa",
    regulator_name="Iowa Alcoholic Beverages Division",
    regulator_url="https://abd.iowa.gov",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=25, brand_reg_fee_spirits=0, brand_reg_fee_beer=25,
    excise_tax=ExciseTaxRates(wine_per_gallon=1.75, spirits_per_gallon=0.0, beer_per_gallon=0.19),
    reporting_frequency="monthly", priority_tier=2,
),

"KS": StateRules(
    state_code="KS", state_name="Kansas",
    regulator_name="Division of Alcoholic Beverage Control",
    regulator_url="https://www.ksrevenue.gov/abc.html",
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=50, brand_reg_fee_beer=25,
    local_restrictions=True, local_notes="Local dry areas and club license complexity",
    excise_tax=ExciseTaxRates(wine_per_gallon=0.30, spirits_per_gallon=2.50, beer_per_gallon=0.18),
    reporting_frequency="monthly", priority_tier=3,
),

"KY": StateRules(
    state_code="KY", state_name="Kentucky",
    regulator_name="Department of Alcoholic Beverage Control",
    regulator_url="https://abc.ky.gov",
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=100, brand_reg_fee_beer=50,
    local_restrictions=True, local_notes="Local-option elections; significant dry county map",
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=60),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.50, spirits_per_gallon=1.92, beer_per_gallon=0.08),
    reporting_frequency="monthly", priority_tier=2,
),

"LA": StateRules(
    state_code="LA", state_name="Louisiana",
    regulator_name="Office of Alcohol and Tobacco Control",
    regulator_url="https://atc.louisiana.gov",
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=50, brand_reg_fee_beer=25,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.11, spirits_per_gallon=2.50, beer_per_gallon=0.32),
    reporting_frequency="monthly", priority_tier=2,
),

"ME": StateRules(
    state_code="ME", state_name="Maine",
    regulator_name="Bureau of Alcoholic Beverages and Lottery Operations",
    regulator_url="https://www.maine.gov/dafs/bablo",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=75, brand_reg_fee_spirits=0, brand_reg_fee_beer=35,
    excise_tax=ExciseTaxRates(wine_per_gallon=0.60, spirits_per_gallon=0.0, beer_per_gallon=0.35),
    reporting_frequency="monthly", priority_tier=2,
),

"MD": StateRules(
    state_code="MD", state_name="Maryland",
    regulator_name="Comptroller of Maryland - Field Enforcement Division",
    regulator_url="https://www.marylandtaxes.gov/alcohol",
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=100, brand_reg_fee_beer=50,
    local_restrictions=True,
    local_notes="Montgomery County operates as a control jurisdiction. County-level ABC boards statewide.",
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=60),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.40, spirits_per_gallon=1.50, beer_per_gallon=0.09),
    reporting_frequency="monthly", priority_tier=2,
),

"MA": StateRules(
    state_code="MA", state_name="Massachusetts",
    regulator_name="Alcoholic Beverages Control Commission",
    regulator_url="https://www.mass.gov/orgs/alcoholic-beverages-control-commission",
    brand_registration_required=True,
    brand_reg_fee_wine=150, brand_reg_fee_spirits=150, brand_reg_fee_beer=75,
    price_posting_required_wine=True, price_posting_required_spirits=True,
    post_and_hold_days=30,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.55, spirits_per_gallon=4.05, beer_per_gallon=0.11),
    reporting_frequency="monthly", priority_tier=1,
),

"MI": StateRules(
    state_code="MI", state_name="Michigan",
    regulator_name="Michigan Liquor Control Commission",
    regulator_url="https://www.michigan.gov/lara/0,4601,7-154-10570_16941---,00.html",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=0, brand_reg_fee_beer=50,
    price_posting_required_wine=True, price_posting_required_spirits=True,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.51, spirits_per_gallon=0.0, beer_per_gallon=0.20),
    reporting_frequency="monthly", priority_tier=1,
),

"MN": StateRules(
    state_code="MN", state_name="Minnesota",
    regulator_name="Alcohol and Gambling Enforcement Division",
    regulator_url="https://dps.mn.gov/divisions/age",
    brand_registration_required=True,
    brand_reg_fee_wine=75, brand_reg_fee_spirits=75, brand_reg_fee_beer=35,
    local_restrictions=True, local_notes="Municipal licensing adds complexity",
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.30, spirits_per_gallon=5.03, beer_per_gallon=0.15),
    reporting_frequency="monthly", priority_tier=2,
),

"MS": StateRules(
    state_code="MS", state_name="Mississippi",
    regulator_name="Alcohol Beverage Control Division",
    regulator_url="https://www.dor.ms.gov/alcohol-beverage-control",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=75, brand_reg_fee_spirits=0, brand_reg_fee_beer=35,
    local_restrictions=True, local_notes="Dry county map; many areas remain restricted",
    excise_tax=ExciseTaxRates(wine_per_gallon=0.35, spirits_per_gallon=0.0, beer_per_gallon=0.43),
    reporting_frequency="monthly", priority_tier=3,
),

"MO": StateRules(
    state_code="MO", state_name="Missouri",
    regulator_name="Division of Alcohol and Tobacco Control",
    regulator_url="https://atc.dps.mo.gov",
    brand_registration_required=False,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.42, spirits_per_gallon=2.00, beer_per_gallon=0.06),
    reporting_frequency="monthly", priority_tier=2,
),

"MT": StateRules(
    state_code="MT", state_name="Montana",
    regulator_name="Montana Department of Revenue - Liquor License Bureau",
    regulator_url="https://mtrevenue.gov/liquor-tobacco/liquor-licenses",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=0, brand_reg_fee_beer=25,
    excise_tax=ExciseTaxRates(wine_per_gallon=1.06, spirits_per_gallon=0.0, beer_per_gallon=0.14),
    reporting_frequency="monthly", priority_tier=3,
),

"NE": StateRules(
    state_code="NE", state_name="Nebraska",
    regulator_name="Nebraska Liquor Control Commission",
    regulator_url="https://nlcc.nebraska.gov",
    brand_registration_required=True,
    brand_reg_fee_wine=75, brand_reg_fee_spirits=75, brand_reg_fee_beer=35,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.95, spirits_per_gallon=3.75, beer_per_gallon=0.31),
    reporting_frequency="monthly", priority_tier=3,
),

"NV": StateRules(
    state_code="NV", state_name="Nevada",
    regulator_name="Nevada Department of Taxation",
    regulator_url="https://tax.nv.gov/Collections/Liquor_License_Information",
    brand_registration_required=False,
    local_restrictions=True, local_notes="County and city licensing overlaps Clark/Washoe",
    excise_tax=ExciseTaxRates(wine_per_gallon=0.70, spirits_per_gallon=3.60, beer_per_gallon=0.16),
    reporting_frequency="monthly", priority_tier=2,
),

"NH": StateRules(
    state_code="NH", state_name="New Hampshire",
    regulator_name="New Hampshire Liquor Commission",
    regulator_url="https://www.liquorandwineoutlets.com",
    is_control_spirits=True,
    is_control_wine=True,
    brand_registration_required=True,
    brand_reg_fee_wine=0, brand_reg_fee_spirits=0, brand_reg_fee_beer=25,
    excise_tax=ExciseTaxRates(wine_per_gallon=0.0, spirits_per_gallon=0.0, beer_per_gallon=0.30),
    reporting_frequency="monthly", priority_tier=2,
),

"NJ": StateRules(
    state_code="NJ", state_name="New Jersey",
    regulator_name="Division of Alcoholic Beverage Control",
    regulator_url="https://www.nj.gov/lps/abc",
    brand_registration_required=True,
    brand_reg_fee_wine=200, brand_reg_fee_spirits=200, brand_reg_fee_beer=100,
    price_posting_required_wine=True, price_posting_required_spirits=True,
    post_and_hold_days=30,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=180,
        notes="Strong franchise protection; extensive case law"),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.88, spirits_per_gallon=5.50, beer_per_gallon=0.12),
    reporting_frequency="monthly", priority_tier=1,
),

"NM": StateRules(
    state_code="NM", state_name="New Mexico",
    regulator_name="Regulation & Licensing Department - Alcohol & Gaming Division",
    regulator_url="https://www.rld.nm.gov/alcohol-and-gaming",
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=50, brand_reg_fee_beer=25,
    excise_tax=ExciseTaxRates(wine_per_gallon=1.70, spirits_per_gallon=6.06, beer_per_gallon=0.41),
    reporting_frequency="monthly", priority_tier=3,
),

"NY": StateRules(
    state_code="NY", state_name="New York",
    regulator_name="State Liquor Authority",
    regulator_url="https://sla.ny.gov",
    brand_registration_required=True,
    brand_reg_fee_wine=150, brand_reg_fee_spirits=150, brand_reg_fee_beer=75,
    price_posting_required_wine=True, price_posting_required_spirits=True,
    post_and_hold_days=30,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=180,
        notes="Highly active regulator; tied-house and brand ownership rules strictly enforced"),
    local_restrictions=True, local_notes="NYC has additional requirements; some local dry areas",
    excise_tax=ExciseTaxRates(wine_per_gallon=0.30, spirits_per_gallon=6.44, beer_per_gallon=0.14),
    reporting_frequency="monthly", record_retention_years=3,
    priority_tier=1,
),

"NC": StateRules(
    state_code="NC", state_name="North Carolina",
    regulator_name="North Carolina ABC Commission",
    regulator_url="https://www.ncabc.com",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=0, brand_reg_fee_beer=50,
    local_restrictions=True, local_notes="County/city ABC boards control retail; dry areas exist",
    excise_tax=ExciseTaxRates(wine_per_gallon=26.34, spirits_per_gallon=0.0, beer_per_gallon=0.62),
    reporting_frequency="monthly", priority_tier=1,
),

"ND": StateRules(
    state_code="ND", state_name="North Dakota",
    regulator_name="Office of the State Tax Commissioner - Alcohol Tax",
    regulator_url="https://www.nd.gov/tax/user/businesses/alcohol",
    brand_registration_required=True,
    brand_reg_fee_wine=25, brand_reg_fee_spirits=25, brand_reg_fee_beer=15,
    excise_tax=ExciseTaxRates(wine_per_gallon=1.03, spirits_per_gallon=2.50, beer_per_gallon=0.16),
    reporting_frequency="monthly", priority_tier=3,
),

"OH": StateRules(
    state_code="OH", state_name="Ohio",
    regulator_name="Division of Liquor Control",
    regulator_url="https://com.ohio.gov/divisions/liquor-control",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=0, brand_reg_fee_beer=50,
    price_posting_required_wine=True, price_posting_required_spirits=True,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=60),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.32, spirits_per_gallon=0.0, beer_per_gallon=0.18),
    reporting_frequency="monthly", priority_tier=1,
),

"OK": StateRules(
    state_code="OK", state_name="Oklahoma",
    regulator_name="ABLE Commission",
    regulator_url="https://able.ok.gov",
    brand_registration_required=True,
    brand_reg_fee_wine=75, brand_reg_fee_spirits=75, brand_reg_fee_beer=35,
    local_restrictions=True, local_notes="Complex dry/wet map; ABLE + Tax Commission both involved",
    excise_tax=ExciseTaxRates(wine_per_gallon=0.72, spirits_per_gallon=5.56, beer_per_gallon=0.40),
    reporting_frequency="monthly", priority_tier=3,
),

"OR": StateRules(
    state_code="OR", state_name="Oregon",
    regulator_name="Oregon Liquor and Cannabis Commission",
    regulator_url="https://www.oregon.gov/olcc",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=0, brand_reg_fee_beer=50,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.67, spirits_per_gallon=0.0, beer_per_gallon=0.08),
    reporting_frequency="monthly", priority_tier=2,
),

"PA": StateRules(
    state_code="PA", state_name="Pennsylvania",
    regulator_name="Pennsylvania Liquor Control Board",
    regulator_url="https://www.lcb.pa.gov",
    is_control_spirits=True,
    is_control_wine=True,
    brand_registration_required=True,
    brand_reg_fee_wine=0, brand_reg_fee_spirits=0, brand_reg_fee_beer=75,
    state_label_approval_required=True,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.0, spirits_per_gallon=0.0, beer_per_gallon=0.08),
    reporting_frequency="monthly",
    local_notes="One of the most system-driven states; all spirits/wine sold through state stores",
    priority_tier=1,
),

"RI": StateRules(
    state_code="RI", state_name="Rhode Island",
    regulator_name="Department of Business Regulation - Liquor Enforcement",
    regulator_url="https://dbr.ri.gov/divisions/commercial-licensing/liquor-enforcement",
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=100, brand_reg_fee_beer=50,
    price_posting_required_wine=True, price_posting_required_spirits=True,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=1.40, spirits_per_gallon=5.40, beer_per_gallon=0.11),
    reporting_frequency="monthly", priority_tier=2,
),

"SC": StateRules(
    state_code="SC", state_name="South Carolina",
    regulator_name="Department of Revenue & Taxation - Alcohol Licensing",
    regulator_url="https://dor.sc.gov/tax/alcohol",
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=50, brand_reg_fee_beer=25,
    local_restrictions=True, local_notes="County referenda and local restrictions apply",
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.90, spirits_per_gallon=5.36, beer_per_gallon=0.77),
    reporting_frequency="monthly", priority_tier=2,
),

"SD": StateRules(
    state_code="SD", state_name="South Dakota",
    regulator_name="Department of Revenue - Division of Special Taxes",
    regulator_url="https://dor.sd.gov/businesses/licenses/alcohol-licenses",
    brand_registration_required=False,
    excise_tax=ExciseTaxRates(wine_per_gallon=0.93, spirits_per_gallon=3.93, beer_per_gallon=0.27),
    reporting_frequency="monthly", priority_tier=3,
),

"TN": StateRules(
    state_code="TN", state_name="Tennessee",
    regulator_name="Tennessee Alcoholic Beverage Commission",
    regulator_url="https://www.tn.gov/abc",
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=100, brand_reg_fee_beer=50,
    local_restrictions=True, local_notes="Many dry counties; local referendum required",
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=1.21, spirits_per_gallon=4.46, beer_per_gallon=1.29),
    reporting_frequency="monthly", priority_tier=2,
),

"TX": StateRules(
    state_code="TX", state_name="Texas",
    regulator_name="Texas Alcoholic Beverage Commission",
    regulator_url="https://www.tabc.texas.gov",
    brand_registration_required=True,
    brand_reg_fee_wine=150, brand_reg_fee_spirits=150, brand_reg_fee_beer=75,
    local_restrictions=True, local_notes="Complex dry/wet map; local option elections",
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90,
        notes="TABC brand ownership disclosure and invoicing requirements are strict"),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.204, spirits_per_gallon=2.40, beer_per_gallon=0.19),
    reporting_frequency="monthly", priority_tier=1,
),

"UT": StateRules(
    state_code="UT", state_name="Utah",
    regulator_name="Department of Alcoholic Beverage Control",
    regulator_url="https://abc.utah.gov",
    is_control_spirits=True,
    is_control_wine=True,
    brand_registration_required=True,
    brand_reg_fee_wine=0, brand_reg_fee_spirits=0, brand_reg_fee_beer=50,
    excise_tax=ExciseTaxRates(wine_per_gallon=0.0, spirits_per_gallon=0.0, beer_per_gallon=0.41),
    reporting_frequency="monthly",
    local_notes="Highly structured; product entry and channel legality must be verified before launch",
    priority_tier=2,
),

"VT": StateRules(
    state_code="VT", state_name="Vermont",
    regulator_name="Department of Liquor and Lottery",
    regulator_url="https://liquorandlottery.vermont.gov",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=0, brand_reg_fee_beer=25,
    excise_tax=ExciseTaxRates(wine_per_gallon=0.55, spirits_per_gallon=0.0, beer_per_gallon=0.27),
    reporting_frequency="monthly", priority_tier=3,
),

"VA": StateRules(
    state_code="VA", state_name="Virginia",
    regulator_name="Virginia Alcoholic Beverage Control Authority",
    regulator_url="https://www.abc.virginia.gov",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=0, brand_reg_fee_beer=50,
    price_posting_required_wine=True, price_posting_required_spirits=True,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=1.51, spirits_per_gallon=0.0, beer_per_gallon=0.26),
    reporting_frequency="monthly", priority_tier=1,
),

"WA": StateRules(
    state_code="WA", state_name="Washington",
    regulator_name="Liquor and Cannabis Board",
    regulator_url="https://lcb.wa.gov",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=100, brand_reg_fee_spirits=0, brand_reg_fee_beer=50,
    price_posting_required_wine=True, price_posting_required_spirits=True,
    post_and_hold_days=30,
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.87, spirits_per_gallon=0.0, beer_per_gallon=0.26),
    reporting_frequency="monthly", priority_tier=2,
),

"WV": StateRules(
    state_code="WV", state_name="West Virginia",
    regulator_name="Alcohol Beverage Control Administration",
    regulator_url="https://abca.wv.gov",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=0, brand_reg_fee_beer=25,
    excise_tax=ExciseTaxRates(wine_per_gallon=1.00, spirits_per_gallon=0.0, beer_per_gallon=0.18),
    reporting_frequency="monthly", priority_tier=3,
),

"WI": StateRules(
    state_code="WI", state_name="Wisconsin",
    regulator_name="Department of Revenue - Alcohol & Tobacco Enforcement",
    regulator_url="https://www.revenue.wi.gov/pages/doingbusiness/alcohol.aspx",
    brand_registration_required=True,
    brand_reg_fee_wine=75, brand_reg_fee_spirits=75, brand_reg_fee_beer=35,
    local_restrictions=True, local_notes="Municipal licensing adds significant complexity",
    franchise_law=FranchiseLaw(exists=True, termination="good_cause", notice_days=90),
    excise_tax=ExciseTaxRates(wine_per_gallon=0.25, spirits_per_gallon=3.25, beer_per_gallon=0.06),
    reporting_frequency="monthly", priority_tier=2,
),

"WY": StateRules(
    state_code="WY", state_name="Wyoming",
    regulator_name="Wyoming Liquor Commission",
    regulator_url="https://wyomliquor.wyo.gov",
    is_control_spirits=True,
    brand_registration_required=True,
    brand_reg_fee_wine=50, brand_reg_fee_spirits=0, brand_reg_fee_beer=25,
    excise_tax=ExciseTaxRates(wine_per_gallon=0.28, spirits_per_gallon=0.0, beer_per_gallon=0.02),
    reporting_frequency="monthly", priority_tier=3,
),

}


# ─── Control state lookup ─────────────────────────────────────────────────────

CONTROL_STATE_SPIRITS = {s for s, r in STATE_MATRIX.items() if r.is_control_spirits}
CONTROL_STATE_WINE = {s for s, r in STATE_MATRIX.items() if r.is_control_wine}
CONTROL_STATE_BEER = {s for s, r in STATE_MATRIX.items() if r.is_control_beer}

TIER_1_STATES = {s for s, r in STATE_MATRIX.items() if r.priority_tier == 1}


def get_state_rules(state_code: str) -> Optional[StateRules]:
    """Return the StateRules for a given two-letter state code (or DC)."""
    return STATE_MATRIX.get(state_code.upper())


def list_states_by_tier(tier: int) -> list[str]:
    return [s for s, r in STATE_MATRIX.items() if r.priority_tier == tier]

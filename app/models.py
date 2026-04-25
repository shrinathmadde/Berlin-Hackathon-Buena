from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel


# ---------------------------------------------------------------------------
# Structured entity tables — one row per real-world thing, clean columns only.
# ---------------------------------------------------------------------------


class Property(SQLModel, table=True):
    __tablename__ = "properties"

    property_id: str = Field(primary_key=True)  # LIE-001
    name: str
    street: str
    postal_code: str
    city: str
    country: str = "DE"
    built_year: Optional[int] = None
    renovated_year: Optional[int] = None

    manager_name: Optional[str] = None
    manager_street: Optional[str] = None
    manager_postal_code: Optional[str] = None
    manager_city: Optional[str] = None
    manager_email: Optional[str] = None
    manager_phone: Optional[str] = None
    manager_iban: Optional[str] = None
    manager_bic: Optional[str] = None
    manager_bank: Optional[str] = None
    manager_tax_number: Optional[str] = None

    weg_account_iban: Optional[str] = None
    weg_account_bic: Optional[str] = None
    weg_account_bank: Optional[str] = None
    reserve_account_iban: Optional[str] = None
    reserve_account_bic: Optional[str] = None


class Building(SQLModel, table=True):
    __tablename__ = "buildings"

    building_id: str = Field(primary_key=True)  # HAUS-12
    property_id: str = Field(foreign_key="properties.property_id", index=True)
    house_number: str
    units_count: Optional[int] = None
    floors: Optional[int] = None
    has_elevator: bool = False
    built_year: Optional[int] = None


class Unit(SQLModel, table=True):
    __tablename__ = "units"

    unit_id: str = Field(primary_key=True)  # EH-001
    building_id: str = Field(foreign_key="buildings.building_id", index=True)
    property_id: str = Field(foreign_key="properties.property_id", index=True)
    owner_id: Optional[str] = Field(default=None, foreign_key="owners.owner_id", index=True)

    unit_number: str
    location: Optional[str] = None  # "1. OG links"
    type: str  # Wohnung / Tiefgarage / Gewerbe
    area_sqm: Optional[float] = None
    rooms: Optional[float] = None
    ownership_share: Optional[int] = None  # miteigentumsanteil


class Owner(SQLModel, table=True):
    __tablename__ = "owners"

    owner_id: str = Field(primary_key=True)  # EIG-001
    salutation: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None

    street: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    country: str = "DE"

    email: Optional[str] = Field(default=None, index=True)
    phone: Optional[str] = None
    iban: Optional[str] = None
    bic: Optional[str] = None

    is_self_user: bool = False  # selbstnutzer
    has_sev_mandate: bool = False  # SEV mandate
    is_council_member: bool = False  # beirat
    language: str = "de"


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"

    tenant_id: str = Field(primary_key=True)  # MIE-001
    salutation: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    email: Optional[str] = Field(default=None, index=True)
    phone: Optional[str] = None

    unit_id: Optional[str] = Field(default=None, foreign_key="units.unit_id", index=True)
    landlord_owner_id: Optional[str] = Field(default=None, foreign_key="owners.owner_id", index=True)

    lease_start: Optional[date] = None
    lease_end: Optional[date] = None  # null = active

    cold_rent: Optional[float] = None
    utility_advance: Optional[float] = None
    deposit: Optional[float] = None

    iban: Optional[str] = None
    bic: Optional[str] = None
    language: str = "de"


class ServiceProvider(SQLModel, table=True):
    __tablename__ = "service_providers"

    provider_id: str = Field(primary_key=True)  # DL-001
    company: str
    branch: Optional[str] = Field(default=None, index=True)  # Hausmeisterdienst, etc.
    contact_person: Optional[str] = None

    email: Optional[str] = Field(default=None, index=True)
    phone: Optional[str] = None

    street: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    country: str = "DE"

    iban: Optional[str] = None
    bic: Optional[str] = None
    vat_id: Optional[str] = None
    tax_number: Optional[str] = None

    style: Optional[str] = None  # writing style label from dataset
    language: str = "de"
    monthly_contract: Optional[float] = None
    hourly_rate: Optional[float] = None


class BankTransaction(SQLModel, table=True):
    __tablename__ = "bank_transactions"

    transaction_id: str = Field(primary_key=True)  # TX-00001
    property_id: str = Field(foreign_key="properties.property_id", index=True)
    booking_date: date = Field(index=True)
    direction: str  # CREDIT / DEBIT
    amount: float
    category: Optional[str] = Field(default=None, index=True)  # miete / hausgeld / dienstleister / ...
    counterparty_name: Optional[str] = None
    purpose: Optional[str] = None
    reference_id: Optional[str] = Field(default=None, index=True)  # MIE-XXX, DL-XXX, EIG-XXX
    error_types: Optional[str] = None


class Invoice(SQLModel, table=True):
    __tablename__ = "invoices"

    invoice_id: str = Field(primary_key=True)  # INV-00195
    invoice_number: Optional[str] = None  # INV-2026-0195
    invoice_date: date = Field(index=True)
    provider_id: Optional[str] = Field(default=None, foreign_key="service_providers.provider_id", index=True)
    provider_company: Optional[str] = None
    recipient: Optional[str] = None
    property_id: Optional[str] = Field(default=None, foreign_key="properties.property_id", index=True)

    net_amount: Optional[float] = None
    vat_amount: Optional[float] = None
    gross_amount: Optional[float] = None
    iban: Optional[str] = None

    paid_transaction_id: Optional[str] = Field(default=None, foreign_key="bank_transactions.transaction_id")
    source_event_id: Optional[str] = Field(default=None, foreign_key="source_events.event_id", index=True)
    error_types: Optional[str] = None


# ---------------------------------------------------------------------------
# Source events — every email, PDF, CSV row, etc. that produced a fact.
# ---------------------------------------------------------------------------


class SourceEvent(SQLModel, table=True):
    __tablename__ = "source_events"

    event_id: str = Field(primary_key=True)  # EMAIL-00001 / LTR-0001 / INV-00195 / TX-00001
    source_type: str = Field(index=True)  # email / pdf_letter / pdf_invoice / bank_tx / csv_import
    property_id: Optional[str] = Field(default=None, foreign_key="properties.property_id", index=True)
    source_path: Optional[str] = None
    received_at: Optional[datetime] = Field(default=None, index=True)

    # Email-specific (nullable for non-email sources)
    thread_id: Optional[str] = Field(default=None, index=True)
    direction: Optional[str] = None  # incoming / outgoing
    from_address: Optional[str] = Field(default=None, index=True)
    to_address: Optional[str] = None
    subject: Optional[str] = None
    category: Optional[str] = Field(default=None, index=True)  # eigentuemer/rechtlich, etc.
    language: Optional[str] = None

    raw_content: Optional[str] = None  # optional snippet/full body
    error_types: Optional[str] = None


# ---------------------------------------------------------------------------
# Facts table — the flexible layer for anything that doesn't fit a column.
# Every fact is tied to (property, entity, category) with full provenance
# and a supersession chain so updates stay surgical and history is preserved.
# ---------------------------------------------------------------------------


class Fact(SQLModel, table=True):
    __tablename__ = "facts"

    fact_id: str = Field(primary_key=True)
    property_id: str = Field(foreign_key="properties.property_id", index=True)

    entity_type: str = Field(index=True)  # owner / tenant / unit / building / service_provider / property
    entity_id: str = Field(index=True)

    category: str = Field(index=True)  # communication_preference / open_issue / decision / complaint / ...
    statement: str  # human-readable fact, e.g. "prefers WhatsApp for urgent issues"

    source_event_id: Optional[str] = Field(default=None, foreign_key="source_events.event_id", index=True)
    extracted_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    superseded_by: Optional[str] = Field(default=None, foreign_key="facts.fact_id")
    status: str = Field(default="active", index=True)  # active / superseded / conflicted
    confidence: Optional[float] = None

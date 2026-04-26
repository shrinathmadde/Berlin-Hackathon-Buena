from __future__ import annotations

import csv
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from functools import lru_cache
from io import BytesIO, StringIO
from pathlib import PurePosixPath
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.database import get_session
from app.llm import LLMError, LLMProvider, get_gpt_provider, get_qwen_provider
from app.models import (
    BankTransaction,
    Building,
    Fact,
    Invoice,
    Owner,
    Property,
    ServiceProvider,
    SourceEvent,
    Tenant,
    Unit,
)

router = APIRouter(prefix="/api", tags=["sql"])

MODELS = (
    Property,
    Building,
    Unit,
    Owner,
    Tenant,
    ServiceProvider,
    BankTransaction,
    Invoice,
    SourceEvent,
    Fact,
)
WRITE_PREFIXES = {"insert", "update", "delete", "create", "drop", "alter", "replace"}
TABLE_TO_MODEL = {model.__table__.name: model for model in MODELS}
ID_PATTERNS = (
    r"(EMAIL-\d+)",
    r"(LTR-\d+)",
    r"(INV-\d+)",
    r"(TX-\d+)",
    r"(EIG-\d+)",
    r"(MIE-\d+)",
    r"(DL-\d+)",
    r"(EH-\d+)",
    r"(HAUS-\d+)",
    r"(LIE-\d+)",
)
CSV_TARGET_TABLES = {
    "eigentuemer.csv": "owners",
    "mieter.csv": "tenants",
    "einheiten.csv": "units",
    "dienstleister.csv": "service_providers",
    "bank_index.csv": "bank_transactions",
    "emails_index.csv": "source_events",
    "rechnungen_index.csv": "invoices",
    "kontoauszug.csv": "bank_transactions",
}
CSV_COLUMN_MAPS = {
    "eigentuemer.csv": {
        "id": "owner_id",
        "anrede": "salutation",
        "vorname": "first_name",
        "nachname": "last_name",
        "firma": "company",
        "strasse": "street",
        "plz": "postal_code",
        "ort": "city",
        "land": "country",
        "email": "email",
        "telefon": "phone",
        "iban": "iban",
        "bic": "bic",
        "selbstnutzer": "is_self_user",
        "sev_mandat": "has_sev_mandate",
        "beirat": "is_council_member",
        "sprache": "language",
    },
    "mieter.csv": {
        "id": "tenant_id",
        "anrede": "salutation",
        "vorname": "first_name",
        "nachname": "last_name",
        "email": "email",
        "telefon": "phone",
        "einheit_id": "unit_id",
        "eigentuemer_id": "landlord_owner_id",
        "mietbeginn": "lease_start",
        "mietende": "lease_end",
        "kaltmiete": "cold_rent",
        "nk_vorauszahlung": "utility_advance",
        "kaution": "deposit",
        "iban": "iban",
        "bic": "bic",
        "sprache": "language",
    },
    "einheiten.csv": {
        "id": "unit_id",
        "haus_id": "building_id",
        "einheit_nr": "unit_number",
        "lage": "location",
        "typ": "type",
        "wohnflaeche_qm": "area_sqm",
        "zimmer": "rooms",
        "miteigentumsanteil": "ownership_share",
    },
    "dienstleister.csv": {
        "id": "provider_id",
        "firma": "company",
        "branche": "branch",
        "ansprechpartner": "contact_person",
        "email": "email",
        "telefon": "phone",
        "strasse": "street",
        "plz": "postal_code",
        "ort": "city",
        "land": "country",
        "iban": "iban",
        "bic": "bic",
        "ust_id": "vat_id",
        "steuernummer": "tax_number",
        "stil": "style",
        "sprache": "language",
        "vertrag_monatlich": "monthly_contract",
        "stundensatz": "hourly_rate",
    },
    "bank_index.csv": {
        "id": "transaction_id",
        "datum": "booking_date",
        "typ": "direction",
        "betrag": "amount",
        "kategorie": "category",
        "gegen_name": "counterparty_name",
        "verwendungszweck": "purpose",
        "referenz_id": "reference_id",
        "error_types": "error_types",
    },
    "emails_index.csv": {
        "id": "event_id",
        "datetime": "received_at",
        "thread_id": "thread_id",
        "direction": "direction",
        "from_email": "from_address",
        "to_email": "to_address",
        "subject": "subject",
        "category": "category",
        "sprache": "language",
        "error_types": "error_types",
    },
    "rechnungen_index.csv": {
        "id": "invoice_id",
        "rechnungsnr": "invoice_number",
        "datum": "invoice_date",
        "dienstleister_id": "provider_id",
        "dienstleister_firma": "provider_company",
        "empfaenger": "recipient",
        "netto": "net_amount",
        "mwst": "vat_amount",
        "brutto": "gross_amount",
        "iban": "iban",
        "error_types": "error_types",
    },
    "kontoauszug.csv": {
        "Buchungstag": "booking_date",
        "Beguenstigter/Zahlungspflichtiger": "counterparty_name",
        "Verwendungszweck": "purpose",
        "Kundenreferenz (End-to-End)": "transaction_id",
    },
}


def _column_summary(model: type[Any]) -> str:
    columns: list[str] = []
    for column in model.__table__.columns:
        annotations: list[str] = [str(column.type)]
        if column.primary_key:
            annotations.append("PK")
        annotations.append("REQUIRED" if not column.nullable else "NULLABLE")
        for foreign_key in column.foreign_keys:
            annotations.append(
                f"FK->{foreign_key.column.table.name}.{foreign_key.column.name}"
            )
        columns.append(f"{column.name} [{' '.join(annotations)}]")
    return f"{model.__table__.name}({', '.join(columns)})"


@lru_cache(maxsize=1)
def _schema_summary() -> str:
    return "\n".join(_column_summary(model) for model in MODELS)


@lru_cache(maxsize=1)
def _sql_query_system_prompt() -> str:
    return (
        "You translate natural-language requests into SQLite SQL for the database below.\n"
        "Return exactly one SQL statement and nothing else.\n"
        "Do not add markdown fences, comments, or explanations.\n"
        "Prefer SELECT statements unless the user explicitly asks to modify data.\n"
        "Use only the tables and columns in this schema.\n\n"
        f"SCHEMA:\n{_schema_summary()}"
    )


@lru_cache(maxsize=1)
def _agent_system_prompt() -> str:
    return (
        "You answer questions about a German property-management database by running SQL.\n"
        "On every turn, output exactly one JSON object — no prose, no markdown fences:\n"
        '  {"tool": "run_sql", "sql": "SELECT ..."}\n'
        '  {"tool": "final", "answer": "natural-language answer for the user"}\n'
        "Rules:\n"
        "- SELECT only. Writes are rejected.\n"
        "- Add LIMIT 50 unless you are aggregating.\n"
        "- After 1-3 SELECTs you should have enough; emit \"final\" then.\n"
        "- The final answer must be plain prose grounded in observed rows.\n\n"
        f"SCHEMA:\n{_schema_summary()}"
    )


@lru_cache(maxsize=1)
def _document_ingest_system_prompt() -> str:
    schema = DocumentExtraction.model_json_schema()
    return (
        "You extract structured records from one German property-management document.\n"
        "Return valid JSON only. No markdown fences. No prose. No comments.\n"
        "The JSON must match the schema exactly.\n"
        "Use only the database tables and columns listed below.\n"
        "Create records only when the document clearly supports them.\n"
        "For email/letter/unstructured communication, prefer a source_events record and facts records.\n"
        "For email replies, extract material facts from both the latest reply and quoted prior messages; "
        "do not drop quoted repair requests, costs, approvals, appointments, or dates just because they are quoted.\n"
        "For invoices, prefer invoices plus source_events when provenance is useful.\n"
        "For bank CSV rows or statements, prefer bank_transactions.\n"
        "For master data files, use owners, tenants, units, buildings, properties, or service_providers as needed.\n"
        "Extract each distinct business fact as its own facts record. Do not collapse a termination, handover, "
        "repair request, repair date, and repair cost into one generic fact.\n"
        "Do not invent unsupported columns.\n"
        "Do not emit fields that are not present in the document unless they are obvious defaults.\n"
        "facts.entity_type MUST be exactly one of: owner, tenant, unit, building, service_provider, property. "
        "Do not invent values like 'person' or 'repair'.\n"
        "facts.entity_id MUST be a business ID — EIG-XXX (owner), MIE-XXX (tenant), EH-XXX (unit), "
        "HAUS-XXX (building), DL-XXX (service_provider), LIE-XXX (property). "
        "If the document only identifies the entity by email address, emit that email as entity_id and the server "
        "will resolve it. Do not invent IDs.\n"
        "Whenever the document mentions a tenant, owner, or service_provider — even by name + email only — also "
        "emit an upsert into the matching master-data table (tenants / owners / service_providers) carrying every "
        "field the document supplies (first_name, last_name, email, phone, company, etc.). If you do not know the "
        "business primary key (tenant_id / owner_id / provider_id) but you have an email, omit the primary key "
        "field entirely; the server will resolve it via email or synthesize a stable ID. Never invent a primary "
        "key value.\n"
        "For lease terminations / Kündigungen, emit a fact with entity_type='tenant' and category='termination', "
        "and write the termination date in the statement using DD.MM.YYYY or ISO form so it can be promoted to "
        "tenants.lease_end.\n"
        "For handover / Wohnungsuebergabe / Wohnungsübergabe details, emit a separate fact with "
        "entity_type='tenant' and category='handover' when a tenant is identified.\n"
        "For repair addenda / Nachtrag Reparatur / additional parts or costs, emit separate facts with "
        "category='repair_request' or category='repair_cost_estimate'. If no unit, provider, or tenant business "
        "ID is known, use entity_type='property' and entity_id='LIE-001'. Include exact dates and amounts from "
        "the document, preserving comma decimal amounts such as 3180,62 EUR in the statement.\n"
        "When a property id is needed and the document is clearly about the seeded property, use 'LIE-001'.\n"
        "Use ISO dates and datetimes where possible.\n"
        "Use exact table names from the schema.\n\n"
        f"SCHEMA:\n{_schema_summary()}"
        "\n\nJSON SCHEMA:\n"
        f"{json.dumps(schema, ensure_ascii=True)}"
    )


def _document_ingest_user_prompt(text: str, document_path: str | None) -> str:
    path_line = f"Document path: {document_path}\n" if document_path else ""
    return f"{path_line}Document text:\n\"\"\"\n{text}\n\"\"\""


def _csv_kind(document_path: str | None) -> str | None:
    if not document_path:
        return None
    name = PurePosixPath(document_path).name.lower()
    if name in CSV_TARGET_TABLES:
        return name
    if name.startswith("kontoauszug") and name.endswith(".csv"):
        return "kontoauszug.csv"
    return None


def _is_csv_document(document_path: str | None) -> bool:
    return _csv_kind(document_path) is not None


def _csv_delimiter(text: str) -> str:
    header = text.splitlines()[0] if text else ""
    return ";" if header.count(";") > header.count(",") else ","


def _clean_csv_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _parse_decimal(value: str) -> float:
    cleaned = value.replace(" ", "")
    if "," in cleaned:
        if "." in cleaned and cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", ".")
    return float(cleaned)


def _parse_csv_date(value: str) -> str:
    cleaned = value.strip()
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", cleaned):
        return datetime.strptime(cleaned, "%d.%m.%Y").date().isoformat()
    return cleaned


def _infer_bank_reference_id(purpose: str | None) -> str | None:
    if not purpose:
        return None
    match = re.search(r"\b(MIE-\d+|DL-\d+|EIG-\d+)\b", purpose)
    return match.group(1) if match else None


def _infer_bank_category(purpose: str | None) -> str | None:
    if not purpose:
        return None
    normalized = purpose.lower()
    if "miete" in normalized:
        return "miete"
    if "hausgeld" in normalized:
        return "hausgeld"
    if "rechnung" in normalized:
        return "dienstleister"
    if any(token in normalized for token in {"strom", "gas", "wasser", "entsorgung"}):
        return "versorger"
    return None


def _map_csv_row(csv_kind: str, row: dict[str, str | None]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for source_key, target_key in CSV_COLUMN_MAPS[csv_kind].items():
        value = _clean_csv_value(row.get(source_key))
        if value is None:
            continue
        record[target_key] = value

    if csv_kind == "emails_index.csv":
        filename = _clean_csv_value(row.get("filename"))
        month_dir = _clean_csv_value(row.get("month_dir"))
        record.setdefault("source_type", "email")
        if filename and month_dir:
            record.setdefault("source_path", f"emails/{month_dir}/{filename}")
    elif csv_kind == "kontoauszug.csv":
        amount_text = _clean_csv_value(row.get("Betrag"))
        if amount_text is not None:
            amount = _parse_decimal(amount_text)
            record["amount"] = abs(amount)
            record["direction"] = "DEBIT" if amount < 0 else "CREDIT"
        booking_date = _clean_csv_value(row.get("Buchungstag"))
        if booking_date is not None:
            record["booking_date"] = _parse_csv_date(booking_date)
        purpose = record.get("purpose")
        record.setdefault("reference_id", _infer_bank_reference_id(purpose))
        record.setdefault("category", _infer_bank_category(purpose))
    return record


def _csv_extraction(text: str, document_path: str | None) -> "DocumentExtraction":
    csv_kind = _csv_kind(document_path)
    if csv_kind is None:
        raise HTTPException(422, "Unsupported CSV format")

    reader = csv.DictReader(StringIO(text), delimiter=_csv_delimiter(text))
    rows = list(reader)
    operations: list[ExtractOperation] = []
    target_table = CSV_TARGET_TABLES[csv_kind]

    for row in rows:
        record = _map_csv_row(csv_kind, row)
        if not record:
            continue
        operations.append(ExtractOperation(table=target_table, record=record))

    return DocumentExtraction(
        summary=f"Loaded {len(operations)} rows from CSV into {target_table}.",
        records=operations,
    )


def _is_write_sql(sql: str) -> bool:
    parts = sql.lstrip().split(None, 1)
    return bool(parts) and parts[0].lower() in WRITE_PREFIXES


def _strip_sql_fences(sql: str) -> str:
    cleaned = sql.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _extract_json_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _default_property_id(record: dict[str, Any], table_name: str) -> None:
    if table_name in {"bank_transactions", "invoices", "source_events", "facts", "units", "buildings"}:
        record.setdefault("property_id", "LIE-001")


def _derived_id(document_path: str | None) -> str | None:
    if not document_path:
        return None
    for pattern in ID_PATTERNS:
        match = re.search(pattern, document_path)
        if match:
            return match.group(1)
    return None


def _default_source_type(document_path: str | None) -> str:
    path = (document_path or "").lower()
    if ".eml" in path or "email" in path:
        return "email"
    if ".pdf" in path and "rechn" in path:
        return "pdf_invoice"
    if ".pdf" in path:
        return "pdf_letter"
    if ".csv" in path or ".xml" in path or ".json" in path:
        return "csv_import"
    return "note"


def _prepare_record(
    table_name: str,
    record: dict[str, Any],
    *,
    document_path: str | None,
    document_text: str | None,
) -> dict[str, Any]:
    prepared = dict(record)
    _default_property_id(prepared, table_name)

    if table_name == "source_events":
        prepared.setdefault("event_id", _derived_id(document_path) or f"NOTE-{uuid4().hex[:8]}")
        prepared.setdefault("source_type", _default_source_type(document_path))
        if document_path:
            prepared.setdefault("source_path", document_path)
        if document_text:
            prepared.setdefault("raw_content", document_text[:5000])
    elif table_name == "facts":
        prepared.setdefault("fact_id", f"FACT-{uuid4().hex[:8]}")
        prepared.setdefault("status", "active")
        prepared.setdefault("extracted_at", datetime.utcnow().isoformat())
    elif table_name == "invoices":
        prepared.setdefault("invoice_id", _derived_id(document_path))
    elif table_name == "bank_transactions":
        prepared.setdefault("transaction_id", _derived_id(document_path))
    elif table_name == "owners":
        prepared.setdefault("owner_id", _derived_id(document_path))
    elif table_name == "tenants":
        prepared.setdefault("tenant_id", _derived_id(document_path))
    elif table_name == "service_providers":
        prepared.setdefault("provider_id", _derived_id(document_path))
    elif table_name == "units":
        prepared.setdefault("unit_id", _derived_id(document_path))
    elif table_name == "buildings":
        prepared.setdefault("building_id", _derived_id(document_path))
    elif table_name == "properties":
        prepared.setdefault("property_id", _derived_id(document_path) or "LIE-001")
    return prepared


def _primary_key_name(model: type[Any]) -> str:
    for column in model.__table__.columns:
        if column.primary_key:
            return column.name
    raise RuntimeError(f"No primary key found for {model.__name__}")


_TERMINATION_CATEGORIES = {
    "termination",
    "lease_termination",
    "lease_end",
    "kuendigung",
    "kündigung",
}
_GERMAN_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")

# Person-like entity types that have a master table keyed by email.
_PERSON_TABLE = {
    "tenant": "tenants",
    "owner": "owners",
    "service_provider": "service_providers",
}
_PERSON_PK = {
    "tenants": "tenant_id",
    "owners": "owner_id",
    "service_providers": "provider_id",
}
_PERSON_ID_PREFIX = {
    "tenants": "MIE-AUTO",
    "owners": "EIG-AUTO",
    "service_providers": "DL-AUTO",
}
_PERSON_MODEL = {
    "tenants": Tenant,
    "owners": Owner,
    "service_providers": ServiceProvider,
}
_TABLE_TO_ENTITY_TYPE = {table: entity for entity, table in _PERSON_TABLE.items()}


def _parse_german_date(text: str | None) -> date | None:
    if not text:
        return None
    match = _GERMAN_DATE_RE.search(text)
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _synthetic_business_id(table: str, email: str) -> str:
    """Stable per-email ID. Same email always yields the same ID, so re-extraction is idempotent."""
    digest = hashlib.sha1(email.lower().encode("utf-8")).hexdigest()[:8].upper()
    return f"{_PERSON_ID_PREFIX[table]}-{digest}"


def _lookup_business_id_by_email(table: str, email: str, session: Session) -> str | None:
    if table == "tenants":
        return session.exec(select(Tenant.tenant_id).where(Tenant.email == email)).first()
    if table == "owners":
        return session.exec(select(Owner.owner_id).where(Owner.email == email)).first()
    if table == "service_providers":
        return session.exec(
            select(ServiceProvider.provider_id).where(ServiceProvider.email == email)
        ).first()
    return None


def _seed_synthetic_master(table: str, business_id: str, email: str) -> dict[str, Any]:
    """Minimal record to satisfy NOT NULL columns on the master table."""
    record: dict[str, Any] = {_PERSON_PK[table]: business_id, "email": email}
    if table == "service_providers":
        # ServiceProvider.company is NOT NULL — derive a placeholder from the email domain.
        domain = email.split("@", 1)[1] if "@" in email else email
        record["company"] = domain or "(unbekannt)"
    return record


def _normalize_extraction(extraction: "DocumentExtraction", session: Session) -> None:
    """Resolve emails to business IDs, synthesize master rows on miss, and promote termination facts."""
    # Pass 1: master-data ops (tenants/owners/service_providers). Fill missing PK from email.
    for op in extraction.records:
        if op.table not in _PERSON_PK:
            continue
        pk = _PERSON_PK[op.table]
        record = op.record
        if record.get(pk):
            continue
        email = record.get("email")
        if not isinstance(email, str) or "@" not in email:
            continue
        normalized_email = email.strip().lower()
        record["email"] = normalized_email
        resolved = _lookup_business_id_by_email(op.table, normalized_email, session)
        record[pk] = resolved or _synthetic_business_id(op.table, normalized_email)

    # Index master-data ops already in the extraction so we can merge into them.
    master_index: dict[tuple[str, str], dict[str, Any]] = {}
    for op in extraction.records:
        if op.table not in _PERSON_PK:
            continue
        pk_value = op.record.get(_PERSON_PK[op.table])
        if isinstance(pk_value, str):
            master_index[(op.table, pk_value)] = op.record

    # Pass 2: resolve fact entity_ids (email -> existing or synthetic business ID).
    new_masters: dict[tuple[str, str], dict[str, Any]] = {}
    for op in extraction.records:
        if op.table != "facts":
            continue
        record = op.record
        entity_type = record.get("entity_type")
        entity_id = record.get("entity_id")
        if not (isinstance(entity_type, str) and isinstance(entity_id, str)):
            continue
        if "@" not in entity_id:
            continue
        table = _PERSON_TABLE.get(entity_type)
        if table is None:
            continue
        email = entity_id.strip().lower()
        resolved = _lookup_business_id_by_email(table, email, session)
        if resolved is None:
            resolved = _synthetic_business_id(table, email)
            key = (table, resolved)
            if key not in master_index and key not in new_masters:
                new_masters[key] = _seed_synthetic_master(table, resolved, email)
            elif key in master_index:
                master_index[key].setdefault("email", email)
        record["entity_id"] = resolved

    # Pass 3: promote termination facts to tenants.lease_end on the matching tenant row.
    for op in extraction.records:
        if op.table != "facts":
            continue
        record = op.record
        if record.get("entity_type") != "tenant":
            continue
        category = (record.get("category") or "").strip().lower()
        if category not in _TERMINATION_CATEGORIES:
            continue
        tenant_id = record.get("entity_id")
        if not isinstance(tenant_id, str) or not tenant_id.startswith("MIE-"):
            continue
        parsed = _parse_german_date(record.get("statement"))
        if parsed is None:
            continue
        lease_end_iso = parsed.isoformat()
        key = ("tenants", tenant_id)
        if key in master_index:
            master_index[key]["lease_end"] = lease_end_iso
        elif key in new_masters:
            new_masters[key]["lease_end"] = lease_end_iso
        elif session.get(Tenant, tenant_id) is not None:
            new_masters[key] = {"tenant_id": tenant_id, "lease_end": lease_end_iso}

    for (table, _pk_value), record in new_masters.items():
        extraction.records.append(ExtractOperation(table=table, record=record))


def _upsert_extraction(
    extraction: "DocumentExtraction",
    session: Session,
    *,
    document_path: str | None,
    document_text: str | None,
) -> list["WriteRecord"]:
    _normalize_extraction(extraction, session)
    writes: list[WriteRecord] = []
    for operation in extraction.records:
        model = TABLE_TO_MODEL[operation.table]
        prepared = _prepare_record(
            operation.table,
            operation.record,
            document_path=document_path,
            document_text=document_text,
        )
        obj = model.model_validate(prepared)
        pk_name = _primary_key_name(model)
        pk_value = getattr(obj, pk_name, None)
        if not pk_value:
            raise ValueError(f"Missing primary key for {operation.table}")
        existing = session.get(model, pk_value)
        if existing is None:
            session.add(obj)
            status = "created"
        else:
            for key, value in obj.model_dump(exclude_unset=True).items():
                if key != pk_name:
                    setattr(existing, key, value)
            session.add(existing)
            status = "updated"
        writes.append(WriteRecord(table=operation.table, primary_key=str(pk_value), status=status))
    return writes


def _split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single_quote = False
    i = 0
    while i < len(sql):
        char = sql[i]
        current.append(char)
        if char == "'":
            next_char = sql[i + 1] if i + 1 < len(sql) else ""
            if in_single_quote and next_char == "'":
                current.append(next_char)
                i += 1
            else:
                in_single_quote = not in_single_quote
        elif char == ";" and not in_single_quote:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement[:-1].strip() if statement.endswith(";") else statement)
            current = []
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return [statement for statement in statements if statement]


class SQLRequest(BaseModel):
    question: str | None = Field(default=None, description="Natural-language database request")
    mode: Literal["query", "document_extract"] = "query"
    text: str | None = Field(default=None, description="Document text to extract into structured records")
    document_path: str | None = Field(default=None, description="Optional source path for the document")


class AskRequest(BaseModel):
    question: str | None = Field(default=None, description="Natural-language property question")
    query: str | None = Field(default=None, description="Alias for question")
    agentic: bool = Field(default=False, description="Run a bounded ReAct loop instead of one-shot SQL")


class ExtractOperation(BaseModel):
    table: Literal[
        "properties",
        "buildings",
        "units",
        "owners",
        "tenants",
        "service_providers",
        "bank_transactions",
        "invoices",
        "source_events",
        "facts",
    ]
    record: dict[str, Any] = Field(default_factory=dict)


class DocumentExtraction(BaseModel):
    summary: str = ""
    records: list[ExtractOperation] = Field(default_factory=list)


class WriteRecord(BaseModel):
    table: str
    primary_key: str
    status: Literal["created", "updated"]


class ModelExtractionResult(BaseModel):
    label: str
    model: str | None = None
    latency_ms: float
    raw_model_output: str | None = None
    extraction: DocumentExtraction | None = None
    error: str | None = None


class SQLResponse(BaseModel):
    mode: Literal["query", "document_extract"]
    model: str
    returns_rows: bool
    row_count: int
    execution_ms: float
    sql: str | None = None
    statement_count: int = 0
    rows: list[dict[str, Any]] = Field(default_factory=list)
    extraction: DocumentExtraction | None = None
    writes: list[WriteRecord] = Field(default_factory=list)
    comparisons: list[ModelExtractionResult] = Field(default_factory=list)
    raw_model_output: str | None = None
    answer: str | None = None
    agent_steps: list[dict[str, Any]] = Field(default_factory=list)


def _extract_file_text(filename: str | None, content: bytes) -> str:
    if not content:
        raise HTTPException(422, "uploaded file is empty")

    path = (filename or "").lower()
    if path.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception as e:
            raise HTTPException(422, f"could not extract text from PDF: {e}") from e

    return content.decode("utf-8", errors="replace").strip()


def _extract_document_with_provider(
    text: str,
    document_path: str | None,
    provider: LLMProvider,
) -> tuple[DocumentExtraction, str]:
    raw_json = provider.complete(
        _document_ingest_user_prompt(text, document_path),
        system=_document_ingest_system_prompt(),
        max_tokens=8192,
        temperature=0,
    )
    json_text = _extract_json_text(raw_json)
    if not json_text:
        raise ValueError("LLM returned empty JSON")
    return DocumentExtraction.model_validate_json(json_text), raw_json


def _comparison_extraction(
    provider_factory: Any,
    text: str,
    document_path: str | None,
) -> ModelExtractionResult:
    started_at = perf_counter()
    provider: LLMProvider | None = None
    try:
        provider = provider_factory()
        extraction, raw_model_output = _extract_document_with_provider(text, document_path, provider)
        return ModelExtractionResult(
            label=provider.model_name,
            model=provider.model_name,
            latency_ms=round((perf_counter() - started_at) * 1000, 2),
            raw_model_output=raw_model_output,
            extraction=extraction,
        )
    except Exception as e:
        return ModelExtractionResult(
            label=provider.model_name if provider else "unavailable",
            model=provider.model_name if provider else None,
            latency_ms=round((perf_counter() - started_at) * 1000, 2),
            error=str(e),
        )


def _extract_comparison_results(text: str, document_path: str | None) -> list[ModelExtractionResult]:
    jobs = [
        get_gpt_provider,
        get_qwen_provider,
    ]
    results: list[ModelExtractionResult | None] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = {
            executor.submit(_comparison_extraction, provider_factory, text, document_path): index
            for index, provider_factory in enumerate(jobs)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [result for result in results if result is not None]


def _process_document_text(
    text: str,
    document_path: str | None,
    session: Session,
) -> SQLResponse:
    if not text:
        raise HTTPException(422, "no text could be extracted from the document")

    if _is_csv_document(document_path):
        extraction = _csv_extraction(text, document_path)
        started_at = perf_counter()
        try:
            writes = _upsert_extraction(
                extraction,
                session,
                document_path=document_path,
                document_text=None,
            )
            session.commit()
        except (SQLAlchemyError, ValueError) as e:
            session.rollback()
            raise HTTPException(400, detail={"extraction": extraction.model_dump(), "error": str(e)}) from e
        execution_ms = round((perf_counter() - started_at) * 1000, 2)
        return SQLResponse(
            mode="document_extract",
            model="local-csv-loader",
            returns_rows=False,
            row_count=len(writes),
            execution_ms=execution_ms,
            raw_model_output=extraction.model_dump_json(indent=2),
            extraction=extraction,
            writes=writes,
        )

    comparisons = _extract_comparison_results(text, document_path)
    primary_result = comparisons[0] if comparisons else None
    if primary_result is None or primary_result.extraction is None:
        error = primary_result.error if primary_result else "primary comparison result was not returned"
        raise HTTPException(502, f"Primary extraction failed: {error}")

    extraction = primary_result.extraction

    started_at = perf_counter()
    try:
        writes = _upsert_extraction(
            extraction,
            session,
            document_path=document_path,
            document_text=text,
        )
        session.commit()
    except (SQLAlchemyError, ValueError) as e:
        session.rollback()
        raise HTTPException(400, detail={"extraction": extraction.model_dump(), "error": str(e)}) from e
    execution_ms = round((perf_counter() - started_at) * 1000, 2)
    return SQLResponse(
        mode="document_extract",
        model=primary_result.model or "unknown",
        returns_rows=False,
        row_count=len(writes),
        execution_ms=execution_ms,
        raw_model_output=primary_result.raw_model_output,
        extraction=extraction,
        writes=writes,
        comparisons=comparisons,
    )


def _run_question_as_sql(question: str | None, session: Session) -> SQLResponse:
    if not question or not question.strip():
        raise HTTPException(422, "question is required")

    try:
        provider = get_gpt_provider()
    except RuntimeError as e:
        raise HTTPException(502, f"LLM call failed: {e}") from e

    try:
        raw_sql = provider.complete(
            question,
            system=_sql_query_system_prompt(),
            temperature=0,
        )
    except LLMError as e:
        raise HTTPException(502, f"LLM call failed: {e}") from e

    sql = _strip_sql_fences(raw_sql)
    if not sql:
        raise HTTPException(502, "LLM returned empty SQL")

    statements = _split_sql_statements(sql)
    if not statements:
        raise HTTPException(502, "LLM returned no executable SQL statements")

    started_at = perf_counter()
    try:
        rows: list[dict[str, Any]] = []
        returns_rows = False
        row_count = 0
        should_commit = any(_is_write_sql(statement) for statement in statements)
        connection = session.connection()

        for statement in statements:
            result = connection.exec_driver_sql(statement)
            if result.returns_rows:
                rows = [dict(row) for row in result.mappings().all()]
                returns_rows = True
                row_count = len(rows)
            elif not returns_rows:
                current_row_count = 0 if result.rowcount is None or result.rowcount < 0 else result.rowcount
                row_count += current_row_count

        execution_ms = round((perf_counter() - started_at) * 1000, 2)

        if should_commit:
            session.commit()

        return SQLResponse(
            mode="query",
            sql=sql,
            model=provider.model_name,
            statement_count=len(statements),
            returns_rows=returns_rows,
            row_count=row_count,
            execution_ms=execution_ms,
            rows=jsonable_encoder(rows),
        )
    except SQLAlchemyError as e:
        session.rollback()
        raise HTTPException(400, detail={"sql": sql, "error": str(e)}) from e


_AGENT_MAX_STEPS = 6
_AGENT_MAX_ROWS_FETCHED = 200
_AGENT_MAX_OBSERVATION_ROWS = 50


def _exec_agent_select(session: Session, sql: str) -> list[dict[str, Any]]:
    if _is_write_sql(sql):
        raise ValueError("only SELECT permitted in agent mode")
    result = session.connection().exec_driver_sql(sql)
    if not result.returns_rows:
        return []
    return [dict(row) for row in result.mappings().fetchmany(_AGENT_MAX_ROWS_FETCHED)]


def _parse_agent_action(raw: str) -> dict[str, Any]:
    text = _extract_json_text(raw)
    if not text:
        raise ValueError("empty response")
    return json.loads(text)


def _run_question_agentic(question: str | None, session: Session) -> SQLResponse:
    if not question or not question.strip():
        raise HTTPException(422, "question is required")

    try:
        provider = get_gpt_provider()
    except RuntimeError as e:
        raise HTTPException(502, f"LLM call failed: {e}") from e

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _agent_system_prompt()},
        {"role": "user", "content": question},
    ]
    steps: list[dict[str, Any]] = []
    started_at = perf_counter()

    for _ in range(_AGENT_MAX_STEPS):
        try:
            raw = provider.complete_messages(messages, temperature=0)
        except LLMError as e:
            raise HTTPException(502, f"LLM call failed: {e}") from e

        try:
            action = _parse_agent_action(raw)
        except (ValueError, json.JSONDecodeError):
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {"role": "user", "content": "ERROR: response was not valid JSON. Emit one JSON object only."}
            )
            steps.append({"raw": raw[:500], "error": "invalid_json"})
            continue

        tool = action.get("tool")

        if tool == "final":
            answer = str(action.get("answer", "")).strip()
            execution_ms = round((perf_counter() - started_at) * 1000, 2)
            last_with_rows = next(
                (s for s in reversed(steps) if isinstance(s.get("rows"), list) and s.get("rows")),
                None,
            )
            return SQLResponse(
                mode="query",
                model=provider.model_name,
                returns_rows=bool(last_with_rows),
                row_count=sum(len(s.get("rows", [])) for s in steps if isinstance(s.get("rows"), list)),
                execution_ms=execution_ms,
                sql=steps[-1].get("sql") if steps else None,
                statement_count=sum(1 for s in steps if "sql" in s and "error" not in s),
                rows=jsonable_encoder((last_with_rows or {}).get("rows", [])),
                answer=answer,
                agent_steps=jsonable_encoder(steps),
            )

        if tool == "run_sql":
            sql = _strip_sql_fences(str(action.get("sql", "")))
            if not sql:
                steps.append({"sql": "", "error": "empty"})
                obs = "ERROR: empty sql"
            else:
                try:
                    rows = _exec_agent_select(session, sql)
                    steps.append({"sql": sql, "rows": rows})
                    truncated = rows[:_AGENT_MAX_OBSERVATION_ROWS]
                    obs = json.dumps(jsonable_encoder(truncated), default=str)
                    if len(rows) > _AGENT_MAX_OBSERVATION_ROWS:
                        obs += f"\n... ({len(rows) - _AGENT_MAX_OBSERVATION_ROWS} more rows truncated)"
                except (SQLAlchemyError, ValueError) as e:
                    session.rollback()
                    steps.append({"sql": sql, "error": str(e)})
                    obs = f"SQL ERROR: {e}"
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"OBSERVATION:\n{obs}"})
            continue

        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {"role": "user", "content": f"ERROR: unknown tool {tool!r}. Use 'run_sql' or 'final'."}
        )
        steps.append({"raw": raw[:500], "error": f"unknown_tool:{tool}"})

    raise HTTPException(504, detail={"error": "agent exceeded step budget", "steps": jsonable_encoder(steps)})


@router.post("/process-file", response_model=SQLResponse)
async def process_file(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> SQLResponse:
    content = await file.read()
    text = _extract_file_text(file.filename, content)
    return _process_document_text(text, file.filename, session)


@router.post("/ask", response_model=SQLResponse)
def ask_property_question(
    payload: AskRequest,
    session: Session = Depends(get_session),
) -> SQLResponse:
    question = payload.question or payload.query
    if payload.agentic:
        return _run_question_agentic(question, session)
    return _run_question_as_sql(question, session)


@router.post("/sql", response_model=SQLResponse)
def run_sql(
    payload: SQLRequest,
    session: Session = Depends(get_session),
) -> SQLResponse:
    if payload.mode == "document_extract":
        if not payload.text:
            raise HTTPException(422, "text is required when mode=document_extract")
        return _process_document_text(payload.text, payload.document_path, session)

    return _run_question_as_sql(payload.question, session)

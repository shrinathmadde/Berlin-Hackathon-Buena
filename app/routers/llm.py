from __future__ import annotations

import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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
from sqlmodel import Session

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
def _document_ingest_system_prompt() -> str:
    schema = DocumentExtraction.model_json_schema()
    return (
        "You extract structured records from one German property-management document.\n"
        "Return valid JSON only. No markdown fences. No prose. No comments.\n"
        "The JSON must match the schema exactly.\n"
        "Use only the database tables and columns listed below.\n"
        "Create records only when the document clearly supports them.\n"
        "For email/letter/unstructured communication, prefer a source_events record and facts records.\n"
        "For invoices, prefer invoices plus source_events when provenance is useful.\n"
        "For bank CSV rows or statements, prefer bank_transactions.\n"
        "For master data files, use owners, tenants, units, buildings, properties, or service_providers as needed.\n"
        "Do not invent unsupported columns.\n"
        "Do not emit fields that are not present in the document unless they are obvious defaults.\n"
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


def _upsert_extraction(
    extraction: "DocumentExtraction",
    session: Session,
    *,
    document_path: str | None,
    document_text: str | None,
) -> list["WriteRecord"]:
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
            for key, value in obj.model_dump(exclude_unset=False).items():
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
) -> DocumentExtraction:
    raw_json = provider.complete(
        _document_ingest_user_prompt(text, document_path),
        system=_document_ingest_system_prompt(),
        temperature=0,
    )
    json_text = _extract_json_text(raw_json)
    if not json_text:
        raise ValueError("LLM returned empty JSON")
    return DocumentExtraction.model_validate_json(json_text)


def _comparison_extraction(
    label: str,
    provider_factory: Any,
    text: str,
    document_path: str | None,
) -> ModelExtractionResult:
    started_at = perf_counter()
    provider: LLMProvider | None = None
    try:
        provider = provider_factory()
        extraction = _extract_document_with_provider(text, document_path, provider)
        return ModelExtractionResult(
            label=label,
            model=provider.model_name,
            latency_ms=round((perf_counter() - started_at) * 1000, 2),
            extraction=extraction,
        )
    except Exception as e:
        return ModelExtractionResult(
            label=label,
            model=provider.model_name if provider else None,
            latency_ms=round((perf_counter() - started_at) * 1000, 2),
            error=str(e),
        )


def _extract_comparison_results(text: str, document_path: str | None) -> list[ModelExtractionResult]:
    jobs = [
        ("GPT-5.5", get_gpt_provider),
        ("Qwen", get_qwen_provider),
    ]
    results: list[ModelExtractionResult] = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = [
            executor.submit(_comparison_extraction, label, provider_factory, text, document_path)
            for label, provider_factory in jobs
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda result: 0 if result.label == "GPT-5.5" else 1)


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
            extraction=extraction,
            writes=writes,
        )

    comparisons = _extract_comparison_results(text, document_path)
    gpt_result = next((result for result in comparisons if result.label == "GPT-5.5"), None)
    if gpt_result is None or gpt_result.extraction is None:
        error = gpt_result.error if gpt_result else "GPT-5.5 comparison result was not returned"
        raise HTTPException(502, f"GPT-5.5 extraction failed: {error}")

    extraction = gpt_result.extraction

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
        model=gpt_result.model or "gpt-5.5",
        returns_rows=False,
        row_count=len(writes),
        execution_ms=execution_ms,
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
    return _run_question_as_sql(payload.question or payload.query, session)


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

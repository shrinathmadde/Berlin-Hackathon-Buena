from __future__ import annotations

from functools import lru_cache
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.database import get_session
from app.llm import LLMError, get_llm_provider
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


def _column_summary(model: type[Any]) -> str:
    columns: list[str] = []
    for column in model.__table__.columns:
        annotations: list[str] = [str(column.type)]
        if column.primary_key:
            annotations.append("PK")
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
def _sql_system_prompt() -> str:
    return (
        "You translate natural-language requests into SQLite SQL for the database below.\n"
        "Return exactly one SQL statement and nothing else.\n"
        "Do not add markdown fences, comments, or explanations.\n"
        "Prefer SELECT statements unless the user explicitly asks to modify data.\n"
        "Use only the tables and columns in this schema.\n\n"
        f"SCHEMA:\n{_schema_summary()}"
    )


def _is_write_sql(sql: str) -> bool:
    parts = sql.lstrip().split(None, 1)
    return bool(parts) and parts[0].lower() in WRITE_PREFIXES


class SQLRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Natural-language request to run")


class SQLResponse(BaseModel):
    sql: str
    model: str
    returns_rows: bool
    row_count: int
    execution_ms: float
    rows: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/sql", response_model=SQLResponse)
def run_sql(
    payload: SQLRequest,
    session: Session = Depends(get_session),
) -> SQLResponse:
    try:
        provider = get_llm_provider()
        sql = provider.complete(
            payload.question,
            system=_sql_system_prompt(),
            temperature=0,
        ).strip()
    except (LLMError, RuntimeError) as e:
        raise HTTPException(502, f"LLM call failed: {e}") from e

    if not sql:
        raise HTTPException(502, "LLM returned empty SQL")

    started_at = perf_counter()
    try:
        result = session.connection().exec_driver_sql(sql)
        execution_ms = round((perf_counter() - started_at) * 1000, 2)
        should_commit = _is_write_sql(sql)

        if result.returns_rows:
            rows = [dict(row) for row in result.mappings().all()]
            if should_commit:
                session.commit()
            return SQLResponse(
                sql=sql,
                model=provider.model_name,
                returns_rows=True,
                row_count=len(rows),
                execution_ms=execution_ms,
                rows=jsonable_encoder(rows),
            )

        if should_commit:
            session.commit()

        row_count = 0 if result.rowcount is None or result.rowcount < 0 else result.rowcount
        return SQLResponse(
            sql=sql,
            model=provider.model_name,
            returns_rows=False,
            row_count=row_count,
            execution_ms=execution_ms,
        )
    except SQLAlchemyError as e:
        session.rollback()
        raise HTTPException(400, detail={"sql": sql, "error": str(e)}) from e

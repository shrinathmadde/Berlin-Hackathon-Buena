"""Custom routes for the facts table.

Beyond plain CRUD, facts need:
  - filtering by entity / category / status (the hot query path for context queries)
  - a supersede operation that atomically inserts a new fact and links the old one
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_session
from app.models import Fact

router = APIRouter(prefix="/facts", tags=["facts"])


class FactCreate(BaseModel):
    property_id: str
    entity_type: str
    entity_id: str
    category: str
    statement: str
    source_event_id: Optional[str] = None
    confidence: Optional[float] = None


class SupersedeRequest(BaseModel):
    new_statement: str
    source_event_id: Optional[str] = None
    confidence: Optional[float] = None


@router.get("", response_model=list[Fact])
def list_facts(
    property_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = "active",
    limit: int = Query(100, le=1000),
    offset: int = 0,
    session: Session = Depends(get_session),
):
    query = select(Fact)
    if property_id:
        query = query.where(Fact.property_id == property_id)
    if entity_type:
        query = query.where(Fact.entity_type == entity_type)
    if entity_id:
        query = query.where(Fact.entity_id == entity_id)
    if category:
        query = query.where(Fact.category == category)
    if status:
        query = query.where(Fact.status == status)
    query = query.order_by(Fact.extracted_at.desc()).offset(offset).limit(limit)
    return session.exec(query).all()


@router.get("/{fact_id}", response_model=Fact)
def get_fact(fact_id: str, session: Session = Depends(get_session)):
    fact = session.get(Fact, fact_id)
    if fact is None:
        raise HTTPException(404, f"Fact {fact_id} not found")
    return fact


@router.post("", response_model=Fact, status_code=201)
def create_fact(payload: FactCreate, session: Session = Depends(get_session)):
    fact = Fact(
        fact_id=f"FACT-{uuid4().hex[:12]}",
        extracted_at=datetime.utcnow(),
        status="active",
        **payload.model_dump(),
    )
    session.add(fact)
    session.commit()
    session.refresh(fact)
    return fact


@router.post("/{fact_id}/supersede", response_model=Fact)
def supersede_fact(
    fact_id: str,
    payload: SupersedeRequest,
    session: Session = Depends(get_session),
):
    """Replace an existing fact with a new statement.

    The old fact stays in the table with status=superseded and a pointer to the
    new fact. The new fact inherits the same entity/category — this is the
    surgical-update path the brief asks for.
    """
    old = session.get(Fact, fact_id)
    if old is None:
        raise HTTPException(404, f"Fact {fact_id} not found")
    if old.status != "active":
        raise HTTPException(409, f"Fact {fact_id} is not active (status={old.status})")

    new = Fact(
        fact_id=f"FACT-{uuid4().hex[:12]}",
        property_id=old.property_id,
        entity_type=old.entity_type,
        entity_id=old.entity_id,
        category=old.category,
        statement=payload.new_statement,
        source_event_id=payload.source_event_id,
        confidence=payload.confidence,
        extracted_at=datetime.utcnow(),
        status="active",
    )
    session.add(new)
    session.flush()

    old.status = "superseded"
    old.superseded_by = new.fact_id
    session.add(old)

    session.commit()
    session.refresh(new)
    return new


@router.post("/{fact_id}/conflict", response_model=Fact)
def mark_conflicted(fact_id: str, session: Session = Depends(get_session)):
    fact = session.get(Fact, fact_id)
    if fact is None:
        raise HTTPException(404, f"Fact {fact_id} not found")
    fact.status = "conflicted"
    session.add(fact)
    session.commit()
    session.refresh(fact)
    return fact


@router.delete("/{fact_id}", status_code=204)
def delete_fact(fact_id: str, session: Session = Depends(get_session)):
    fact = session.get(Fact, fact_id)
    if fact is None:
        raise HTTPException(404, f"Fact {fact_id} not found")
    session.delete(fact)
    session.commit()
    return None

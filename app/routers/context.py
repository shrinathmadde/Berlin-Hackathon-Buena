"""Aggregate context endpoints — the "give me everything about X" path.

These are the queries that the AI agent will hit most. Each one is a single
indexed lookup against structured tables, optionally joined with the facts
table for unstructured information.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
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

router = APIRouter(prefix="/context", tags=["context"])


def _facts_for(session: Session, entity_type: str, entity_id: str) -> list[Fact]:
    return session.exec(
        select(Fact)
        .where(Fact.entity_type == entity_type)
        .where(Fact.entity_id == entity_id)
        .where(Fact.status == "active")
        .order_by(Fact.extracted_at.desc())
    ).all()


@router.get("/property/{property_id}")
def property_context(property_id: str, session: Session = Depends(get_session)):
    prop = session.get(Property, property_id)
    if prop is None:
        raise HTTPException(404, f"Property {property_id} not found")
    buildings = session.exec(
        select(Building).where(Building.property_id == property_id)
    ).all()
    units = session.exec(select(Unit).where(Unit.property_id == property_id)).all()
    return {
        "property": prop,
        "buildings": buildings,
        "units_count": len(units),
        "facts": _facts_for(session, "property", property_id),
    }


@router.get("/unit/{unit_id}")
def unit_context(unit_id: str, session: Session = Depends(get_session)):
    unit = session.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, f"Unit {unit_id} not found")
    owner = session.get(Owner, unit.owner_id) if unit.owner_id else None
    tenant = session.exec(
        select(Tenant).where(Tenant.unit_id == unit_id).where(Tenant.lease_end == None)  # noqa: E711
    ).first()
    return {
        "unit": unit,
        "owner": owner,
        "current_tenant": tenant,
        "facts": _facts_for(session, "unit", unit_id),
    }


@router.get("/owner/{owner_id}")
def owner_context(owner_id: str, session: Session = Depends(get_session)):
    owner = session.get(Owner, owner_id)
    if owner is None:
        raise HTTPException(404, f"Owner {owner_id} not found")
    units = session.exec(select(Unit).where(Unit.owner_id == owner_id)).all()
    return {
        "owner": owner,
        "units": units,
        "facts": _facts_for(session, "owner", owner_id),
    }


@router.get("/tenant/{tenant_id}")
def tenant_context(tenant_id: str, session: Session = Depends(get_session)):
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, f"Tenant {tenant_id} not found")
    unit = session.get(Unit, tenant.unit_id) if tenant.unit_id else None
    landlord = session.get(Owner, tenant.landlord_owner_id) if tenant.landlord_owner_id else None
    return {
        "tenant": tenant,
        "unit": unit,
        "landlord": landlord,
        "facts": _facts_for(session, "tenant", tenant_id),
    }


@router.get("/provider/{provider_id}")
def provider_context(provider_id: str, session: Session = Depends(get_session)):
    provider = session.get(ServiceProvider, provider_id)
    if provider is None:
        raise HTTPException(404, f"ServiceProvider {provider_id} not found")
    recent_invoices = session.exec(
        select(Invoice)
        .where(Invoice.provider_id == provider_id)
        .order_by(Invoice.invoice_date.desc())
        .limit(20)
    ).all()
    return {
        "provider": provider,
        "recent_invoices": recent_invoices,
        "facts": _facts_for(session, "service_provider", provider_id),
    }


@router.get("/source/{event_id}")
def source_event_context(event_id: str, session: Session = Depends(get_session)):
    event = session.get(SourceEvent, event_id)
    if event is None:
        raise HTTPException(404, f"SourceEvent {event_id} not found")
    derived_facts = session.exec(
        select(Fact).where(Fact.source_event_id == event_id)
    ).all()
    return {"source_event": event, "derived_facts": derived_facts}

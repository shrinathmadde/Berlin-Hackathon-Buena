from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db
from app.models import (
    BankTransaction,
    Building,
    Invoice,
    Owner,
    Property,
    ServiceProvider,
    SourceEvent,
    Tenant,
    Unit,
)
from app.routers import context, facts
from app.routers.crud import make_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Buena Context Engine",
    description="Per-property context store. Structured tables for clean entities, "
    "facts table for the flexible layer, source_events for provenance.",
    version="0.1.0",
    lifespan=lifespan,
)


# Plain CRUD for every structured entity.
app.include_router(make_router(Property, "properties", "property_id", "properties"))
app.include_router(make_router(Building, "buildings", "building_id", "buildings"))
app.include_router(make_router(Unit, "units", "unit_id", "units"))
app.include_router(make_router(Owner, "owners", "owner_id", "owners"))
app.include_router(make_router(Tenant, "tenants", "tenant_id", "tenants"))
app.include_router(make_router(ServiceProvider, "providers", "provider_id", "service_providers"))
app.include_router(make_router(BankTransaction, "transactions", "transaction_id", "bank_transactions"))
app.include_router(make_router(Invoice, "invoices", "invoice_id", "invoices"))
app.include_router(make_router(SourceEvent, "events", "event_id", "source_events"))

# Custom routers for the flexible layer + aggregate context queries.
app.include_router(facts.router)
app.include_router(context.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}

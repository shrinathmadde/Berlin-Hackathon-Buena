from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Load .env before any module reads os.environ (factory caches LLM_*).
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from app.routers import context, facts, llm, scan
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

# Open CORS — the Lovable preview lives on a different origin and needs to call /api/llm.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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

# LLM endpoint consumed by the frontend (placeholder for now).
app.include_router(llm.router)

# Server-side data-folder scan that diffs mtimes and routes new files through the LLM.
app.include_router(scan.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}

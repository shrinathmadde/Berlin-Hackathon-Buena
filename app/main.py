from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Load .env before any module reads os.environ (factory caches LLM_*).
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routers import llm


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="LLM SQL Runner",
    description="Generate one SQL query from a natural-language request and execute it directly.",
    version="0.1.0",
    lifespan=lifespan,
)

# Open CORS so the frontend can call the API from a different origin during local dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single SQL route.
app.include_router(llm.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}

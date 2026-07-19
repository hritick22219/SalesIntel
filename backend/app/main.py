from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine, Base
from redis_client import redis_client

# Import routers
from auth.router import router as auth_router
from ingestion.router import router as ingest_router
from analytics.router import router as analytics_router
from report.router import router as report_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Automatically create tables in database (if using local SQLite or PostgreSQL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Cleanup Redis connection on shutdown
    await redis_client.close()

app = FastAPI(
    title="Sales Intelligence API",
    description="Unified API endpoints for Auth, Ingestion, Analytics, and Reports",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers with prefixes
app.include_router(auth_router, prefix="/auth")
app.include_router(ingest_router, prefix="/ingest")
app.include_router(analytics_router, prefix="/analytics")
app.include_router(report_router, prefix="/report")

@app.get("/")
async def root():
    return {"message": "Welcome to the Sales Intelligence API portal."}

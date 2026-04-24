"""
Brand Protection Scanner — FastAPI application entry point.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import admin, brands, dashboard, monitor, results, scan, sweep, workers
from core.config import settings
from core.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Brand Protection Scanner",
    description="Real-time brand abuse & phishing detection engine.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for screenshots + dashboard
app.mount("/screenshots", StaticFiles(directory=settings.screenshot_dir, check_dir=False), name="screenshots")
app.mount("/static", StaticFiles(directory="dashboard/static", check_dir=False), name="static")

# Routers
app.include_router(brands.router)
app.include_router(scan.router)
app.include_router(sweep.router)
app.include_router(monitor.router)
app.include_router(results.router)
app.include_router(workers.router)
app.include_router(admin.router)
app.include_router(dashboard.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}


@app.get("/", tags=["meta"])
async def root():
    return {
        "service": "Brand Protection Scanner",
        "docs": "/docs",
        "health": "/health",
    }

"""Application entry point. Product integrations are implemented in separate tasks."""

from fastapi import FastAPI

from app.api.health import router as health_router

app = FastAPI(title="Company Memory API", version="0.1.0")
app.include_router(health_router, prefix="/api")

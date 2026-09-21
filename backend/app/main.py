"""Application entry point. Product integrations are implemented in separate tasks."""

from fastapi import FastAPI

from app.api.cases import router as cases_router
from app.api.error_handlers import register_error_handlers
from app.api.health import router as health_router

app = FastAPI(title="Company Memory API", version="0.1.0")
app.include_router(health_router, prefix="/api")
app.include_router(cases_router, prefix="/api")
register_error_handlers(app)

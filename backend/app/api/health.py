"""Process liveness only: this endpoint does not assert database or provider readiness."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["company-memory-api"] = "company-memory-api"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()

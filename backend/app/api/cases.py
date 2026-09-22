"""HTTP layer for the case contract (Issue #2).

Business rules live in `app.cases.service`; this module only translates HTTP
requests to service calls and picks status codes. Error bodies are produced
centrally by `app.api.error_handlers` from `app.cases.errors` exceptions.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import get_actor, get_case_service
from app.cases.models import (
    Case,
    CaseConfirmRequest,
    CaseCreateRequest,
    CaseHistoryResponse,
    CaseSearchResponse,
    CaseSearchResult,
    CaseUpdateRequest,
    ErrorResponse,
)
from app.cases.protocol import CaseServiceProtocol
from app.identity import Actor

router = APIRouter(tags=["cases"])

ServiceDep = Annotated[CaseServiceProtocol, Depends(get_case_service)]
ActorDep = Annotated[Actor, Depends(get_actor)]

# Every case route needs an accepted identity: the bot-only MVP has no public
# read surface, and Issue #10 forbids a client-declared user id.
UNAUTHORIZED = {status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}}


@router.post(
    "/cases",
    response_model=Case,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": Case,
            "description": "Replayed event: `source.id`/`external_event_id` already exists.",
        },
        **UNAUTHORIZED,
    },
    summary="Create a draft case from a source message",
)
def create_case(
    payload: CaseCreateRequest,
    response: Response,
    service: ServiceDep,
    actor: ActorDep,
) -> Case:
    case, created = service.create(payload)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return case


@router.get(
    "/cases",
    response_model=CaseSearchResponse,
    responses={**UNAUTHORIZED},
    summary="Search confirmed cases",
)
def search_cases(
    service: ServiceDep,
    actor: ActorDep,
    q: Annotated[
        str | None, Query(description="Free-text match over symptom/cause/action/result.")
    ] = None,
    equipment_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CaseSearchResponse:
    cases = service.search(q=q, equipment_id=equipment_id, limit=limit)
    return CaseSearchResponse(items=[CaseSearchResult(case=c) for c in cases], next_cursor=None)


@router.get(
    "/cases/{case_id}",
    response_model=Case,
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}, **UNAUTHORIZED},
    summary="Fetch a single case",
)
def get_case(case_id: str, service: ServiceDep, actor: ActorDep) -> Case:
    return service.get(case_id)


@router.patch(
    "/cases/{case_id}",
    response_model=Case,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        **UNAUTHORIZED,
    },
    summary="Correct a draft case",
)
def update_case(
    case_id: str, payload: CaseUpdateRequest, service: ServiceDep, actor: ActorDep
) -> Case:
    return service.update(case_id, payload, actor)


@router.post(
    "/cases/{case_id}/confirm",
    response_model=Case,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        **UNAUTHORIZED,
    },
    summary="Confirm a draft case",
)
def confirm_case(
    case_id: str,
    payload: CaseConfirmRequest,
    service: ServiceDep,
    actor: ActorDep,
) -> Case:
    return service.confirm(case_id, payload, actor)


@router.get(
    "/equipment/{equipment_id}/history",
    response_model=CaseHistoryResponse,
    responses={**UNAUTHORIZED},
    summary="Confirmed case history for one piece of equipment",
)
def equipment_history(
    equipment_id: str,
    service: ServiceDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CaseHistoryResponse:
    cases = service.history(equipment_id, limit=limit)
    return CaseHistoryResponse(equipment_id=equipment_id, items=cases, next_cursor=None)

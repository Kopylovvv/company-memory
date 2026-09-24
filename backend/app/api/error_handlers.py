"""Maps domain errors to the shared HTTP error contract.

See `app.cases.models.ErrorResponse` for the response body shape,
`app.cases.errors` for case errors and `app.identity` for identity ones.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.cases.errors import (
    CaseAlreadyConfirmedError,
    CaseError,
    CaseForbiddenError,
    CaseNotFoundError,
    InsufficientDataError,
)
from app.cases.models import ErrorDetail, ErrorResponse
from app.identity import IdentityNotVerifiedError

_STATUS_CODES: dict[type[Exception], int] = {
    CaseNotFoundError: status.HTTP_404_NOT_FOUND,
    CaseAlreadyConfirmedError: status.HTTP_409_CONFLICT,
    InsufficientDataError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CaseForbiddenError: status.HTTP_403_FORBIDDEN,
    IdentityNotVerifiedError: status.HTTP_401_UNAUTHORIZED,
}


def _handle_domain_error(
    request: Request, exc: CaseError | IdentityNotVerifiedError
) -> JSONResponse:
    status_code = _STATUS_CODES[type(exc)]
    body = ErrorResponse(error=ErrorDetail(code=exc.code, message=str(exc)))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def register_error_handlers(app: FastAPI) -> None:
    for error_type in _STATUS_CODES:
        app.add_exception_handler(error_type, _handle_domain_error)

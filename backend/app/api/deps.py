"""Shared FastAPI dependencies for the API layer."""

from fastapi import Header

from app.cases.service import CaseService

_case_service = CaseService()


def get_case_service() -> CaseService:
    return _case_service


def get_current_user_id(x_demo_user_id: str = Header(min_length=1)) -> str:
    """Stand-in for a server-verified user identity.

    MAX launch-data verification (Issue #10) is not implemented yet, so there
    is currently no server-side way to know who is confirming a case. Until
    then, callers must pass the confirming user id explicitly via this
    header. This is not an authentication boundary: it trusts whatever the
    client sends. Replace this dependency with one backed by verified MAX
    launch data before relying on `confirmed_by` for anything sensitive.
    """
    return x_demo_user_id

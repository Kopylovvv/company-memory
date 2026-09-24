"""Shared FastAPI dependencies for the API layer."""

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.cases.protocol import CaseServiceProtocol
from app.db.engine import get_session
from app.db.repository import DbCaseService
from app.identity import Actor, IdentityNotVerifiedError, demo_identity_allowed


def get_case_service(session: Session = Depends(get_session)) -> CaseServiceProtocol:
    return DbCaseService(session)


def get_actor(x_demo_user_id: str | None = Header(default=None)) -> Actor:
    """The user the server is willing to act for on this HTTP request.

    In the bot-only MVP the verified channel is the bot's own connection to
    MAX (see `app.identity`), not HTTP, so this dependency has nothing to
    verify and refuses by default. Setting `ALLOW_DEMO_IDENTITY` opens a
    development-only door that trusts `X-Demo-User-Id` as sent; production
    leaves it unset, which is what keeps the header out of a public run.
    """
    if not demo_identity_allowed() or not x_demo_user_id:
        raise IdentityNotVerifiedError()
    return Actor(user_id=x_demo_user_id, verified_via="demo_header")

"""Structural interface shared by every case store.

`app.cases.service.CaseService` (in-memory, Issue #2) and
`app.db.repository.DbCaseService` (Postgres, Issue #4) both satisfy this
protocol, so `app.api.deps.get_case_service` can hand either one to the HTTP
layer without it caring which is behind the call.
"""

from typing import Protocol

from app.cases.models import (
    Case,
    CaseConfirmRequest,
    CaseCreateRequest,
    CaseSearchResult,
    CaseUpdateRequest,
)
from app.identity import Actor


class CaseServiceProtocol(Protocol):
    def create(self, payload: CaseCreateRequest) -> tuple[Case, bool]: ...

    def get(self, case_id: str) -> Case: ...

    def latest_max_case(self, author_id: str) -> Case | None:
        """Most recent MAX case by this author, draft or confirmed, for commands without an ID.

        Deliberately not "the latest draft": once that draft is confirmed, the latest
        draft becomes an older one the author never reviewed, and a repeated or
        redelivered `/confirm` would confirm it.
        """
        ...

    def update(self, case_id: str, payload: CaseUpdateRequest, actor: Actor) -> Case: ...

    def confirm(self, case_id: str, payload: CaseConfirmRequest, actor: Actor) -> Case: ...

    def search(
        self, *, q: str | None, equipment_id: str | None, limit: int
    ) -> list[CaseSearchResult]:
        """Confirmed cases only; with `q`, ranked by `app.ai.search` and scored."""
        ...

    def history(self, equipment_id: str, *, limit: int) -> list[Case]: ...

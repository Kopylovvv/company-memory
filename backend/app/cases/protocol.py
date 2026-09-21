"""Structural interface shared by every case store.

`app.cases.service.CaseService` (in-memory, Issue #2) and
`app.db.repository.DbCaseService` (Postgres, Issue #4) both satisfy this
protocol, so `app.api.deps.get_case_service` can hand either one to the HTTP
layer without it caring which is behind the call.
"""

from typing import Protocol

from app.cases.models import Case, CaseConfirmRequest, CaseCreateRequest, CaseUpdateRequest


class CaseServiceProtocol(Protocol):
    def create(self, payload: CaseCreateRequest) -> tuple[Case, bool]: ...

    def get(self, case_id: str) -> Case: ...

    def update(self, case_id: str, payload: CaseUpdateRequest) -> Case: ...

    def confirm(self, case_id: str, payload: CaseConfirmRequest, user_id: str) -> Case: ...

    def search(self, *, q: str | None, equipment_id: str | None, limit: int) -> list[Case]: ...

    def history(self, equipment_id: str, *, limit: int) -> list[Case]: ...

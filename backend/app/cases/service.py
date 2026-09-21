"""Case business rules behind an in-memory store.

This is a stand-in for PostgreSQL persistence (Issue #4): enough to validate
the API contract end-to-end and to demo the chain, but not durable and not
safe for concurrent writers. A database-backed implementation should keep the
same method signatures and the same request/response shapes from
`app.cases.models`.
"""

import uuid
from datetime import UTC, datetime

from app.cases.errors import CaseAlreadyConfirmedError, CaseNotFoundError, InsufficientDataError
from app.cases.models import (
    Case,
    CaseConfirmRequest,
    CaseCreateRequest,
    CaseUpdateRequest,
    Participant,
    ParticipantInput,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _resolve_participant(participant: ParticipantInput | None) -> Participant | None:
    if participant is None:
        return None
    participant_id = participant.id or f"participant-{uuid.uuid4().hex[:8]}"
    return Participant(id=participant_id, display_name=participant.display_name)


class CaseService:
    """Owns case lifecycle rules: create, correct, confirm, search, history."""

    def __init__(self) -> None:
        self._cases: dict[str, Case] = {}
        self._case_id_by_source_id: dict[str, str] = {}
        self._case_id_by_event_id: dict[str, str] = {}

    def create(self, payload: CaseCreateRequest) -> tuple[Case, bool]:
        """Create a draft. Returns `(case, created)`; `created` is False on a replayed event."""
        existing_id = None
        if payload.source.external_event_id:
            existing_id = self._case_id_by_event_id.get(payload.source.external_event_id)
        if existing_id is None:
            existing_id = self._case_id_by_source_id.get(payload.source.id)
        if existing_id is not None:
            return self._cases[existing_id], False

        now = _now()
        case_id = f"case-{uuid.uuid4().hex[:12]}"
        case = Case(
            id=case_id,
            status="draft",
            source=payload.source,
            equipment=payload.equipment,
            symptom=payload.symptom,
            cause=payload.cause,
            action=payload.action,
            result=payload.result,
            participant=_resolve_participant(payload.participant),
            confirmed_by=None,
            confirmed_at=None,
            created_at=now,
            updated_at=now,
            is_demo=payload.is_demo,
        )
        self._cases[case_id] = case
        self._case_id_by_source_id[payload.source.id] = case_id
        if payload.source.external_event_id:
            self._case_id_by_event_id[payload.source.external_event_id] = case_id
        return case, True

    def get(self, case_id: str) -> Case:
        try:
            return self._cases[case_id]
        except KeyError as exc:
            raise CaseNotFoundError(case_id) from exc

    def update(self, case_id: str, payload: CaseUpdateRequest) -> Case:
        case = self.get(case_id)
        if case.status == "confirmed":
            raise CaseAlreadyConfirmedError(case_id)
        updated = self._apply(case, payload)
        self._cases[case_id] = updated
        return updated

    def confirm(self, case_id: str, payload: CaseConfirmRequest, user_id: str) -> Case:
        case = self.get(case_id)
        if case.status == "confirmed":
            raise CaseAlreadyConfirmedError(case_id)
        candidate = self._apply(case, payload)
        if candidate.equipment is None or not any(
            (candidate.symptom, candidate.action, candidate.result)
        ):
            raise InsufficientDataError(case_id)
        now = _now()
        confirmed = candidate.model_copy(
            update={
                "status": "confirmed",
                "confirmed_by": user_id,
                "confirmed_at": now,
                "updated_at": now,
            }
        )
        self._cases[case_id] = confirmed
        return confirmed

    def search(self, *, q: str | None, equipment_id: str | None, limit: int) -> list[Case]:
        results = [c for c in self._cases.values() if c.status == "confirmed"]
        if equipment_id:
            results = [c for c in results if c.equipment and c.equipment.id == equipment_id]
        if q:
            needle = q.lower()
            results = [c for c in results if needle in self._searchable_text(c)]
        results.sort(key=lambda c: c.confirmed_at or c.updated_at, reverse=True)
        return results[:limit]

    def history(self, equipment_id: str, *, limit: int) -> list[Case]:
        results = [
            c
            for c in self._cases.values()
            if c.status == "confirmed" and c.equipment and c.equipment.id == equipment_id
        ]
        results.sort(key=lambda c: c.confirmed_at or c.updated_at, reverse=True)
        return results[:limit]

    @staticmethod
    def _searchable_text(case: Case) -> str:
        return " ".join(filter(None, [case.symptom, case.cause, case.action, case.result])).lower()

    @staticmethod
    def _apply(case: Case, payload: CaseUpdateRequest) -> Case:
        changes: dict[str, object] = {}
        for field in payload.model_fields_set:
            value = getattr(payload, field)
            if field == "participant":
                value = _resolve_participant(value)
            changes[field] = value
        changes["updated_at"] = _now()
        return case.model_copy(update=changes)

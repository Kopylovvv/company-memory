"""Postgres-backed case repository behind the same interface as `app.cases.service.CaseService`.

Issue #4 replaces the in-memory store for production use once migrations are
applied. The in-memory `CaseService` (Issue #2) stays as-is for fast, DB-free
contract tests; this module is exercised by `tests/test_db_case_repository.py`
against a real Postgres.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cases.errors import CaseAlreadyConfirmedError, CaseNotFoundError, InsufficientDataError
from app.cases.models import (
    Case,
    CaseConfirmRequest,
    CaseCreateRequest,
    CaseUpdateRequest,
    Equipment,
    Participant,
    ParticipantInput,
    Source,
)
from app.cases.permissions import ensure_may_modify
from app.db.models import CaseRow
from app.identity import Actor


def _now() -> datetime:
    return datetime.now(UTC)


def _resolve_participant_id(participant: ParticipantInput | None) -> str | None:
    if participant is None:
        return None
    return participant.id or f"participant-{uuid.uuid4().hex[:8]}"


def _row_to_case(row: CaseRow) -> Case:
    equipment = (
        Equipment(id=row.equipment_id, label=row.equipment_label)
        if row.equipment_id is not None
        else None
    )
    participant = (
        Participant(id=row.participant_id, display_name=row.participant_display_name)
        if row.participant_id is not None
        else None
    )
    return Case(
        id=row.id,
        status=row.status,
        source=Source(
            id=row.source_id,
            type=row.source_type,
            text=row.source_text,
            author_id=row.source_author_id,
            received_at=row.source_received_at,
            external_event_id=row.source_external_event_id,
        ),
        equipment=equipment,
        symptom=row.symptom,
        cause=row.cause,
        action=row.action,
        result=row.result,
        participant=participant,
        confirmed_by=row.confirmed_by,
        confirmed_at=row.confirmed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        is_demo=row.is_demo,
    )


class DbCaseService:
    """Same public interface as `CaseService`, backed by a Postgres session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, payload: CaseCreateRequest) -> tuple[Case, bool]:
        existing = self._find_existing(payload.source.id, payload.source.external_event_id)
        if existing is not None:
            return _row_to_case(existing), False

        now = _now()
        row = CaseRow(
            id=f"case-{uuid.uuid4().hex[:12]}",
            status="draft",
            source_id=payload.source.id,
            source_type=payload.source.type,
            source_text=payload.source.text,
            source_author_id=payload.source.author_id,
            source_received_at=payload.source.received_at,
            source_external_event_id=payload.source.external_event_id,
            equipment_id=payload.equipment.id if payload.equipment else None,
            equipment_label=payload.equipment.label if payload.equipment else None,
            symptom=payload.symptom,
            cause=payload.cause,
            action=payload.action,
            result=payload.result,
            participant_id=_resolve_participant_id(payload.participant),
            participant_display_name=(
                payload.participant.display_name if payload.participant else None
            ),
            confirmed_by=None,
            confirmed_at=None,
            created_at=now,
            updated_at=now,
            is_demo=payload.is_demo,
        )
        self._session.add(row)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            existing = self._find_existing(payload.source.id, payload.source.external_event_id)
            if existing is None:
                raise
            return _row_to_case(existing), False
        return _row_to_case(row), True

    def get(self, case_id: str) -> Case:
        return _row_to_case(self._get_row(case_id))

    def update(self, case_id: str, payload: CaseUpdateRequest, actor: Actor) -> Case:
        row = self._get_row(case_id)
        ensure_may_modify(actor, case_id=case_id, source_author_id=row.source_author_id)
        if row.status == "confirmed":
            raise CaseAlreadyConfirmedError(case_id)
        self._apply(row, payload)
        self._session.commit()
        return _row_to_case(row)

    def confirm(self, case_id: str, payload: CaseConfirmRequest, actor: Actor) -> Case:
        row = self._get_row(case_id)
        ensure_may_modify(actor, case_id=case_id, source_author_id=row.source_author_id)
        if row.status == "confirmed":
            raise CaseAlreadyConfirmedError(case_id)
        self._apply(row, payload)
        if row.equipment_id is None or not any((row.symptom, row.action, row.result)):
            self._session.rollback()
            raise InsufficientDataError(case_id)
        now = _now()
        row.status = "confirmed"
        row.confirmed_by = actor.user_id
        row.confirmed_at = now
        row.updated_at = now
        self._session.commit()
        return _row_to_case(row)

    def search(self, *, q: str | None, equipment_id: str | None, limit: int) -> list[Case]:
        stmt = select(CaseRow).where(CaseRow.status == "confirmed")
        if equipment_id:
            stmt = stmt.where(CaseRow.equipment_id == equipment_id)
        stmt = stmt.order_by(CaseRow.confirmed_at.desc().nullslast(), CaseRow.updated_at.desc())
        if q is None:
            stmt = stmt.limit(limit)
        rows = self._session.execute(stmt).scalars().all()
        if q:
            needle = q.lower()
            rows = [r for r in rows if needle in self._searchable_text(r)][:limit]
        return [_row_to_case(r) for r in rows]

    def history(self, equipment_id: str, *, limit: int) -> list[Case]:
        stmt = (
            select(CaseRow)
            .where(CaseRow.status == "confirmed", CaseRow.equipment_id == equipment_id)
            .order_by(CaseRow.confirmed_at.desc().nullslast(), CaseRow.updated_at.desc())
            .limit(limit)
        )
        rows = self._session.execute(stmt).scalars().all()
        return [_row_to_case(r) for r in rows]

    def _find_existing(self, source_id: str, external_event_id: str | None) -> CaseRow | None:
        if external_event_id:
            row = self._session.execute(
                select(CaseRow).where(CaseRow.source_external_event_id == external_event_id)
            ).scalar_one_or_none()
            if row is not None:
                return row
        return self._session.execute(
            select(CaseRow).where(CaseRow.source_id == source_id)
        ).scalar_one_or_none()

    def _get_row(self, case_id: str) -> CaseRow:
        row = self._session.get(CaseRow, case_id)
        if row is None:
            raise CaseNotFoundError(case_id)
        return row

    @staticmethod
    def _searchable_text(row: CaseRow) -> str:
        return " ".join(filter(None, [row.symptom, row.cause, row.action, row.result])).lower()

    @staticmethod
    def _apply(row: CaseRow, payload: CaseUpdateRequest) -> None:
        for field in payload.model_fields_set:
            value = getattr(payload, field)
            if field == "equipment":
                row.equipment_id = value.id if value else None
                row.equipment_label = value.label if value else None
            elif field == "participant":
                row.participant_id = _resolve_participant_id(value)
                row.participant_display_name = value.display_name if value else None
            else:
                setattr(row, field, value)
        row.updated_at = _now()

"""Integration tests for the Postgres-backed case repository (Issue #4).

Needs a reachable Postgres, matching the `POSTGRES_*` env vars in
`app/db/settings.py`, with the schema from `migrations/` applied. Locally:

    docker compose up -d db
    uv run alembic upgrade head
    uv run pytest tests/test_db_case_repository.py

This file skips itself automatically if that database isn't reachable. CI
provisions Postgres and runs migrations before pytest, so this suite is not
skipped there (see .github/workflows/ci.yml) — that is what actually proves
the migration works, not this file alone.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.bot.handler import MaxUpdateHandler
from app.bot.models import MaxUpdate
from app.cases.models import CaseConfirmRequest, CaseCreateRequest, Equipment, Source
from app.db.engine import engine
from app.db.repository import DbCaseService


def _database_reachable() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM cases LIMIT 1"))
        return True
    except SQLAlchemyError:
        return False


pytestmark = pytest.mark.skipif(
    not _database_reachable(),
    reason=(
        "Postgres with migrations applied is not reachable; run "
        "'docker compose up -d db' and 'uv run alembic upgrade head' first."
    ),
)


@pytest.fixture
def db_session():
    with Session(engine) as session:
        session.execute(text("TRUNCATE TABLE cases"))
        session.commit()
        yield session


@pytest.fixture
def service(db_session):
    return DbCaseService(db_session)


def _create_request(**overrides):
    fields = {
        "source": Source(
            id="demo-message-001",
            type="max_message",
            text="На Н-204 выросла вибрация.",
            author_id="demo-user-001",
            received_at="2026-09-21T09:00:00Z",
            external_event_id="max-evt-001",
        ),
        "equipment": Equipment(id="eq-204"),
        "symptom": "Повышенная вибрация",
        "action": "Подтянули крепление муфты",
        "result": "Вибрация исчезла",
    }
    fields.update(overrides)
    return CaseCreateRequest(**fields)


def test_create_persists_a_draft(service):
    case, created = service.create(_create_request())
    assert created is True
    assert case.status == "draft"
    assert case.equipment.id == "eq-204"
    assert case.cause is None


def test_create_is_idempotent_on_replay(service):
    first, _ = service.create(_create_request())
    second, created_again = service.create(_create_request())
    assert created_again is False
    assert first.id == second.id


def test_confirm_persists_across_sessions(service, db_session):
    case, _ = service.create(_create_request())
    service.confirm(case.id, CaseConfirmRequest(), "andrey")

    reloaded = DbCaseService(db_session).get(case.id)
    assert reloaded.status == "confirmed"
    assert reloaded.confirmed_by == "andrey"
    assert reloaded.confirmed_at is not None


def test_search_and_history_return_confirmed_cases_for_equipment(service):
    case, _ = service.create(_create_request())
    service.confirm(case.id, CaseConfirmRequest(), "andrey")

    results = service.search(q=None, equipment_id="eq-204", limit=20)
    assert any(c.id == case.id for c in results)

    history = service.history("eq-204", limit=20)
    assert any(c.id == case.id for c in history)


def test_search_excludes_draft_cases(service):
    case, _ = service.create(_create_request())

    results = service.search(q=None, equipment_id="eq-204", limit=20)
    assert not any(c.id == case.id for c in results)


def test_bot_update_is_persisted_and_survives_redelivery(service, db_session):
    """Issue #5 through Postgres: the same MAX message must not create a second case."""
    update = MaxUpdate.model_validate(
        {
            "update_type": "message_created",
            "timestamp": 1790000000000,
            "message": {
                "body": {"mid": "mid.bot.integration", "text": "Течь сальника на Н-204"},
                "recipient": {"chat_id": 501, "user_id": 400790839, "chat_type": "dialog"},
                "timestamp": 1790000000000,
                "sender": {"user_id": 4242, "first_name": "Иван", "is_bot": False},
            },
        }
    )
    handler = MaxUpdateHandler(service)

    first = handler.handle(update)
    second = MaxUpdateHandler(DbCaseService(db_session)).handle(update)

    assert first.created is True
    assert second.created is False
    assert first.case.id == second.case.id
    assert second.has_reply is False

    stored = DbCaseService(db_session).get(first.case.id)
    assert stored.source.id == "mid.bot.integration"
    assert stored.source.author_id == "4242"
    assert stored.status == "draft"

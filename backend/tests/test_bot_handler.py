"""Issue #5: a MAX text message becomes a stored draft case and gets acknowledged."""

import logging
from datetime import UTC, datetime

from app.ai.extraction import DraftExtraction, ExtractionOutcome
from app.bot import polling
from app.bot.ai import extract_message
from app.bot.handler import (
    AI_UNAVAILABLE_NOTICE,
    TEXT_ONLY_NOTICE,
    MaxUpdateHandler,
    PendingCommandStore,
)
from app.bot.models import MaxUpdate, MaxUpdateList
from app.cases.models import (
    CaseConfirmRequest,
    CaseCreateRequest,
    CaseUpdateRequest,
    Equipment,
    Source,
)
from app.cases.service import CaseService
from app.identity import actor_from_max_event

MID = "mid.demo.0001"


def _update(**message_overrides):
    message = {
        "body": {"mid": MID, "seq": 1, "text": "На Н-204 выросла вибрация. Подтянули муфту."},
        "recipient": {"chat_id": 777, "user_id": 400790839, "chat_type": "dialog"},
        "timestamp": 1790000000000,
        "sender": {"user_id": 12345, "first_name": "Иван", "is_bot": False},
    }
    message.update(message_overrides)
    return MaxUpdate.model_validate(
        {"update_type": "message_created", "timestamp": 1790000000000, "message": message}
    )


def _handler():
    service = CaseService()
    return MaxUpdateHandler(service), service


def test_text_message_is_stored_as_a_draft_with_source_fields():
    handler, _ = _handler()

    outcome = handler.handle(_update())

    assert outcome.created is True
    case = outcome.case
    assert case is not None
    assert case.status == "draft"
    assert case.source.id == MID
    assert case.source.type == "max_message"
    assert case.source.author_id == "12345"
    assert case.source.external_event_id == MID
    assert case.source.received_at.isoformat() == "2026-09-21T14:13:20+00:00"
    # Nothing is extracted yet: the AI draft is Issue #6.
    assert case.symptom is None
    assert case.cause is None
    assert case.participant is None


def test_sender_gets_an_acknowledgement_without_copying_the_case_id():
    handler, _ = _handler()

    outcome = handler.handle(_update())

    assert outcome.has_reply
    assert outcome.case is not None
    assert outcome.case.id not in outcome.reply_text
    assert "/confirm" in outcome.reply_text
    assert outcome.reply_chat_id == 777


def test_redelivered_update_creates_no_duplicate_and_retries_acknowledgement():
    handler, service = _handler()

    first = handler.handle(_update())
    second = handler.handle(_update())

    assert first.created is True
    assert second.created is False
    assert second.case is not None
    assert first.case is not None
    assert first.case.id == second.case.id
    assert second.has_reply is True
    assert second.reply_text == first.reply_text
    # The stored case is still the first one, untouched and still a draft.
    assert service.get(first.case.id).status == "draft"


def test_message_without_text_is_not_stored_but_is_answered():
    handler, _ = _handler()

    outcome = handler.handle(_update(body={"mid": MID, "text": None}))

    assert outcome.case is None
    assert outcome.reply_text == TEXT_ONLY_NOTICE


def test_bot_messages_and_other_update_types_are_ignored():
    handler, _ = _handler()

    from_bot = handler.handle(
        _update(sender={"user_id": 400790839, "first_name": "bot", "is_bot": True})
    )
    other_type = handler.handle(
        MaxUpdate.model_validate({"update_type": "bot_started", "timestamp": 1790000000000})
    )

    assert from_bot.case is None and from_bot.has_reply is False
    assert other_type.case is None and other_type.has_reply is False


def test_bot_started_greets_the_user_with_command_help():
    handler, _ = _handler()
    started = MaxUpdate.model_validate(
        {
            "update_type": "bot_started",
            "timestamp": 1790000000000,
            "chat_id": 777,
            "user": {"user_id": 12345, "first_name": "Иван", "is_bot": False},
        }
    )

    outcome = handler.handle(started)

    assert "/search" in outcome.reply_text
    assert "/confirm" in outcome.reply_text
    assert outcome.reply_chat_id == 777
    assert outcome.case is None


def test_tapped_search_command_accepts_query_in_next_message_without_creating_draft():
    handler, service = _handler()
    created = handler.handle(_update()).case
    assert created is not None
    author = actor_from_max_event(12345)
    service.update(
        created.id,
        CaseUpdateRequest(equipment=Equipment(id="Н-204"), symptom="вибрация насоса"),
        author,
    )
    service.confirm(created.id, CaseConfirmRequest(), author)

    prompt = handler.handle(_update(body={"mid": "search.prompt", "text": "/search"}))
    query = _update(body={"mid": "search.query", "text": "вибрация насоса"})
    found = handler.handle(query)
    redelivered = handler.handle(query)

    assert "следующим сообщением" in prompt.reply_text.lower()
    assert "Н-204" in found.reply_text
    assert found.reply_text == redelivered.reply_text
    assert found.case is None and redelivered.case is None
    assert len(service._cases) == 1


def test_tapped_edit_command_accepts_field_and_value_in_next_message():
    handler, service = _handler()
    created = handler.handle(_update()).case
    assert created is not None

    prompt = handler.handle(_update(body={"mid": "edit.prompt", "text": "/edit"}))
    answer = _update(body={"mid": "edit.answer", "text": "результат вибрация исчезла"})
    edited = handler.handle(answer)
    redelivered = handler.handle(answer)

    assert "следующим сообщением" in prompt.reply_text.lower()
    assert "вибрация исчезла" in edited.reply_text
    assert service.get(created.id).result == "вибрация исчезла"
    assert edited.reply_text == redelivered.reply_text


def test_tapped_edit_keeps_waiting_after_incomplete_answer():
    handler, service = _handler()
    created = handler.handle(_update()).case
    assert created is not None
    handler.handle(_update(body={"mid": "edit.prompt", "text": "/edit"}))

    incomplete = handler.handle(_update(body={"mid": "edit.incomplete", "text": "результат"}))
    complete = handler.handle(
        _update(body={"mid": "edit.complete", "text": "результат течь устранена"})
    )

    assert "поле и новое значение" in incomplete.reply_text
    assert incomplete.case is None
    assert "Черновик обновлён" in complete.reply_text
    assert service.get(created.id).result == "течь устранена"


def test_pending_command_is_scoped_to_author_and_new_command_cancels_it():
    handler, service = _handler()
    handler.handle(_update(body={"mid": "search.prompt", "text": "/search"}))

    other_user = handler.handle(
        _update(
            sender={"user_id": 67890, "first_name": "Петр", "is_bot": False},
            body={"mid": "other.description", "text": "На насосе появилась течь"},
        )
    )
    assert other_user.created is True

    handler.handle(_update(body={"mid": "help.cancel", "text": "/help"}))
    own_description = handler.handle(
        _update(body={"mid": "own.description", "text": "На насосе появилась вибрация"})
    )
    assert own_description.created is True
    assert len(service._cases) == 2


def test_pending_command_survives_new_handler_for_next_polled_update():
    service = CaseService()
    pending = PendingCommandStore()
    first_handler = MaxUpdateHandler(service, pending_commands=pending)
    next_handler = MaxUpdateHandler(service, pending_commands=pending)

    first_handler.handle(_update(body={"mid": "search.prompt", "text": "/search"}))
    answer = next_handler.handle(
        _update(body={"mid": "search.answer", "text": "нет такого случая"})
    )

    assert "пока нет" in answer.reply_text
    assert answer.case is None
    assert len(service._cases) == 0


def test_cancel_stops_waiting_for_search_or_edit_answer():
    handler, service = _handler()
    handler.handle(_update(body={"mid": "search.prompt", "text": "/search"}))
    cancelled = handler.handle(_update(body={"mid": "search.cancel", "text": "/cancel"}))
    description = handler.handle(
        _update(body={"mid": "repair.after.cancel", "text": "На насосе появилась течь"})
    )
    assert "Действие отменено" in cancelled.reply_text
    assert description.created is True

    handler.handle(_update(body={"mid": "edit.prompt", "text": "/edit"}))
    cancelled = handler.handle(_update(body={"mid": "edit.cancel", "text": "/cancel"}))
    no_action = handler.handle(_update(body={"mid": "cancel.again", "text": "/cancel"}))
    assert "Действие отменено" in cancelled.reply_text
    assert "нечего отменять" in no_action.reply_text
    assert len(service._cases) == 1


def test_message_created_without_a_message_body_is_ignored():
    # The platform is known to deliver such updates for some native voice messages.
    handler, _ = _handler()

    result = handler.handle(
        MaxUpdate.model_validate({"update_type": "message_created", "timestamp": 1790000000000})
    )

    assert result.case is None
    assert result.has_reply is False


def test_reply_goes_to_the_sender_when_there_is_no_chat_id():
    handler, _ = _handler()

    outcome = handler.handle(
        _update(recipient={"chat_id": None, "user_id": 400790839, "chat_type": "dialog"})
    )

    assert outcome.reply_user_id == 12345
    assert outcome.reply_chat_id is None


def test_author_can_correct_confirm_and_find_case_in_bot():
    handler, service = _handler()
    created = handler.handle(_update())
    case_id = created.case.id

    edited = handler.handle(
        _update(body={"mid": "edit.1", "text": f"/edit {case_id} оборудование Н-204"})
    )
    assert "Н-204" in edited.reply_text
    assert service.get(case_id).equipment.id == "Н-204"

    edited = handler.handle(
        _update(body={"mid": "edit.2", "text": f"/edit {case_id} симптом сильная вибрация"})
    )
    assert "сильная вибрация" in edited.reply_text

    confirmed = handler.handle(_update(body={"mid": "confirm.1", "text": f"/confirm {case_id}"}))
    assert "подтверждён" in confirmed.reply_text
    assert service.get(case_id).confirmed_by == "12345"

    found = handler.handle(_update(body={"mid": "search.1", "text": "/search вибрация"}))
    assert "Н-204" in found.reply_text
    assert "Симптом: сильная вибрация" in found.reply_text
    assert "Источник:" in found.reply_text


def test_commands_without_id_use_only_the_authors_latest_case():
    handler, service = _handler()
    first = handler.handle(_update(body={"mid": "draft.1", "text": "Первый ремонт"})).case
    second = handler.handle(_update(body={"mid": "draft.2", "text": "Второй ремонт"})).case
    assert first is not None and second is not None

    edited = handler.handle(
        _update(body={"mid": "edit.latest", "text": "/edit оборудование Н-777"})
    )
    handler.handle(_update(body={"mid": "edit.latest.2", "text": "/edit результат течь устранена"}))
    assert "Н-777" in edited.reply_text
    assert service.get(first.id).equipment is None
    assert service.get(second.id).equipment.id == "Н-777"

    confirmed = handler.handle(_update(body={"mid": "confirm.latest", "text": "/confirm"}))
    assert "подтверждён" in confirmed.reply_text
    assert service.get(second.id).status == "confirmed"
    assert service.get(first.id).status == "draft"

    no_draft = handler.handle(
        _update(
            sender={"user_id": 67890, "first_name": "Петр", "is_bot": False},
            body={"mid": "confirm.other", "text": "/confirm"},
        )
    )
    assert "нет черновика" in no_draft.reply_text


def test_repeated_confirm_never_confirms_an_older_unreviewed_draft():
    """A double tap or a redelivered update must not confirm a draft nobody reviewed.

    With AI on, every draft already has equipment and a symptom, so "enough facts"
    does not stop it: only targeting the author's latest case, whatever its status,
    does.
    """

    def ai(text):
        equipment = "Н-101" if "Н-101" in text else "Н-204"
        return ExtractionOutcome(
            draft=DraftExtraction(equipment=Equipment(id=equipment), symptom=text)
        )

    service = CaseService()
    handler = MaxUpdateHandler(service, draft_extractor=ai)
    older = handler.handle(_update(body={"mid": "draft.old", "text": "Течь на Н-101"})).case
    latest = handler.handle(_update(body={"mid": "draft.new", "text": "Вибрация на Н-204"})).case

    confirm = _update(body={"mid": "confirm.once", "text": "/confirm"})
    first = handler.handle(confirm)
    redelivered = handler.handle(confirm)
    tapped_again = handler.handle(_update(body={"mid": "confirm.twice", "text": "/confirm"}))

    assert "подтверждён и доступен" in first.reply_text
    assert "уже подтверждён" in redelivered.reply_text
    assert "уже подтверждён" in tapped_again.reply_text
    assert service.get(latest.id).status == "confirmed"
    assert service.get(older.id).status == "draft"

    # /edit without an ID must not silently move on to the older draft either.
    edited = handler.handle(_update(body={"mid": "edit.after", "text": "/edit причина износ"}))
    assert "уже подтверждён" in edited.reply_text
    assert service.get(older.id).cause is None


def test_search_result_includes_action_and_outcome():
    handler, service = _handler()
    created = handler.handle(_update()).case
    assert created is not None
    author = actor_from_max_event(12345)
    service.update(
        created.id,
        CaseUpdateRequest(
            equipment=Equipment(id="Н-204"),
            symptom="вибрация насоса",
            action="подтянули муфту",
            result="вибрация исчезла",
        ),
        author,
    )
    service.confirm(created.id, CaseConfirmRequest(), author)

    found = handler.handle(_update(body={"mid": "search.details", "text": "/search вибрация"}))

    assert "Что сделали: подтянули муфту" in found.reply_text
    assert "Результат: вибрация исчезла" in found.reply_text


def test_other_sender_cannot_change_or_confirm_draft():
    handler, service = _handler()
    case_id = handler.handle(_update()).case.id
    other = {"user_id": 67890, "first_name": "Петр", "is_bot": False}

    edited = handler.handle(
        _update(sender=other, body={"mid": "edit.3", "text": f"/edit {case_id} оборудование Н-204"})
    )
    confirmed = handler.handle(
        _update(sender=other, body={"mid": "confirm.2", "text": f"/confirm {case_id}"})
    )

    assert "только автор" in edited.reply_text
    assert "только автор" in confirmed.reply_text
    assert service.get(case_id).status == "draft"


def test_confirmation_requires_enough_facts_and_commands_do_not_create_cases():
    handler, service = _handler()
    case_id = handler.handle(_update()).case.id

    incomplete = handler.handle(_update(body={"mid": "confirm.3", "text": f"/confirm {case_id}"}))
    malformed = handler.handle(
        _update(body={"mid": "edit.4", "text": f"/edit {case_id} оборудование"})
    )

    assert "укажите оборудование" in incomplete.reply_text
    assert "Формат:" in malformed.reply_text
    assert service.get(case_id).status == "draft"
    assert len(service._cases) == 1


def test_extraction_updates_a_saved_draft_and_does_not_rerun_on_redelivery():
    service = CaseService()
    calls = []

    def extract(text):
        assert service.search(q="вибрация", equipment_id=None, limit=3) == []
        calls.append(text)
        return ExtractionOutcome(
            draft=DraftExtraction(equipment=Equipment(id="Н-204"), symptom="вибрация")
        )

    handler = MaxUpdateHandler(service, draft_extractor=extract)
    first = handler.handle(_update())
    second = handler.handle(_update())

    assert first.case is not None
    assert first.case.source.text == "На Н-204 выросла вибрация. Подтянули муфту."
    assert first.case.equipment is not None and first.case.equipment.id == "Н-204"
    assert first.case.symptom == "вибрация"
    assert second.case is not None and second.case.id == first.case.id
    assert len(calls) == 1


def test_model_failure_keeps_the_original_message():
    service = CaseService()
    handler = MaxUpdateHandler(
        service,
        draft_extractor=lambda _text: ExtractionOutcome(
            draft=DraftExtraction(), error="model_unavailable"
        ),
    )

    outcome = handler.handle(_update())

    assert outcome.case is not None
    assert outcome.case.source.text.startswith("На Н-204")
    assert outcome.case.status == "draft"
    assert AI_UNAVAILABLE_NOTICE in outcome.reply_text


def test_without_yandex_credentials_no_external_call_is_attempted(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)

    outcome = extract_message("На Н-204 выросла вибрация")

    assert outcome.error == "model_not_configured"
    assert outcome.draft.case_fields()["symptom"] is None


def test_search_command_returns_only_confirmed_cases_and_creates_no_draft():
    service = CaseService()
    case, _ = service.create(
        CaseCreateRequest(
            source=Source(
                id="source-confirmed",
                type="max_message",
                text="На Н-204 была вибрация",
                author_id="12345",
                received_at=datetime.now(UTC),
            ),
            equipment=Equipment(id="Н-204"),
            symptom="вибрация насоса",
        )
    )
    service.confirm(case.id, CaseConfirmRequest(), actor_from_max_event(12345))
    handler = MaxUpdateHandler(service)

    result = handler.handle(_update(body={"mid": "search-command", "text": "/search вибрация"}))

    assert result.case is None
    assert "Симптом: вибрация насоса" in result.reply_text
    assert "Источник:" in result.reply_text
    assert "source-confirmed" not in result.reply_text
    assert "Участник ремонта" not in result.reply_text
    assert len(service.search(q=None, equipment_id=None, limit=20)) == 1


# --- Issue #5: a failed batch must not be committed away --------------------


def _batch(*updates, marker: int):
    return MaxUpdateList(updates=list(updates), marker=marker)


def test_failed_update_keeps_the_marker_so_the_batch_can_be_redelivered(monkeypatch, caplog):
    def exploding_process(update, client):
        raise RuntimeError("database is unreachable")

    monkeypatch.setattr(polling, "process_update", exploding_process)

    with caplog.at_level(logging.ERROR):
        next_marker = polling.process_batch(_batch(_update(), marker=999), client=None, marker=42)

    assert next_marker == 42, "a batch that failed must not be committed"
    assert MID in caplog.text
    # The failure type is logged, never the exception text (it can carry a DSN).
    assert "database is unreachable" not in caplog.text


def test_successful_batch_advances_the_marker(monkeypatch):
    monkeypatch.setattr(polling, "process_update", lambda update, client: None)

    next_marker = polling.process_batch(_batch(_update(), marker=999), client=None, marker=42)

    assert next_marker == 999


# --- startup log: is AI extraction configured? -------------------------------


def test_startup_log_warns_when_ai_is_not_configured(monkeypatch, caplog):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)

    with caplog.at_level(logging.INFO):
        polling.log_ai_status()

    assert "AI extraction is OFF" in caplog.text


def test_startup_log_confirms_ai_without_leaking_the_key(monkeypatch, caplog):
    secret = "AQVN-test-secret-value"
    monkeypatch.setenv("LLM_API_KEY", secret)
    monkeypatch.setenv("YANDEX_FOLDER_ID", "b1gtestfolder")

    with caplog.at_level(logging.INFO):
        polling.log_ai_status()

    assert "AI extraction is ON" in caplog.text
    assert secret not in caplog.text

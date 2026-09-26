"""Issue #5: a MAX text message becomes a stored draft case and gets acknowledged."""

import logging
from datetime import UTC, datetime

from app.ai.extraction import DraftExtraction, ExtractionOutcome
from app.bot import polling
from app.bot.ai import extract_message
from app.bot.handler import AI_UNAVAILABLE_NOTICE, TEXT_ONLY_NOTICE, MaxUpdateHandler
from app.bot.models import MaxUpdate, MaxUpdateList
from app.cases.models import CaseConfirmRequest, CaseCreateRequest, Equipment, Source
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


def test_sender_gets_an_acknowledgement_with_the_case_id():
    handler, _ = _handler()

    outcome = handler.handle(_update())

    assert outcome.has_reply
    assert outcome.case is not None
    assert outcome.case.id in outcome.reply_text
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
    assert case.id in result.reply_text
    assert "source-confirmed" in result.reply_text
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

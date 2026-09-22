"""Issue #5: a MAX text message becomes a stored draft case and gets acknowledged."""

from app.bot.handler import TEXT_ONLY_NOTICE, MaxUpdateHandler
from app.bot.models import MaxUpdate
from app.cases.service import CaseService

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


def test_redelivered_update_creates_no_duplicate_and_stays_silent():
    handler, service = _handler()

    first = handler.handle(_update())
    second = handler.handle(_update())

    assert first.created is True
    assert second.created is False
    assert second.case is not None
    assert first.case is not None
    assert first.case.id == second.case.id
    assert second.has_reply is False
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

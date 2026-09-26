"""Turn MAX updates into stored cases.

Transport-independent on purpose: long polling uses it today
(`app.bot.polling`), and a webhook endpoint can reuse it unchanged once the
public HTTPS stand is up (Issue #11).
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.ai.extraction import ExtractionOutcome
from app.bot.models import UPDATE_MESSAGE_CREATED, MaxUpdate, to_utc
from app.cases.models import Case, CaseCreateRequest, CaseUpdateRequest, Source
from app.cases.protocol import CaseServiceProtocol
from app.identity import actor_from_max_event

# Placeholder wording. The bot's dialogue, commands and texts are Issue #16
# (Egor); this adapter only has to confirm receipt so the sender is not left
# without an answer.
ACK_TEMPLATE = (
    "Сообщение сохранено как черновик случая {case_id}. Проверьте факты перед подтверждением."
)
AI_UNAVAILABLE_NOTICE = " AI сейчас недоступен, исходный текст сохранён."
TEXT_ONLY_NOTICE = "Пока я понимаю только текстовые сообщения."
HELP_TEXT = (
    "Отправьте описание ремонта, чтобы сохранить черновик. "
    "Для поиска подтверждённого опыта напишите /search и описание проблемы."
)


@dataclass(frozen=True)
class HandledUpdate:
    """What the transport should do after the update was processed."""

    case: Case | None = None
    created: bool = False
    reply_text: str | None = None
    reply_user_id: int | None = None
    reply_chat_id: int | None = None

    @property
    def has_reply(self) -> bool:
        return self.reply_text is not None


IGNORED = HandledUpdate()


class MaxUpdateHandler:
    def __init__(
        self,
        case_service: CaseServiceProtocol,
        draft_extractor: Callable[[str], ExtractionOutcome] | None = None,
    ) -> None:
        self._case_service = case_service
        self._draft_extractor = draft_extractor

    def _reply(self, text: str, *, user_id: int | None, chat_id: int | None) -> HandledUpdate:
        return HandledUpdate(reply_text=text, reply_user_id=user_id, reply_chat_id=chat_id)

    @staticmethod
    def _draft_summary(case: Case) -> str:
        fields = [
            ("оборудование", case.equipment.label or case.equipment.id if case.equipment else None),
            ("симптом", case.symptom),
            ("причина", case.cause),
            ("действие", case.action),
            ("результат", case.result),
        ]
        known = [f"{label}: {value}" for label, value in fields if value]
        return "\n📝 Черновик AI, проверьте факты:\n" + "\n".join(known) if known else ""

    def _search(self, query: str, *, user_id: int | None, chat_id: int | None) -> HandledUpdate:
        if not query:
            return self._reply(
                "Напишите запрос после /search, например: /search вибрация насоса",
                user_id=user_id,
                chat_id=chat_id,
            )
        results = self._case_service.search(q=query, equipment_id=None, limit=3)
        if not results:
            return self._reply(
                "Подтверждённых похожих случаев пока нет.", user_id=user_id, chat_id=chat_id
            )
        lines = ["Похожие подтверждённые случаи:"]
        for result in results:
            case = result.case
            equipment = (
                case.equipment.label or case.equipment.id
                if case.equipment is not None
                else "оборудование не указано"
            )
            summary = (case.symptom or case.action or case.result or "без описания")[:120]
            participant = (
                case.participant.display_name or case.participant.id
                if case.participant is not None
                else "не указан"
            )
            lines.append(
                f"{case.id}: {equipment}; {summary}; участник: {participant}; "
                f"источник: {case.source.id}"
            )
        return self._reply("\n".join(lines), user_id=user_id, chat_id=chat_id)

    def handle(self, update: MaxUpdate) -> HandledUpdate:
        if update.update_type != UPDATE_MESSAGE_CREATED or update.message is None:
            return IGNORED

        message = update.message
        sender = message.sender
        # No sender means a channel post; a bot sender would let the bot answer itself.
        if sender is None or sender.is_bot:
            return IGNORED

        reply_user_id = None if message.recipient.chat_id else sender.user_id
        reply_chat_id = message.recipient.chat_id

        text = (message.body.text or "").strip()
        if not text:
            return self._reply(TEXT_ONLY_NOTICE, user_id=reply_user_id, chat_id=reply_chat_id)

        if text in {"/start", "/help"}:
            return self._reply(HELP_TEXT, user_id=reply_user_id, chat_id=reply_chat_id)
        if text == "/search" or text.startswith("/search "):
            return self._search(
                text.removeprefix("/search").strip(),
                user_id=reply_user_id,
                chat_id=reply_chat_id,
            )
        if text.startswith("/"):
            return self._reply(HELP_TEXT, user_id=reply_user_id, chat_id=reply_chat_id)

        # MAX asserted this sender over the bot's authenticated connection, so the
        # author recorded on the case is a verified identity, not a client claim.
        author = actor_from_max_event(sender.user_id)
        case, created = self._case_service.create(
            CaseCreateRequest(
                source=Source(
                    id=message.body.mid,
                    type="max_message",
                    text=text,
                    author_id=author.user_id,
                    received_at=to_utc(message.timestamp),
                    # Same message delivered twice must not create a second case.
                    external_event_id=message.body.mid,
                )
            )
        )
        extraction_error = None
        if created and self._draft_extractor is not None:
            outcome = self._draft_extractor(text)
            extraction_error = outcome.error
            fields = {
                name: value
                for name, value in outcome.draft.case_fields().items()
                if value is not None
            }
            if fields:
                case = self._case_service.update(case.id, CaseUpdateRequest(**fields), author)
        return HandledUpdate(
            case=case,
            created=created,
            reply_text=ACK_TEMPLATE.format(case_id=case.id)
            + self._draft_summary(case)
            + (AI_UNAVAILABLE_NOTICE if extraction_error else ""),
            reply_user_id=reply_user_id,
            reply_chat_id=reply_chat_id,
        )

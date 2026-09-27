"""Turn MAX updates into stored cases.

Transport-independent on purpose: long polling uses it today
(`app.bot.polling`), and a webhook endpoint can reuse it unchanged once the
public HTTPS stand is up (Issue #11).
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.ai.extraction import ExtractionOutcome
from app.bot.models import UPDATE_MESSAGE_CREATED, MaxUpdate, to_utc
from app.cases.errors import (
    CaseAlreadyConfirmedError,
    CaseForbiddenError,
    CaseNotFoundError,
    InsufficientDataError,
)
from app.cases.models import (
    Case,
    CaseConfirmRequest,
    CaseCreateRequest,
    CaseUpdateRequest,
    Equipment,
    Source,
)
from app.cases.protocol import CaseServiceProtocol
from app.identity import actor_from_max_event

# Placeholder wording. The bot's dialogue, commands and texts are Issue #16
# (Egor); this adapter only has to confirm receipt so the sender is not left
# without an answer.
ACK_TEMPLATE = (
    "Сообщение сохранено как черновик случая {case_id}. Проверьте факты: "
    "исправьте их через /edit {case_id} <поле> <значение>, затем отправьте "
    "/confirm {case_id}."
)
AI_UNAVAILABLE_NOTICE = " AI сейчас недоступен, исходный текст сохранён."
TEXT_ONLY_NOTICE = "Пока я понимаю только текстовые сообщения."
HELP_TEXT = (
    "Отправьте описание ремонта, чтобы сохранить черновик. "
    "Исправьте поле командой /edit <ID> <поле> <значение> и подтвердите "
    "командой /confirm <ID>. Поля: оборудование, симптом, причина, действие, результат. "
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

    def _change_case(
        self, text: str, *, sender_id: int, user_id: int | None, chat_id: int | None
    ) -> HandledUpdate:
        parts = text.split(maxsplit=3)
        command = parts[0]
        if command == "/confirm":
            if len(parts) != 2:
                return self._reply(
                    "Формат: /confirm <ID черновика>", user_id=user_id, chat_id=chat_id
                )
            case_id = parts[1]
            payload = CaseConfirmRequest()
        else:
            if len(parts) != 4 or not parts[3].strip():
                return self._reply(
                    "Формат: /edit <ID черновика> <поле> <значение>",
                    user_id=user_id,
                    chat_id=chat_id,
                )
            case_id = parts[1]
            field = {
                "оборудование": "equipment",
                "симптом": "symptom",
                "причина": "cause",
                "действие": "action",
                "результат": "result",
            }.get(parts[2].lower())
            if field is None:
                return self._reply(HELP_TEXT, user_id=user_id, chat_id=chat_id)
            value = parts[3].strip()
            payload = CaseUpdateRequest(
                **{field: Equipment(id=value) if field == "equipment" else value}
            )

        actor = actor_from_max_event(sender_id)
        try:
            if command == "/confirm":
                case = self._case_service.confirm(case_id, payload, actor)
                response = f"✅ Случай {case.id} подтверждён и доступен в поиске."
            else:
                case = self._case_service.update(case_id, payload, actor)
                response = f"Черновик {case.id} обновлён." + self._draft_summary(case)
        except CaseNotFoundError:
            response = "Черновик с таким ID не найден."
        except CaseForbiddenError:
            response = "Исправить или подтвердить черновик может только автор сообщения."
        except CaseAlreadyConfirmedError:
            response = "Этот случай уже подтверждён."
        except InsufficientDataError:
            response = (
                "Для подтверждения укажите оборудование и хотя бы симптом, "
                "действие или результат через /edit."
            )
        return self._reply(response, user_id=user_id, chat_id=chat_id)

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
        if text in {"/edit", "/confirm"} or text.startswith(("/edit ", "/confirm ")):
            return self._change_case(
                text,
                sender_id=sender.user_id,
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

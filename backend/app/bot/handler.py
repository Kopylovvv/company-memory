"""Turn MAX updates into stored cases.

Transport-independent on purpose: long polling uses it today
(`app.bot.polling`), and a webhook endpoint can reuse it unchanged once the
public HTTPS stand is up (Issue #11).
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.ai.extraction import ExtractionOutcome
from app.bot.models import UPDATE_BOT_STARTED, UPDATE_MESSAGE_CREATED, MaxUpdate, to_utc
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

ACK_TEXT = (
    "Сообщение сохранено как черновик. Проверьте поля ниже. "
    "Чтобы исправить последнее сообщение, напишите, например, "
    "/edit результат течь прекратилась. Когда всё верно — /confirm."
)
AI_UNAVAILABLE_NOTICE = " AI сейчас недоступен, исходный текст сохранён."
TEXT_ONLY_NOTICE = "Пока я понимаю только текстовые сообщения."
HELP_TEXT = (
    "Отправьте описание ремонта — я сохраню черновик и предложу поля для проверки. "
    "Для последнего своего черновика: /edit <поле> <значение> и /confirm. "
    "Поля: оборудование, симптом, причина, действие, результат. "
    "Для поиска: /search <описание проблемы>. "
    "Команды без ID работают с вашим последним черновиком."
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
        return "\n📝 Предложенные поля, проверьте факты:\n" + "\n".join(known) if known else ""

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
        lines = ["Похожие подтверждённые случаи (это опыт коллег, не диагноз):"]
        for index, result in enumerate(results, start=1):
            case = result.case
            equipment = (
                case.equipment.label or case.equipment.id
                if case.equipment is not None
                else "оборудование не указано"
            )
            participant = (
                case.participant.display_name or case.participant.id
                if case.participant is not None
                else "не указан в сообщении"
            )
            detail = [f"{index}. {equipment}" + (" · демо" if case.is_demo else "")]
            for label, value in (
                ("Симптом", case.symptom),
                ("Причина", case.cause),
                ("Что сделали", case.action),
                ("Результат", case.result),
            ):
                if value:
                    detail.append(f"{label}: {value[:180]}")
            detail.append(f"Участник ремонта: {participant}")
            source_type = "MAX" if case.source.type == "max_message" else "синтетические данные"
            detail.append(f"Источник: {source_type}, {case.source.received_at.date()}")
            lines.append("\n".join(detail))
        return self._reply("\n\n".join(lines), user_id=user_id, chat_id=chat_id)

    def _change_case(
        self, text: str, *, sender_id: int, user_id: int | None, chat_id: int | None
    ) -> HandledUpdate:
        parts = text.split(maxsplit=3)
        command = parts[0]
        actor = actor_from_max_event(sender_id)
        if command == "/confirm":
            if len(parts) > 2:
                return self._reply(
                    "Формат: /confirm или /confirm <ID черновика>",
                    user_id=user_id,
                    chat_id=chat_id,
                )
            case_id = parts[1] if len(parts) == 2 else None
            payload = CaseConfirmRequest()
        else:
            field_names = {
                "оборудование": "equipment",
                "симптом": "symptom",
                "причина": "cause",
                "действие": "action",
                "результат": "result",
            }
            if len(parts) >= 2 and parts[1].lower() in field_names:
                case_id = None
                field_name = parts[1].lower()
                value = text.split(maxsplit=2)[2].strip() if len(parts) >= 3 else ""
            elif len(parts) == 4:
                case_id = parts[1]
                field_name = parts[2].lower()
                value = parts[3].strip()
            else:
                return self._reply(
                    "Формат: /edit <поле> <значение> (или /edit <ID> <поле> <значение>)",
                    user_id=user_id,
                    chat_id=chat_id,
                )
            field = field_names.get(field_name)
            if field is None:
                return self._reply(HELP_TEXT, user_id=user_id, chat_id=chat_id)
            if not value:
                return self._reply(
                    "После названия поля напишите новое значение.",
                    user_id=user_id,
                    chat_id=chat_id,
                )
            payload = CaseUpdateRequest(
                **{field: Equipment(id=value) if field == "equipment" else value}
            )

        if case_id is None:
            latest = self._case_service.latest_draft(actor.user_id)
            if latest is None:
                return self._reply(
                    "У вас нет черновика для исправления. Сначала отправьте описание ремонта.",
                    user_id=user_id,
                    chat_id=chat_id,
                )
            case_id = latest.id
        try:
            if command == "/confirm":
                case = self._case_service.confirm(case_id, payload, actor)
                response = "✅ Случай подтверждён и доступен в поиске. Попробуйте /search."
            else:
                case = self._case_service.update(case_id, payload, actor)
                response = "Черновик обновлён." + self._draft_summary(case)
        except CaseNotFoundError:
            response = "Черновик с таким ID не найден."
        except CaseForbiddenError:
            response = "Исправить или подтвердить черновик может только автор сообщения."
        except CaseAlreadyConfirmedError:
            response = "Этот случай уже подтверждён."
        except InsufficientDataError:
            response = (
                "Для подтверждения укажите оборудование и хотя бы симптом, "
                "действие или результат через /edit <поле> <значение>."
            )
        return self._reply(response, user_id=user_id, chat_id=chat_id)

    def handle(self, update: MaxUpdate) -> HandledUpdate:
        if update.update_type == UPDATE_BOT_STARTED and update.user and not update.user.is_bot:
            return self._reply(
                HELP_TEXT,
                user_id=None if update.chat_id else update.user.user_id,
                chat_id=update.chat_id,
            )
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
            reply_text=ACK_TEXT
            + self._draft_summary(case)
            + (AI_UNAVAILABLE_NOTICE if extraction_error else ""),
            reply_user_id=reply_user_id,
            reply_chat_id=reply_chat_id,
        )

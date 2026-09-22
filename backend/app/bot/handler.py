"""Turn MAX updates into stored cases.

Transport-independent on purpose: long polling uses it today
(`app.bot.polling`), and a webhook endpoint can reuse it unchanged once the
public HTTPS stand is up (Issue #11).
"""

from dataclasses import dataclass

from app.bot.models import UPDATE_MESSAGE_CREATED, MaxUpdate, to_utc
from app.cases.models import Case, CaseCreateRequest, Source
from app.cases.protocol import CaseServiceProtocol

# Placeholder wording. The bot's dialogue, commands and texts are Issue #16
# (Egor); this adapter only has to confirm receipt so the sender is not left
# without an answer.
ACK_TEMPLATE = (
    "Сообщение сохранено как черновик случая {case_id}. Проверка и подтверждение — следующий шаг."
)
TEXT_ONLY_NOTICE = "Пока я понимаю только текстовые сообщения."


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
    def __init__(self, case_service: CaseServiceProtocol) -> None:
        self._case_service = case_service

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
            return HandledUpdate(
                reply_text=TEXT_ONLY_NOTICE,
                reply_user_id=reply_user_id,
                reply_chat_id=reply_chat_id,
            )

        case, created = self._case_service.create(
            CaseCreateRequest(
                source=Source(
                    id=message.body.mid,
                    type="max_message",
                    text=text,
                    author_id=str(sender.user_id),
                    received_at=to_utc(message.timestamp),
                    # Same message delivered twice must not create a second case.
                    external_event_id=message.body.mid,
                )
            )
        )
        if not created:
            # Redelivery of an update we already stored: stay silent instead of
            # answering the same message twice.
            return HandledUpdate(case=case, created=False)

        return HandledUpdate(
            case=case,
            created=True,
            reply_text=ACK_TEMPLATE.format(case_id=case.id),
            reply_user_id=reply_user_id,
            reply_chat_id=reply_chat_id,
        )

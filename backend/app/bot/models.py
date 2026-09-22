"""Subset of the MAX Bot API payloads this adapter consumes.

Modelled from the official schema
(https://github.com/max-messenger/max-bot-api-client-go/blob/v2/schema.yaml).
Unknown fields are ignored on purpose: this is someone else's API and it may
grow fields at any time, unlike our own contract in `app.cases.models`, which
rejects unknown fields.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

UPDATE_MESSAGE_CREATED = "message_created"


class MaxApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MaxUser(MaxApiModel):
    user_id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    is_bot: bool = False


class MaxRecipient(MaxApiModel):
    chat_id: int | None = None
    user_id: int | None = None
    chat_type: str | None = None


class MaxMessageBody(MaxApiModel):
    mid: str
    seq: int | None = None
    text: str | None = None


class MaxMessage(MaxApiModel):
    body: MaxMessageBody
    recipient: MaxRecipient
    timestamp: int
    sender: MaxUser | None = None


class MaxUpdate(MaxApiModel):
    update_type: str
    timestamp: int
    # Optional even for message_created: the platform is known to deliver such an
    # update without a message for some native voice messages.
    message: MaxMessage | None = None
    user_locale: str | None = None


class MaxUpdateList(MaxApiModel):
    updates: list[MaxUpdate] = []
    marker: int | None = None


def to_utc(unix_milliseconds: int) -> datetime:
    """MAX timestamps are Unix time in milliseconds; the case contract stores UTC."""
    return datetime.fromtimestamp(unix_milliseconds / 1000, tz=UTC)

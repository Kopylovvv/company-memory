"""Who is acting, and how the server knows it (Issue #10).

The product rule is that the server decides who a request comes from, never a
field the client fills in. In the bot-only MVP there is exactly one channel
that establishes identity: updates the bot fetches from MAX over its
authenticated connection. MAX asserts `sender.user_id` there, so an actor
built from an update is verified.

`demo_header` exists only so the HTTP API stays usable for local development
and manual checks. It trusts whatever the caller sends and is therefore off
unless `ALLOW_DEMO_IDENTITY` is set, which production never does.
"""

import os
from dataclasses import dataclass
from typing import Literal

ActorSource = Literal["max_bot_event", "demo_header"]

DEMO_IDENTITY_ENV = "ALLOW_DEMO_IDENTITY"


@dataclass(frozen=True)
class Actor:
    """A user the server is willing to act for."""

    user_id: str
    verified_via: ActorSource

    @property
    def is_verified(self) -> bool:
        """False for the development header: it proves nothing about the caller."""
        return self.verified_via != "demo_header"


class IdentityNotVerifiedError(Exception):
    """The request carried no identity the server is willing to trust."""

    code = "identity_not_verified"

    def __init__(self) -> None:
        super().__init__(
            "This action needs a MAX-verified user. The HTTP API has no verified "
            "identity in the bot-only MVP; act through the bot instead."
        )


def actor_from_max_event(sender_user_id: int | str) -> Actor:
    """Identity taken from an update MAX delivered over the bot's authenticated channel."""
    return Actor(user_id=str(sender_user_id), verified_via="max_bot_event")


def demo_identity_allowed() -> bool:
    return os.environ.get(DEMO_IDENTITY_ENV, "").strip().lower() in {"1", "true", "yes"}

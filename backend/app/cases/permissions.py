"""Minimal rights for case actions (Issue #10).

Deliberately small for the bot-only MVP, and shared by both case stores so the
in-memory and PostgreSQL paths cannot drift apart:

- reading a confirmed case: any actor the server accepts;
- correcting or confirming a draft: only the author of the source message.

Anything richer — roles, teams, per-equipment visibility — needs its own Issue
and the consumers' agreement, because it changes what the bot and the future
mini-app can show.
"""

from app.cases.errors import CaseForbiddenError
from app.identity import Actor


def ensure_may_modify(actor: Actor, *, case_id: str, source_author_id: str) -> None:
    """Correcting and confirming someone else's draft is refused, not silently ignored."""
    if actor.user_id != source_author_id:
        raise CaseForbiddenError(case_id)

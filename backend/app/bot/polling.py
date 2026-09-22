"""Long-polling runner for the MAX bot.

Long polling is the development mode: the platform documents it as limited in
speed and event retention, and webhooks require a public HTTPS endpoint with a
trusted certificate, which arrives with the production stand (Issue #11). The
handler is shared, so switching to a webhook does not change how cases are
stored.

Run it with `uv run python -m app.bot.polling` (add `--once` for a single
cycle). `MAX_BOT_TOKEN` comes from the environment; it is never read from the
source tree.
"""

import logging
import sys
import time

from app.bot.client import MaxApiError, MaxClient
from app.bot.handler import HandledUpdate, MaxUpdateHandler
from app.bot.models import MaxUpdate
from app.bot.settings import MissingTokenError, load_max_bot_settings
from app.cases.protocol import CaseServiceProtocol
from app.db.engine import SessionLocal
from app.db.repository import DbCaseService

logger = logging.getLogger("app.bot.polling")

ERROR_BACKOFF_SECONDS = 5.0


def _handle_update(update: MaxUpdate, case_service: CaseServiceProtocol) -> HandledUpdate:
    handler = MaxUpdateHandler(case_service)
    return handler.handle(update)


def process_update(update: MaxUpdate, client: MaxClient) -> HandledUpdate:
    """Store one update and answer its sender. Message text never reaches the log."""
    with SessionLocal() as session:
        outcome = _handle_update(update, DbCaseService(session))

    if outcome.case is not None:
        logger.info(
            "update %s stored as case %s (created=%s)",
            update.message.body.mid if update.message else "-",
            outcome.case.id,
            outcome.created,
        )
    if outcome.has_reply and outcome.reply_text is not None:
        client.send_text(
            outcome.reply_text,
            user_id=outcome.reply_user_id,
            chat_id=outcome.reply_chat_id,
        )
    return outcome


def run(once: bool = False) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = load_max_bot_settings()
    except MissingTokenError as exc:
        logger.error("%s", exc)
        return 2

    marker: int | None = None
    with MaxClient(settings) as client:
        try:
            me = client.get_me()
            logger.info("connected to MAX as @%s", me.get("username", "unknown"))
        except MaxApiError as exc:
            logger.error("%s", exc)
            return 1

        while True:
            try:
                batch = client.get_updates(marker)
            except MaxApiError as exc:
                logger.error("%s", exc)
                if once:
                    return 1
                time.sleep(ERROR_BACKOFF_SECONDS)
                continue

            for update in batch.updates:
                try:
                    process_update(update, client)
                except MaxApiError as exc:
                    # Keep the marker unchanged so the platform redelivers it;
                    # storing is idempotent, so a repeat cannot duplicate a case.
                    logger.error("could not process an update: %s", exc)
            marker = batch.marker

            if once:
                logger.info("processed %d update(s)", len(batch.updates))
                return 0


if __name__ == "__main__":
    sys.exit(run(once="--once" in sys.argv))

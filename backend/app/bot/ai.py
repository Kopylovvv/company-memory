"""Extract a draft after the original MAX message has been stored."""

import logging

from app.ai.extraction import EMPTY_DRAFT, ExtractionOutcome, extract_or_empty
from app.ai.yandex_gpt import (
    ModelNotConfiguredError,
    YandexGptClient,
    load_yandex_gpt_settings,
)

logger = logging.getLogger(__name__)


def extract_message(text: str) -> ExtractionOutcome:
    """Use YandexGPT when configured; keep a usable empty draft on failure."""
    try:
        settings = load_yandex_gpt_settings()
        with YandexGptClient(settings) as client:
            return extract_or_empty(text, client)
    except ModelNotConfiguredError:
        return ExtractionOutcome(draft=EMPTY_DRAFT, error="model_not_configured")
    except Exception as exc:
        # Exception messages and tracebacks can contain request text or a key.
        logger.warning("AI extraction failed: %s", type(exc).__name__)
        return ExtractionOutcome(draft=EMPTY_DRAFT, error="model_unavailable")

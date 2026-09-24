"""MAX bot connection settings, read from environment variables.

The token never appears in code, logs or tracebacks: it is held in a
`SecretStr`, so printing the settings renders it as `**********`.
"""

import os

from pydantic import BaseModel, ConfigDict, Field, SecretStr

DEFAULT_BASE_URL = "https://platform-api2.max.ru"


class MaxBotSettings(BaseModel):
    """Everything the MAX client needs to reach the platform."""

    model_config = ConfigDict(extra="forbid")

    token: SecretStr
    base_url: str = DEFAULT_BASE_URL
    ca_bundle: str | None = Field(
        default=None,
        description=(
            "Path to a CA bundle that trusts the Russian Trusted Root CA, which signs the "
            "MAX API certificate chain. Null means the system/certifi bundle is used, which "
            "does not include that root. See backend/app/bot/README.md."
        ),
    )
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    long_poll_timeout_seconds: int = Field(default=30, ge=0, le=90)
    updates_limit: int = Field(default=100, ge=1, le=1000)


class MissingTokenError(RuntimeError):
    """`MAX_BOT_TOKEN` is not set, so the bot cannot authenticate."""


def load_max_bot_settings() -> MaxBotSettings:
    token = os.environ.get("MAX_BOT_TOKEN", "").strip()
    if not token:
        raise MissingTokenError(
            "MAX_BOT_TOKEN is not set. Put it in the environment (.env locally, server "
            "environment in production) — never in the source tree."
        )
    ca_bundle = os.environ.get("MAX_API_CA_BUNDLE", "").strip() or None
    return MaxBotSettings(
        token=SecretStr(token),
        base_url=os.environ.get("MAX_API_BASE_URL", "").strip() or DEFAULT_BASE_URL,
        ca_bundle=ca_bundle,
    )

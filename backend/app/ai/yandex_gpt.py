"""YandexGPT adapter for draft extraction (Issue #6).

Implements `app.ai.extraction.ModelClient` over the Foundation Models REST
API with `httpx`, so no new dependency. The team's Yandex Cloud grant already
hosts the server, which is why this provider was chosen.

Settings come from the environment only; the API key is a `SecretStr` and
never reaches logs, errors or tracebacks. Every call has a timeout, and any
transport or HTTP failure becomes `ModelUnavailableError`, which extraction
turns into an empty draft rather than a lost message.

The request asks for a JSON object (`jsonObject`) instead of passing our
JSON Schema: the answer is validated by `ExtractedFields` anyway, and the key
list is spelled out in the prompt.
"""

import os

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.ai.extraction import ExtractionError, ModelUnavailableError

DEFAULT_BASE_URL = "https://llm.api.cloud.yandex.net"
COMPLETION_PATH = "/foundationModels/v1/completion"
DEFAULT_MODEL = "yandexgpt/latest"

FINAL_STATUSES = {"ALTERNATIVE_STATUS_FINAL"}
REFUSED_STATUSES = {"ALTERNATIVE_STATUS_CONTENT_FILTER"}


class YandexGptSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr
    folder_id: str = Field(min_length=1)
    model: str = Field(
        default=DEFAULT_MODEL, description="Model path after the folder, e.g. 'yandexgpt/latest'."
    )
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = Field(default=20.0, gt=0)
    max_tokens: int = Field(default=1000, ge=100, le=8000)

    @property
    def model_uri(self) -> str:
        return f"gpt://{self.folder_id}/{self.model}"


class ModelNotConfiguredError(RuntimeError):
    """The environment lacks the key or folder, so extraction stays off."""


def load_yandex_gpt_settings() -> YandexGptSettings:
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    folder_id = os.environ.get("YANDEX_FOLDER_ID", "").strip()
    if not api_key or not folder_id:
        raise ModelNotConfiguredError(
            "YandexGPT is not configured: set LLM_API_KEY (a Yandex Cloud API key with the "
            "yc.ai.foundationModels.execute scope) and YANDEX_FOLDER_ID in the environment."
        )
    timeout = os.environ.get("LLM_TIMEOUT_SECONDS", "").strip()
    return YandexGptSettings(
        api_key=SecretStr(api_key),
        folder_id=folder_id,
        model=os.environ.get("YANDEX_GPT_MODEL", "").strip() or DEFAULT_MODEL,
        **({"timeout_seconds": float(timeout)} if timeout else {}),
    )


class YandexGptClient:
    def __init__(self, settings: YandexGptSettings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            base_url=settings.base_url,
            headers={
                "Authorization": f"Api-Key {settings.api_key.get_secret_value()}",
                "x-folder-id": settings.folder_id,
            },
            timeout=settings.timeout_seconds,
        )

    def __enter__(self) -> "YandexGptClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def complete_json(self, *, system: str, user: str, schema: dict[str, object]) -> str:
        body = {
            "modelUri": self._settings.model_uri,
            "completionOptions": {
                "stream": False,
                # Extraction, not writing: the most likely reading of the message.
                "temperature": 0,
                "maxTokens": str(self._settings.max_tokens),
            },
            "messages": [
                {"role": "system", "text": system},
                {"role": "user", "text": user},
            ],
            "jsonObject": True,
        }
        try:
            response = self._client.post(COMPLETION_PATH, json=body)
        except httpx.HTTPError as exc:
            # Class name only: the exception text can include the request.
            raise ModelUnavailableError(f"YandexGPT unreachable: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ModelUnavailableError(f"YandexGPT answered HTTP {response.status_code}")

        try:
            payload = response.json()
            result = payload.get("result", payload)
            alternative = result["alternatives"][0]
            status = alternative.get("status")
            text = alternative["message"]["text"]
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ExtractionError("invalid_output") from exc

        if status in REFUSED_STATUSES:
            raise ExtractionError("content_filter")
        if status not in FINAL_STATUSES:
            # Truncated or partial JSON would fail validation anyway; name the cause.
            raise ExtractionError("truncated_output")
        return text

"""HTTP client for the MAX Bot API.

A thin, replaceable adapter (see backend/AGENTS.md): every call has a timeout,
the token travels only in the `Authorization` header, and nothing here logs
the token or message text.
"""

import httpx

from app.bot.models import MaxUpdateList
from app.bot.settings import MaxBotSettings


class MaxApiError(RuntimeError):
    """The MAX platform refused a request or could not be reached."""


class MaxCertificateError(MaxApiError):
    """TLS verification failed, almost always the missing Russian Trusted Root CA."""


class MaxClient:
    def __init__(self, settings: MaxBotSettings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            base_url=settings.base_url,
            headers={"Authorization": settings.token.get_secret_value()},
            verify=settings.ca_bundle or True,
            timeout=httpx.Timeout(
                settings.request_timeout_seconds,
                # Long polling holds the response open for up to `timeout` seconds.
                read=settings.long_poll_timeout_seconds + settings.request_timeout_seconds,
            ),
        )

    def __enter__(self) -> "MaxClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_me(self) -> dict:
        """Bot identity. Used to check the token and to ignore the bot's own messages."""
        return self._request("GET", "/me").json()

    def set_commands(self) -> None:
        """Show supported commands in MAX when a user types '/'."""
        self._request(
            "PATCH",
            "/me/commands",
            json={
                "commands": [
                    {"name": "start", "description": "Как пользоваться ботом"},
                    {"name": "help", "description": "Подсказка по командам"},
                    {"name": "search", "description": "Найти подтверждённый случай"},
                    {"name": "edit", "description": "Исправить последний свой черновик"},
                    {"name": "confirm", "description": "Подтвердить последний свой черновик"},
                ]
            },
        )

    def get_updates(self, marker: int | None = None) -> MaxUpdateList:
        params: dict[str, object] = {
            "limit": self._settings.updates_limit,
            "timeout": self._settings.long_poll_timeout_seconds,
        }
        if marker is not None:
            params["marker"] = marker
        response = self._request("GET", "/updates", params=params)
        return MaxUpdateList.model_validate(response.json())

    def send_text(self, text: str, *, user_id: int | None, chat_id: int | None) -> None:
        if user_id is None and chat_id is None:
            raise MaxApiError("Sending a message needs either user_id or chat_id.")
        params: dict[str, object] = {}
        if user_id is not None:
            params["user_id"] = user_id
        if chat_id is not None:
            params["chat_id"] = chat_id
        self._request("POST", "/messages", params=params, json={"text": text})

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        try:
            response = self._client.request(method, url, **kwargs)  # type: ignore[arg-type]
        except httpx.ConnectError as exc:
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                raise MaxCertificateError(
                    "TLS verification failed for the MAX API. Its certificate chain is issued "
                    "by the Russian Trusted Root CA, which standard trust stores do not "
                    "include. Point MAX_API_CA_BUNDLE at a bundle containing that root — see "
                    "backend/app/bot/README.md."
                ) from exc
            raise MaxApiError(f"Could not reach the MAX API: {exc.__class__.__name__}") from exc
        except httpx.HTTPError as exc:
            raise MaxApiError(f"MAX API request failed: {exc.__class__.__name__}") from exc
        if response.is_error:
            # Body may echo request data, so only the status reaches the caller and logs.
            raise MaxApiError(f"MAX API returned HTTP {response.status_code} for {method} {url}")
        return response

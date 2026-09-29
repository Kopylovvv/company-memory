"""Issue #5: the MAX client authenticates correctly and never exposes the token."""

import logging

import httpx
import pytest
from pydantic import SecretStr

from app.bot.client import MaxApiError, MaxCertificateError, MaxClient
from app.bot.settings import MaxBotSettings, MissingTokenError, load_max_bot_settings

TOKEN = "test-secret-token-value"


def _settings(**overrides) -> MaxBotSettings:
    fields = {"token": SecretStr(TOKEN), "long_poll_timeout_seconds": 1}
    fields.update(overrides)
    return MaxBotSettings(**fields)


def _client(handler) -> MaxClient:
    transport = httpx.MockTransport(handler)
    settings = _settings()
    http = httpx.Client(
        base_url=settings.base_url,
        headers={"Authorization": settings.token.get_secret_value()},
        transport=transport,
    )
    return MaxClient(settings, client=http)


def test_updates_are_parsed_and_marker_is_passed():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(
            200,
            json={
                "updates": [
                    {
                        "update_type": "message_created",
                        "timestamp": 1790000000000,
                        "message": {
                            "body": {"mid": "mid.1", "text": "привет"},
                            "recipient": {"chat_id": 5, "user_id": 9, "chat_type": "dialog"},
                            "timestamp": 1790000000000,
                            "sender": {"user_id": 7, "first_name": "Иван", "is_bot": False},
                        },
                    }
                ],
                "marker": 42,
            },
        )

    with _client(handler) as client:
        batch = client.get_updates(marker=41)

    assert batch.marker == 42
    assert batch.updates[0].message.body.text == "привет"
    assert seen["marker"] == "41"
    assert seen["auth"] == TOKEN


def test_send_text_posts_to_the_chat():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"message": {}})

    with _client(handler) as client:
        client.send_text("готово", user_id=None, chat_id=5)

    assert captured["url"] == "/messages"
    assert captured["params"] == {"chat_id": "5"}
    assert "готово" in captured["body"]


def test_send_text_needs_a_destination():
    with _client(lambda request: httpx.Response(200, json={})) as client:
        with pytest.raises(MaxApiError):
            client.send_text("готово", user_id=None, chat_id=None)


def test_command_hints_are_registered_with_max():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"commands": []})

    with _client(handler) as client:
        client.set_commands()

    assert captured["method"] == "PATCH"
    assert captured["path"] == "/me/commands"
    assert '"name":"search"' in captured["body"]
    assert '"name":"confirm"' in captured["body"]


def test_http_error_does_not_leak_the_response_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "verify.token", "message": TOKEN})

    with _client(handler) as client:
        with pytest.raises(MaxApiError) as error:
            client.get_me()

    assert TOKEN not in str(error.value)
    assert "401" in str(error.value)


def test_certificate_failure_explains_the_russian_root_ca():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

    with _client(handler) as client:
        with pytest.raises(MaxCertificateError) as error:
            client.get_me()

    assert "MAX_API_CA_BUNDLE" in str(error.value)


def test_token_is_not_printed_by_settings_or_logs(caplog):
    settings = _settings()

    with caplog.at_level(logging.INFO):
        logging.getLogger("app.bot.polling").info("connected with %s", settings)

    assert TOKEN not in repr(settings)
    assert TOKEN not in str(settings)
    assert TOKEN not in caplog.text


def test_missing_token_is_reported_clearly(monkeypatch):
    monkeypatch.delenv("MAX_BOT_TOKEN", raising=False)

    with pytest.raises(MissingTokenError):
        load_max_bot_settings()


def test_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("MAX_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MAX_API_CA_BUNDLE", "/etc/ssl/max.pem")

    settings = load_max_bot_settings()

    assert settings.token.get_secret_value() == TOKEN
    assert settings.ca_bundle == "/etc/ssl/max.pem"
    assert settings.base_url == "https://platform-api2.max.ru"

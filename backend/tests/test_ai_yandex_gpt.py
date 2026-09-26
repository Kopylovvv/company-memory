"""YandexGPT adapter over a mocked transport (Issue #6). No real API call."""

import json

import httpx
import pytest

from app.ai.extraction import ExtractionError, ModelUnavailableError, extract_or_empty
from app.ai.yandex_gpt import (
    COMPLETION_PATH,
    ModelNotConfiguredError,
    YandexGptClient,
    YandexGptSettings,
    load_yandex_gpt_settings,
)

API_KEY = "test-key-not-real"
SETTINGS = YandexGptSettings(api_key=API_KEY, folder_id="b1gfolder", timeout_seconds=5)
ANSWER = {
    "equipment_id": "Н-204",
    "symptom": "Вибрация",
    "cause": None,
    "action": "Подтянули крепление муфты",
    "result": "Вибрация ушла",
    "participant_name": None,
}
MESSAGE = "На Н-204 выросла вибрация. Подтянули крепление муфты — вибрация ушла."


def _completion(text: str, status: str = "ALTERNATIVE_STATUS_FINAL") -> dict:
    return {
        "result": {
            "alternatives": [{"message": {"role": "assistant", "text": text}, "status": status}],
            "usage": {"inputTextTokens": "10", "completionTokens": "5", "totalTokens": "15"},
            "modelVersion": "test",
        }
    }


def _client(handler) -> YandexGptClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        base_url=SETTINGS.base_url,
        transport=transport,
        headers={"Authorization": f"Api-Key {API_KEY}", "x-folder-id": SETTINGS.folder_id},
    )
    return YandexGptClient(SETTINGS, client=http)


def test_request_shape_and_answer_text():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion(json.dumps(ANSWER, ensure_ascii=False)))

    outcome = extract_or_empty(MESSAGE, _client(handler))

    assert outcome.error is None
    assert outcome.draft.equipment.id == "Н-204"
    request = seen[0]
    assert request.url.path == COMPLETION_PATH
    assert request.headers["Authorization"] == f"Api-Key {API_KEY}"
    body = json.loads(request.content)
    assert body["modelUri"] == "gpt://b1gfolder/yandexgpt/latest"
    assert body["completionOptions"]["temperature"] == 0
    assert body["jsonObject"] is True
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert MESSAGE in body["messages"][1]["text"]


def test_response_without_result_wrapper_is_read_too():
    unwrapped = _completion(json.dumps(ANSWER))["result"]
    client = _client(lambda request: httpx.Response(200, json=unwrapped))
    assert json.loads(client.complete_json(system="s", user="u", schema={})) == ANSWER


@pytest.mark.parametrize("status_code", [401, 429, 500])
def test_http_error_is_model_unavailable_without_secrets(status_code):
    client = _client(lambda request: httpx.Response(status_code, text=f"echo {API_KEY}"))

    with pytest.raises(ModelUnavailableError) as error:
        client.complete_json(system="s", user="u", schema={})
    assert str(status_code) in str(error.value)
    assert API_KEY not in str(error.value)


def test_timeout_is_model_unavailable_and_draft_stays_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    outcome = extract_or_empty(MESSAGE, _client(handler))
    assert outcome.error == "model_unavailable"
    assert outcome.draft.symptom is None


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("ALTERNATIVE_STATUS_CONTENT_FILTER", "content_filter"),
        ("ALTERNATIVE_STATUS_TRUNCATED_FINAL", "truncated_output"),
    ],
)
def test_unfinished_alternatives_are_explicit_errors(status, code):
    client = _client(lambda request: httpx.Response(200, json=_completion("{", status)))
    with pytest.raises(ExtractionError) as error:
        client.complete_json(system="s", user="u", schema={})
    assert error.value.code == code


def test_unexpected_body_is_invalid_output():
    client = _client(lambda request: httpx.Response(200, json={"result": {}}))
    with pytest.raises(ExtractionError) as error:
        client.complete_json(system="s", user="u", schema={})
    assert error.value.code == "invalid_output"


def test_settings_come_from_the_environment_and_hide_the_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", API_KEY)
    monkeypatch.setenv("YANDEX_FOLDER_ID", "b1gfolder")
    monkeypatch.setenv("YANDEX_GPT_MODEL", "yandexgpt-lite/latest")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "7.5")

    settings = load_yandex_gpt_settings()
    assert settings.model_uri == "gpt://b1gfolder/yandexgpt-lite/latest"
    assert settings.timeout_seconds == 7.5
    assert API_KEY not in repr(settings)


def test_missing_settings_keep_extraction_off(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("YANDEX_FOLDER_ID", "b1gfolder")
    with pytest.raises(ModelNotConfiguredError, match="LLM_API_KEY"):
        load_yandex_gpt_settings()

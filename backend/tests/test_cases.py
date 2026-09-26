"""Contract tests for Issue #2 (create, correct, confirm, search, history) and the
identity rules from Issue #10."""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_case_service
from app.cases.service import CaseService
from app.identity import DEMO_IDENTITY_ENV
from app.main import app

AUTHOR = "demo-user-001"


@pytest.fixture
def client(monkeypatch):
    # The HTTP API has no MAX-verified identity in the bot-only MVP, so these
    # contract tests run against the development-only header, which production
    # leaves disabled.
    monkeypatch.setenv(DEMO_IDENTITY_ENV, "true")
    service = CaseService()
    app.dependency_overrides[get_case_service] = lambda: service
    with TestClient(app, headers={"X-Demo-User-Id": AUTHOR}) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _payload(**overrides):
    body = {
        "source": {
            "id": "demo-message-001",
            "type": "max_message",
            "text": "На Н-204 выросла вибрация. Подтянули крепление муфты — вибрация ушла.",
            "author_id": AUTHOR,
            "received_at": "2026-09-21T09:00:00Z",
            "external_event_id": "max-evt-001",
        },
        "equipment": {"id": "eq-204"},
        "symptom": "Повышенная вибрация",
        "action": "Подтянули крепление муфты",
        "result": "Вибрация исчезла",
    }
    body.update(overrides)
    return body


def _create(client, **overrides):
    return client.post("/api/cases", json=_payload(**overrides))


def test_create_case_returns_a_draft_with_unknown_facts_as_null(client):
    response = _create(client)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["cause"] is None
    assert body["participant"] is None
    assert body["confirmed_by"] is None
    assert body["confirmed_at"] is None
    assert body["equipment"] == {"id": "eq-204", "label": None}


def test_create_case_replay_is_idempotent(client):
    first = _create(client)
    second = _create(client)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_create_case_rejects_naive_received_at(client):
    payload = _payload()
    payload["source"]["received_at"] = "2026-09-21T09:00:00"
    response = client.post("/api/cases", json=payload)
    assert response.status_code == 422


def test_update_draft_case_sets_cause_independently_of_action(client):
    case_id = _create(client).json()["id"]
    response = client.patch(f"/api/cases/{case_id}", json={"cause": "Ослабло крепление муфты"})
    assert response.status_code == 200
    body = response.json()
    assert body["cause"] == "Ослабло крепление муфты"
    assert body["action"] == "Подтянули крепление муфты"


def test_update_names_a_participant_distinct_from_the_source_author(client):
    case_id = _create(client).json()["id"]
    response = client.patch(f"/api/cases/{case_id}", json={"participant": {"display_name": "Иван"}})
    assert response.status_code == 200
    body = response.json()
    assert body["participant"]["display_name"] == "Иван"
    assert body["source"]["author_id"] == AUTHOR


def test_confirm_case_rejects_insufficient_data(client):
    case_id = _create(client, equipment=None, symptom=None, action=None, result=None).json()["id"]
    response = client.post(f"/api/cases/{case_id}/confirm", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_data"


def test_confirm_case_success_records_user_and_time(client):
    case_id = _create(client).json()["id"]
    response = client.post(f"/api/cases/{case_id}/confirm", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["confirmed_by"] == AUTHOR
    assert body["confirmed_at"] is not None


def test_confirming_twice_conflicts(client):
    case_id = _create(client).json()["id"]
    client.post(f"/api/cases/{case_id}/confirm", json={})
    response = client.post(f"/api/cases/{case_id}/confirm", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "case_already_confirmed"


def test_editing_a_confirmed_case_conflicts(client):
    case_id = _create(client).json()["id"]
    client.post(f"/api/cases/{case_id}/confirm", json={})
    response = client.patch(f"/api/cases/{case_id}", json={"cause": "x"})
    assert response.status_code == 409


def test_search_returns_only_confirmed_cases_for_the_equipment(client):
    draft_id = _create(
        client, source={**_payload()["source"], "id": "m-draft", "external_event_id": "evt-draft"}
    ).json()["id"]
    confirmed_id = _create(
        client,
        source={**_payload()["source"], "id": "m-confirmed", "external_event_id": "evt-confirmed"},
    ).json()["id"]
    client.post(f"/api/cases/{confirmed_id}/confirm", json={})

    response = client.get("/api/cases", params={"equipment_id": "eq-204"})
    assert response.status_code == 200
    items = response.json()["items"]
    ids = [item["case"]["id"] for item in items]
    assert confirmed_id in ids
    assert draft_id not in ids
    assert all(item["similarity_score"] is None for item in items)
    assert all(item["match_explanation"] is None for item in items)


def test_search_by_problem_returns_score_and_explanation(client):
    case_id = _create(client).json()["id"]
    client.post(f"/api/cases/{case_id}/confirm", json={})

    response = client.get("/api/cases", params={"q": "сильно вибрирует"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["case"]["id"] for item in items] == [case_id]
    assert 0 < items[0]["similarity_score"] <= 1
    assert "вибрирует" in items[0]["match_explanation"]

    unknown = client.get("/api/cases", params={"q": "течёт кровля"})
    assert unknown.json()["items"] == []


def test_equipment_history_lists_confirmed_cases(client):
    case_id = _create(client).json()["id"]
    client.post(f"/api/cases/{case_id}/confirm", json={})

    response = client.get("/api/equipment/eq-204/history")
    assert response.status_code == 200
    body = response.json()
    assert body["equipment_id"] == "eq-204"
    assert any(item["id"] == case_id for item in body["items"])


def test_get_missing_case_returns_404_with_the_shared_error_shape(client):
    response = client.get("/api/cases/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "case_not_found"


# --- Issue #10: identity and minimal rights ---------------------------------


def test_without_an_accepted_identity_every_case_route_is_refused(client):
    case_id = _create(client).json()["id"]
    anonymous = TestClient(app)

    refused = [
        anonymous.post("/api/cases", json=_payload()),
        anonymous.get("/api/cases"),
        anonymous.get(f"/api/cases/{case_id}"),
        anonymous.patch(f"/api/cases/{case_id}", json={"cause": "x"}),
        anonymous.post(f"/api/cases/{case_id}/confirm", json={}),
        anonymous.get("/api/equipment/eq-204/history"),
    ]

    assert [r.status_code for r in refused] == [401] * 6
    assert refused[0].json()["error"]["code"] == "identity_not_verified"


def test_demo_header_is_ignored_unless_explicitly_enabled(client, monkeypatch):
    case_id = _create(client).json()["id"]
    monkeypatch.delenv(DEMO_IDENTITY_ENV, raising=False)

    response = client.post(f"/api/cases/{case_id}/confirm", json={})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "identity_not_verified"


def test_another_user_cannot_correct_or_confirm_someone_elses_draft(client):
    case_id = _create(client).json()["id"]
    stranger = {"X-Demo-User-Id": "someone-else"}

    correction = client.patch(f"/api/cases/{case_id}", json={"cause": "x"}, headers=stranger)
    confirmation = client.post(f"/api/cases/{case_id}/confirm", json={}, headers=stranger)

    assert correction.status_code == 403
    assert confirmation.status_code == 403
    assert confirmation.json()["error"]["code"] == "forbidden"
    # The draft is untouched and still confirmable by its author.
    assert client.get(f"/api/cases/{case_id}").json()["status"] == "draft"


def test_the_author_may_correct_and_confirm_their_own_draft(client):
    case_id = _create(client).json()["id"]

    correction = client.patch(f"/api/cases/{case_id}", json={"cause": "Ослабло крепление"})
    confirmation = client.post(f"/api/cases/{case_id}/confirm", json={})

    assert correction.status_code == 200
    assert confirmation.status_code == 200
    assert confirmation.json()["confirmed_by"] == AUTHOR


def test_health_stays_open_without_identity():
    anonymous = TestClient(app)

    assert anonymous.get("/api/health").status_code == 200

"""Contract tests for Issue #2: create, correct, confirm, search, and history."""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_case_service
from app.cases.service import CaseService
from app.main import app


@pytest.fixture
def client():
    service = CaseService()
    app.dependency_overrides[get_case_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _payload(**overrides):
    body = {
        "source": {
            "id": "demo-message-001",
            "type": "max_message",
            "text": "На Н-204 выросла вибрация. Подтянули крепление муфты — вибрация ушла.",
            "author_id": "demo-user-001",
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
    assert body["source"]["author_id"] == "demo-user-001"


def test_confirm_case_requires_a_user_header(client):
    case_id = _create(client).json()["id"]
    response = client.post(f"/api/cases/{case_id}/confirm", json={})
    assert response.status_code == 422


def test_confirm_case_rejects_insufficient_data(client):
    case_id = _create(client, equipment=None, symptom=None, action=None, result=None).json()["id"]
    response = client.post(
        f"/api/cases/{case_id}/confirm", json={}, headers={"X-Demo-User-Id": "u1"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_data"


def test_confirm_case_success_records_user_and_time(client):
    case_id = _create(client).json()["id"]
    response = client.post(
        f"/api/cases/{case_id}/confirm", json={}, headers={"X-Demo-User-Id": "u1"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["confirmed_by"] == "u1"
    assert body["confirmed_at"] is not None


def test_confirming_twice_conflicts(client):
    case_id = _create(client).json()["id"]
    client.post(f"/api/cases/{case_id}/confirm", json={}, headers={"X-Demo-User-Id": "u1"})
    response = client.post(
        f"/api/cases/{case_id}/confirm", json={}, headers={"X-Demo-User-Id": "u1"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "case_already_confirmed"


def test_editing_a_confirmed_case_conflicts(client):
    case_id = _create(client).json()["id"]
    client.post(f"/api/cases/{case_id}/confirm", json={}, headers={"X-Demo-User-Id": "u1"})
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
    client.post(f"/api/cases/{confirmed_id}/confirm", json={}, headers={"X-Demo-User-Id": "u1"})

    response = client.get("/api/cases", params={"equipment_id": "eq-204"})
    assert response.status_code == 200
    ids = [item["case"]["id"] for item in response.json()["items"]]
    assert confirmed_id in ids
    assert draft_id not in ids


def test_equipment_history_lists_confirmed_cases(client):
    case_id = _create(client).json()["id"]
    client.post(f"/api/cases/{case_id}/confirm", json={}, headers={"X-Demo-User-Id": "u1"})

    response = client.get("/api/equipment/eq-204/history")
    assert response.status_code == 200
    body = response.json()
    assert body["equipment_id"] == "eq-204"
    assert any(item["id"] == case_id for item in body["items"])


def test_get_missing_case_returns_404_with_the_shared_error_shape(client):
    response = client.get("/api/cases/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "case_not_found"

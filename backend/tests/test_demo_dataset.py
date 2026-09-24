"""The synthetic dataset stays consistent and loads through the case rules (Issue #3)."""

import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ai.dataset import MESSAGES_FILE, QUERIES_FILE, default_data_dir, load_dataset
from app.ai.seed import seed
from app.cases.models import Source
from app.cases.service import CaseService


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


def test_dataset_is_marked_synthetic(dataset):
    assert "СИНТЕТИЧЕСКИЕ" in dataset.messages.notice
    assert "СИНТЕТИЧЕСКИЕ" in dataset.queries.notice


def test_every_message_is_a_valid_contract_source(dataset):
    for message in dataset.messages.messages:
        Source(
            id=message.source_id,
            type="manual",
            text=message.text,
            author_id=message.author_id,
            received_at=datetime.fromisoformat(message.received_at),
        )


def test_dataset_covers_the_required_situations(dataset):
    messages = dataset.messages.messages
    assert 15 <= len(messages) <= 20
    assert len(dataset.messages.equipment) >= 3
    assert len({m.author_id for m in messages}) >= 3

    tags = Counter(tag for m in messages for tag in m.tags)
    for tag in ("incomplete", "negation", "unknown_equipment", "injection", "draft_only"):
        assert tags[tag] >= 1, tag
    assert tags["same_symptom_diff_cause"] >= 4

    # Same symptom, different causes: at least one equipment has several confirmed
    # cases whose causes differ.
    causes_by_equipment: dict[str, set[str | None]] = {}
    for m in messages:
        if m.load == "confirmed":
            causes_by_equipment.setdefault(m.expected.equipment_id, set()).add(m.expected.cause)
    assert any(len(causes) >= 2 for causes in causes_by_equipment.values())

    categories = Counter(q.category for q in dataset.queries.queries)
    assert categories["no_answer"] >= 2
    assert categories["draft_only"] >= 1
    assert sum(q.measurement_task for q in dataset.queries.queries) == 5


def test_messages_without_a_fact_keep_it_null(dataset):
    by_id = dataset.messages.by_source_id()
    # Injected instructions and "не помогло" must not turn into a stated cause.
    assert by_id["demo-src-13"].expected.cause is None
    assert by_id["demo-src-06"].expected.cause is None
    # Chatter is not a repair: nothing to extract.
    chatter = by_id["demo-src-18"].expected
    assert all(value is None for value in chatter.model_dump().values())


def test_seed_loads_confirmed_cases_and_keeps_drafts_out_of_search(dataset):
    service = CaseService()
    report = seed(service, dataset.messages)

    loads = Counter(m.load for m in dataset.messages.messages)
    assert report.created == loads["confirmed"] + loads["draft"]
    assert report.confirmed == loads["confirmed"]
    assert report.skipped == loads["none"]

    found = service.search(q=None, equipment_id=None, limit=100)
    assert {r.case.source.id for r in found} == {
        m.source_id for m in dataset.messages.messages if m.load == "confirmed"
    }
    assert all(r.case.is_demo for r in found)


def test_seed_twice_creates_no_duplicates(dataset):
    service = CaseService()
    seed(service, dataset.messages)
    again = seed(service, dataset.messages)

    assert again.created == 0
    assert again.confirmed == 0
    found = service.search(q=None, equipment_id=None, limit=100)
    assert len(found) == len([m for m in dataset.messages.messages if m.load == "confirmed"])


def test_query_expecting_an_unconfirmed_case_is_rejected(tmp_path: Path):
    for name in (MESSAGES_FILE, QUERIES_FILE):
        shutil.copy(default_data_dir() / name, tmp_path / name)
    queries = json.loads((tmp_path / QUERIES_FILE).read_text(encoding="utf-8"))
    # demo-src-20 is a draft: search can never return it.
    queries["queries"][0]["expected_source_ids"] = ["demo-src-20"]
    (tmp_path / QUERIES_FILE).write_text(json.dumps(queries), encoding="utf-8")

    with pytest.raises(ValidationError, match="demo-src-20"):
        load_dataset(tmp_path)


def test_missing_dataset_folder_names_the_fix(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="--data-dir"):
        load_dataset(tmp_path)

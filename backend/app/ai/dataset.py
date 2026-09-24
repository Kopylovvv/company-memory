"""Synthetic demo and evaluation dataset (Issue #3).

The files live in the repository's `data/` folder, outside the backend
package, because they are shared inputs for extraction (#6), search (#8) and
the demo measurement (#13), not application code. This module only reads and
cross-checks them; `app.ai.seed` loads them into a case store.

Expected results reference `source.id` values, never case ids: case ids are
assigned by the server at creation time and differ between runs.
"""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.cases.models import Equipment, Participant

DATA_DIR_ENV = "DEMO_DATA_DIR"
MESSAGES_FILE = "demo_messages.json"
QUERIES_FILE = "search_queries.json"

# How a message enters the case store when the demo base is seeded:
# - confirmed: part of the confirmed experience that search works over;
# - draft: stored but never confirmed, so search must not return it;
# - none: extraction-only (injection, chatter) or the live demo message.
LoadMode = Literal["confirmed", "draft", "none"]
QueryPhase = Literal["seed", "after_live"]


class DatasetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedFields(DatasetModel):
    """Reference extraction. `null` means the message does not state the fact."""

    equipment_id: str | None
    symptom: str | None
    cause: str | None
    action: str | None
    result: str | None
    participant_id: str | None


class DemoMessage(DatasetModel):
    source_id: str = Field(min_length=1)
    author_id: str = Field(min_length=1)
    received_at: str
    text: str = Field(min_length=1)
    load: LoadMode
    tags: tuple[str, ...]
    expected: ExpectedFields


class DemoMessages(DatasetModel):
    notice: str
    equipment: tuple[Equipment, ...]
    participants: tuple[Participant, ...]
    messages: tuple[DemoMessage, ...]

    @model_validator(mode="after")
    def _check_references(self) -> "DemoMessages":
        _require_unique("equipment id", [e.id for e in self.equipment])
        _require_unique("participant id", [p.id for p in self.participants])
        _require_unique("source id", [m.source_id for m in self.messages])
        equipment_ids = {e.id for e in self.equipment}
        participant_ids = {p.id for p in self.participants}
        for message in self.messages:
            expected = message.expected
            if expected.equipment_id is not None and expected.equipment_id not in equipment_ids:
                raise ValueError(f"{message.source_id}: unknown equipment {expected.equipment_id}")
            if (
                expected.participant_id is not None
                and expected.participant_id not in participant_ids
            ):
                raise ValueError(
                    f"{message.source_id}: unknown participant {expected.participant_id}"
                )
            if message.load == "confirmed" and not _confirmable(expected):
                raise ValueError(
                    f"{message.source_id}: a confirmed case needs equipment and at least one "
                    "of symptom, action, result (the server's confirmation rule)"
                )
        return self

    def by_source_id(self) -> dict[str, DemoMessage]:
        return {m.source_id: m for m in self.messages}


class SearchQuery(DatasetModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    phase: QueryPhase
    category: str = Field(min_length=1)
    # A hit: any of these in the top results. Empty means "no confirmed experience".
    expected_source_ids: tuple[str, ...]
    # Related cases (same equipment or neighbouring topic) that are not an error to
    # show, but are not a hit either.
    acceptable_source_ids: tuple[str, ...]
    measurement_task: bool


class SearchQueries(DatasetModel):
    notice: str
    queries: tuple[SearchQuery, ...]


class Dataset(DatasetModel):
    messages: DemoMessages
    queries: SearchQueries

    @model_validator(mode="after")
    def _check_queries(self) -> "Dataset":
        _require_unique("query id", [q.id for q in self.queries.queries])
        messages = self.messages.by_source_id()
        for query in self.queries.queries:
            for source_id in query.expected_source_ids + query.acceptable_source_ids:
                if source_id not in messages:
                    raise ValueError(f"{query.id}: unknown source {source_id}")
            for source_id in query.expected_source_ids:
                load = messages[source_id].load
                # Search only sees confirmed cases; the live message becomes one during the demo.
                allowed = "confirmed" if query.phase == "seed" else "none"
                if load not in {"confirmed", allowed}:
                    raise ValueError(
                        f"{query.id}: expects {source_id}, which is loaded as {load!r} "
                        "and so can never be a search result"
                    )
        return self


def _confirmable(expected: ExpectedFields) -> bool:
    return expected.equipment_id is not None and any(
        (expected.symptom, expected.action, expected.result)
    )


def _require_unique(what: str, values: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {what}: {value}")
        seen.add(value)


def default_data_dir() -> Path:
    """`DEMO_DATA_DIR` if set, else `data/` at the repository root."""
    configured = os.environ.get(DATA_DIR_ENV)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "data"


def load_dataset(data_dir: Path | None = None) -> Dataset:
    directory = data_dir or default_data_dir()
    messages = DemoMessages.model_validate(_read_json(directory / MESSAGES_FILE))
    queries = SearchQueries.model_validate(_read_json(directory / QUERIES_FILE))
    return Dataset(messages=messages, queries=queries)


def _read_json(path: Path) -> object:
    if not path.is_file():
        raise FileNotFoundError(
            f"Dataset file {path} not found; pass --data-dir or set {DATA_DIR_ENV}."
        )
    return json.loads(path.read_text(encoding="utf-8"))

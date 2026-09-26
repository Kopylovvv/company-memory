"""Load the synthetic demo dataset into a case store (Issue #3).

Goes through `CaseServiceProtocol`, the same create/confirm rules the bot and
API use, so the seeded base cannot hold a case the server would reject. Every
case is marked `is_demo`. Running it again is safe: an existing source maps to
its existing case and an already confirmed case is left alone.

The reference fields in `data/demo_messages.json` stand in for an AI draft that
a person has already checked, which is what a confirmed case represents.

Usage from `backend/` against the database in `POSTGRES_*`:

    uv run python -m app.ai.seed [--data-dir PATH]
"""

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.ai.dataset import DemoMessage, DemoMessages, load_dataset
from app.cases.models import (
    CaseConfirmRequest,
    CaseCreateRequest,
    Equipment,
    Participant,
    ParticipantInput,
    Source,
)
from app.cases.protocol import CaseServiceProtocol
from app.identity import Actor

logger = logging.getLogger(__name__)


@dataclass
class SeedReport:
    created: int = 0
    existing: int = 0
    confirmed: int = 0
    skipped: int = 0


def seed(service: CaseServiceProtocol, messages: DemoMessages) -> SeedReport:
    equipment = {e.id: e for e in messages.equipment}
    participants = {p.id: p for p in messages.participants}
    report = SeedReport()
    for message in messages.messages:
        if message.load == "none":
            report.skipped += 1
            continue
        case, created = service.create(_create_request(message, equipment, participants))
        if created:
            report.created += 1
        else:
            report.existing += 1
        if message.load == "confirmed" and case.status == "draft":
            # A seeding author, not a MAX-verified one: the closest existing
            # actor kind is the development identity.
            author = Actor(user_id=message.author_id, verified_via="demo_header")
            service.confirm(case.id, CaseConfirmRequest(), author)
            report.confirmed += 1
    return report


def _create_request(
    message: DemoMessage,
    equipment: dict[str, Equipment],
    participants: dict[str, Participant],
) -> CaseCreateRequest:
    expected = message.expected
    participant = None
    if expected.participant_id is not None:
        known = participants[expected.participant_id]
        participant = ParticipantInput(id=known.id, display_name=known.display_name)
    return CaseCreateRequest(
        source=Source(
            id=message.source_id,
            # Not delivered by MAX, so there is no delivery id to deduplicate by;
            # the server falls back to `source.id`.
            type="manual",
            text=message.text,
            author_id=message.author_id,
            received_at=datetime.fromisoformat(message.received_at),
            external_event_id=None,
        ),
        equipment=equipment.get(expected.equipment_id) if expected.equipment_id else None,
        symptom=expected.symptom,
        cause=expected.cause,
        action=expected.action,
        result=expected.result,
        participant=participant,
        is_demo=True,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed the synthetic demo cases.")
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="Folder with the dataset JSON files."
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    dataset = load_dataset(args.data_dir)

    # Imported here so validating or testing the dataset never needs a database.
    from app.db.engine import SessionLocal
    from app.db.repository import DbCaseService

    with SessionLocal() as session:
        report = seed(DbCaseService(session), dataset.messages)
    logger.info(
        "Demo seed: %d created, %d already present, %d confirmed now, %d not loaded",
        report.created,
        report.existing,
        report.confirmed,
        report.skipped,
    )


if __name__ == "__main__":
    main()

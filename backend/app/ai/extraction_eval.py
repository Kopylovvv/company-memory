"""Measure draft extraction on the synthetic messages (Issue #6).

Compares each field with the reference in `data/demo_messages.json`:
- invented: the reference is null, the draft is not (the metric that matters
  most: the product promises not to make facts up);
- missed: the reference states a fact, the draft left it empty;
- equipment: exact id match after canonicalization;
- participant: same person (surname prefix match).
Free-text wording is not scored automatically; `format_report` lists it side
by side for a person to judge.

The set is small and synthetic, so the numbers show whether the pipeline
behaves, not how it will do on real messages.

Usage from `backend/` (one model call per message, 20 in total):

    uv run --env-file ../.env python -m app.ai.extraction_eval [--data-dir PATH]
"""

import argparse
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.ai.dataset import Dataset, DemoMessage, load_dataset
from app.ai.extraction import TEXT_FIELDS, ModelClient, extract_or_empty
from app.ai.yandex_gpt import ModelNotConfiguredError, YandexGptClient, load_yandex_gpt_settings

FIELDS = ("equipment", *TEXT_FIELDS, "participant")


@dataclass(frozen=True)
class FieldOutcome:
    name: str
    expected: str | None
    predicted: str | None

    @property
    def kind(self) -> str:
        if self.expected is None:
            return "correct_null" if self.predicted is None else "invented"
        if self.predicted is None:
            return "missed"
        if self.name == "equipment":
            return "match" if self.predicted == self.expected else "wrong"
        if self.name == "participant":
            same = self.predicted.lower()[:5] == self.expected.lower()[:5]
            return "match" if same else "wrong"
        return "filled"


@dataclass(frozen=True)
class MessageOutcome:
    message: DemoMessage
    fields: tuple[FieldOutcome, ...]
    error: str | None
    dropped: tuple[str, ...]


@dataclass(frozen=True)
class ExtractionReport:
    outcomes: tuple[MessageOutcome, ...]

    @property
    def counts(self) -> Counter[str]:
        return Counter(f.kind for o in self.outcomes for f in o.fields)

    @property
    def invented_by_field(self) -> Counter[str]:
        return Counter(f.name for o in self.outcomes for f in o.fields if f.kind == "invented")

    @property
    def errors(self) -> int:
        return sum(o.error is not None for o in self.outcomes)


def evaluate_extraction(dataset: Dataset, client: ModelClient) -> ExtractionReport:
    participants = {p.id: p.display_name for p in dataset.messages.participants}
    outcomes = []
    for message in dataset.messages.messages:
        outcome = extract_or_empty(message.text, client)
        draft, expected = outcome.draft, message.expected
        predicted = {
            "equipment": draft.equipment.id if draft.equipment else None,
            **{name: getattr(draft, name) for name in TEXT_FIELDS},
            "participant": draft.participant.display_name if draft.participant else None,
        }
        reference = {
            "equipment": expected.equipment_id,
            **{name: getattr(expected, name) for name in TEXT_FIELDS},
            "participant": participants.get(expected.participant_id or ""),
        }
        outcomes.append(
            MessageOutcome(
                message=message,
                fields=tuple(FieldOutcome(n, reference[n], predicted[n]) for n in FIELDS),
                error=outcome.error,
                dropped=outcome.draft.dropped,
            )
        )
    return ExtractionReport(outcomes=tuple(outcomes))


def format_report(report: ExtractionReport) -> str:
    counts = report.counts
    stated = sum(counts[k] for k in ("missed", "match", "wrong", "filled"))
    unstated = counts["correct_null"] + counts["invented"]
    invented = ", ".join(f"{name}: {n}" for name, n in report.invented_by_field.items()) or "нет"
    lines = [
        f"Сообщений: {len(report.outcomes)}, сбоев извлечения: {report.errors}",
        f"Выдуманные факты: {counts['invented']} из {unstated} пустых по эталону ({invented})",
        f"Пропущенные факты: {counts['missed']} из {stated} названных в сообщении",
        f"Оборудование и участник: верно {counts['match']}, неверно {counts['wrong']}",
        "",
        "| Сообщение | Поле | Эталон | Черновик | Итог |",
        "| --- | --- | --- | --- | --- |",
    ]
    for outcome in report.outcomes:
        source = outcome.message.source_id.removeprefix("demo-src-")
        if outcome.error:
            lines.append(f"| {source} | — | — | — | сбой: {outcome.error} |")
        for f in outcome.fields:
            if f.kind == "correct_null":
                continue
            lines.append(
                f"| {source} | {f.name} | {f.expected or '—'} | {f.predicted or '—'} | {f.kind} |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate AI drafts on the synthetic set.")
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    dataset = load_dataset(args.data_dir)
    try:
        settings = load_yandex_gpt_settings()
    except ModelNotConfiguredError as exc:
        raise SystemExit(str(exc)) from None
    with YandexGptClient(settings) as client:
        print(format_report(evaluate_extraction(dataset, client)))


if __name__ == "__main__":
    main()

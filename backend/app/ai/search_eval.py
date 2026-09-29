"""Measure case search on the synthetic query set (Issue #8).

Runs every query through `CaseServiceProtocol.search`, the same path the API
and bot use, over an in-memory store seeded from `data/`. Then it confirms the
live demo message and runs the `after_live` queries, which is the demo's
"new experience appears in search" moment.

Metrics, over the top `TOP_K` results:
- hit rate: queries with an expected case that return one of them;
- allowed-result rate for no-answer queries: return nothing, or only
  results listed as acceptable for them;
- unrelated: results that are neither expected nor listed as acceptable.

The set is small and written by the same team as the search, so the numbers
are a working check, not proof of quality on real messages.

Usage from `backend/`:

    uv run python -m app.ai.search_eval [--data-dir PATH]
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

from app.ai.dataset import Dataset, DemoMessages, SearchQuery, load_dataset
from app.ai.seed import seed
from app.cases.protocol import CaseServiceProtocol
from app.cases.service import CaseService

TOP_K = 3


@dataclass(frozen=True)
class QueryOutcome:
    query: SearchQuery
    returned: tuple[tuple[str, float | None], ...]

    @property
    def returned_ids(self) -> tuple[str, ...]:
        return tuple(source_id for source_id, _ in self.returned)

    @property
    def answerable(self) -> bool:
        return bool(self.query.expected_source_ids)

    @property
    def passed(self) -> bool:
        if self.answerable:
            return any(sid in self.query.expected_source_ids for sid in self.returned_ids)
        # No confirmed answer exists: anything shown must at least be acceptable.
        return not self.unrelated

    @property
    def unrelated(self) -> tuple[str, ...]:
        allowed = set(self.query.expected_source_ids) | set(self.query.acceptable_source_ids)
        return tuple(sid for sid in self.returned_ids if sid not in allowed)


@dataclass(frozen=True)
class SearchReport:
    outcomes: tuple[QueryOutcome, ...]

    def _count(self, answerable: bool) -> tuple[int, int]:
        group = [o for o in self.outcomes if o.answerable == answerable]
        return sum(o.passed for o in group), len(group)

    @property
    def hits(self) -> tuple[int, int]:
        return self._count(answerable=True)

    @property
    def honest_empty(self) -> tuple[int, int]:
        return self._count(answerable=False)

    @property
    def unrelated_results(self) -> int:
        return sum(len(o.unrelated) for o in self.outcomes)


def evaluate(dataset: Dataset) -> SearchReport:
    service = CaseService()
    seed(service, dataset.messages)
    outcomes = [_run(service, q) for q in dataset.queries.queries if q.phase == "seed"]

    seed(service, _with_live_messages_confirmed(dataset))
    outcomes += [_run(service, q) for q in dataset.queries.queries if q.phase == "after_live"]
    return SearchReport(outcomes=tuple(outcomes))


def _run(service: CaseServiceProtocol, query: SearchQuery) -> QueryOutcome:
    results = service.search(q=query.text, equipment_id=None, limit=TOP_K)
    return QueryOutcome(
        query=query,
        returned=tuple((r.case.source.id, r.similarity_score) for r in results),
    )


def _with_live_messages_confirmed(dataset: Dataset) -> DemoMessages:
    """The messages the demo confirms live, loaded as if a person had confirmed them."""
    live = {
        source_id
        for query in dataset.queries.queries
        if query.phase == "after_live"
        for source_id in query.expected_source_ids
    }
    messages = tuple(
        m.model_copy(update={"load": "confirmed"}) if m.source_id in live else m
        for m in dataset.messages.messages
    )
    return dataset.messages.model_copy(update={"messages": messages})


def format_report(report: SearchReport) -> str:
    lines = [
        f"| Запрос | Категория | Ожидается | Найдено (top-{TOP_K}) | Итог |",
        "| --- | --- | --- | --- | --- |",
    ]
    for o in report.outcomes:
        expected = ", ".join(_short(s) for s in o.query.expected_source_ids) or "пусто"
        found = ", ".join(f"{_short(s)} ({score:.2f})" for s, score in o.returned) or "пусто"
        verdict = "ok" if o.passed else "ПРОМАХ"
        if o.unrelated:
            verdict += "; лишнее: " + ", ".join(_short(s) for s in o.unrelated)
        lines.append(f"| {o.query.id} | {o.query.category} | {expected} | {found} | {verdict} |")
    hits, answerable = report.hits
    empty, unanswerable = report.honest_empty
    lines += [
        "",
        f"Попадание в top-{TOP_K}: {hits}/{answerable}",
        f"Запросы без эталонного ответа, без посторонних результатов: {empty}/{unanswerable}",
        f"Лишних результатов: {report.unrelated_results}",
    ]
    return "\n".join(lines)


def _short(source_id: str) -> str:
    return source_id.removeprefix("demo-src-")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate case search on the synthetic set.")
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    print(format_report(evaluate(load_dataset(args.data_dir))))


if __name__ == "__main__":
    main()

"""Rank confirmed cases against a free-text problem description (Issue #8).

Lexical on purpose: no model provider, no new dependency, and every score can
be explained by the words that matched. What it does:

- normalizes Russian words with a light suffix stripper, so "вибрирует" and
  "вибрировал" meet;
- maps a small maintenance thesaurus onto shared concepts, so "трясёт" finds
  "вибрация" and "буксует" finds "проскальзывание";
- recognizes equipment ids regardless of dash and Latin/Cyrillic look-alikes
  ("КЛ3" = "КЛ-3", Latin "H-204" = "Н-204");
- scores a case by the share of the query's weighted terms it contains, rarer
  terms weighing more, which gives the contract's 0..1 `similarity_score`;
- when the query names equipment (an id or a type such as "насос"), halves the
  score of cases on other equipment: a pump's bearing is weak evidence for a fan.

A case must match the problem itself (symptom, cause, action or result) and
reach `MIN_SCORE`; matching only the equipment or workshop is not "a similar
problem", and an honest empty answer beats a loosely related one. A result is
past experience to look at, never a diagnosis of the current failure.

The caller passes only cases the user may see: status and access filtering
belong to the case store, not to ranking.
"""

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.cases.models import Case, CaseSearchResult

MIN_SCORE = 0.5
OTHER_EQUIPMENT_FACTOR = 0.5

PROBLEM_FIELDS = ("symptom", "cause", "action", "result")
FIELD_NAMES = {
    "symptom": "симптом",
    "cause": "причина",
    "action": "действие",
    "result": "результат",
    "equipment": "оборудование",
}

# Concept -> word prefixes (lowercase, "ё" folded to "е"). Generic maintenance
# vocabulary, not a list tuned to the evaluation queries; extend it when a real
# phrasing is missed, and re-run the evaluation.
THESAURUS: dict[str, tuple[str, ...]] = {
    "вибрация": ("вибр", "тряс", "трясл", "биени", "бьет", "колбас"),
    "шум": ("шум", "гул", "гуд", "воет", "визж", "визг", "свист"),
    "стук": ("стук", "стуч", "лязг", "бренч"),
    "нагрев": (
        "перегрев",
        "перегре",
        "нагрев",
        "нагре",
        "грее",
        "грет",
        "грел",
        "горяч",
        "температур",
    ),
    "проскальзывание": ("проскальз", "буксу", "пробукс", "скольз"),
    "утечка": ("утеч", "утек", "теч", "текл", "текут", "подтек", "протек", "потек", "травит"),
    "давление": ("давлен",),
    "запуск": ("запуск", "запуст", "пуск", "старт"),
    "износ": ("износ", "изнош", "стерл", "стерт", "истер"),
    "засор": ("засор", "забит", "забил", "загрязн"),
    "разрыв": ("порва", "разрыв", "рвет", "порвал", "обрыв", "лопн"),
    "ослабление": ("ослаб", "разболт", "люфт", "болтает"),
    "гарь": ("гари", "гарь", "гарью", "дым", "горел", "палены", "паленн"),
    "смазка": ("смаз", "масл"),
    "напор": ("напор",),
    "подшипник": ("подшипн",),
    "электрозащита": ("автомат", "выбива", "расцепит"),
}

STOPWORDS = frozenset(
    """
    а без был была были было в во вот все всё где да даже для до его ее ей если есть
    еще ещё же за и из или им их к как когда ко кто ли либо мы на над не нет ни но
    ну о об один опять от очень по под после при про раз раньше с сегодня снова со
    так там то тоже только у уже чем что это эта этот я делали делать сделали было
    сильно почему какой какая какие который надо нужно можно был
    """.split()
)

# Longest first. Strips inflection only; derivation is the thesaurus's job.
_ENDINGS = tuple(
    sorted(
        """
        ыми ими ого его ому ему ая яя ое ее ые ие ый ий ой ую юю ым им ом ем ых их
        ется ются ится ятся атся ует уют ет ют ит ят ат ешь ишь ать ять ить еть уть ти
        ли ла ло ал ял ил ел ами ями ах ях ов ев ей ам ям ию ия ие ью ье
        а я о е ы и у ю ь л
        """.split(),
        key=len,
        reverse=True,
    )
)
_REFLEXIVE = ("ся", "сь")
_MIN_STEM = 3

_LATIN_LOOKALIKES = str.maketrans("ABCEHKMOPTX", "АВСЕНКМОРТХ")
_EQUIPMENT_ID = re.compile(r"(?<![\w-])([A-Za-zА-Яа-яЁё]{1,3})-?(\d{1,4})(?![\w-])")
_WORD = re.compile(r"[a-zа-я]+")


@dataclass(frozen=True)
class _Term:
    key: str
    surface: str


def rank_cases(
    query: str, cases: Sequence[Case], *, limit: int, min_score: float = MIN_SCORE
) -> list[CaseSearchResult]:
    """Best matching cases first; empty when nothing is similar enough."""
    query_terms = _dedupe(_terms(query))
    if not query_terms or not cases:
        return []

    indexed = [(case, _index(case)) for case in cases]
    document_frequency: dict[str, int] = {}
    for _, fields in indexed:
        for key in set().union(*fields.values()):
            document_frequency[key] = document_frequency.get(key, 0) + 1
    weights = {
        term.key: _idf(document_frequency.get(term.key, 0), len(indexed)) for term in query_terms
    }
    total = sum(weights.values())
    known_equipment_terms = set().union(*(fields["equipment"] for _, fields in indexed))
    named_equipment = {t.key for t in query_terms} & known_equipment_terms

    scored: list[tuple[float, CaseSearchResult]] = []
    for case, fields in indexed:
        matched_in: dict[str, list[str]] = {}
        matched_weight = 0.0
        for term in query_terms:
            hit_fields = [name for name, keys in fields.items() if term.key in keys]
            if not hit_fields:
                continue
            matched_weight += weights[term.key]
            for name in hit_fields:
                matched_in.setdefault(name, []).append(term.surface)
        if not any(name in PROBLEM_FIELDS for name in matched_in):
            continue
        score = matched_weight / total
        if named_equipment and not named_equipment & fields["equipment"]:
            score *= OTHER_EQUIPMENT_FACTOR
        score = round(score, 2)
        if score < min_score:
            continue
        scored.append(
            (
                score,
                CaseSearchResult(
                    case=case, similarity_score=score, match_explanation=_explain(matched_in)
                ),
            )
        )

    # Stable sort: equal scores keep the caller's order (most recent first).
    scored.sort(key=lambda item: item[0], reverse=True)
    return [result for _, result in scored[:limit]]


def _index(case: Case) -> dict[str, set[str]]:
    fields = {name: {t.key for t in _terms(getattr(case, name) or "")} for name in PROBLEM_FIELDS}
    equipment: set[str] = set()
    if case.equipment is not None:
        equipment.add(_equipment_key(case.equipment.id))
        equipment |= {t.key for t in _terms(case.equipment.label or "")}
    fields["equipment"] = equipment
    return fields


def _terms(text: str) -> list[_Term]:
    terms = [
        _Term(_equipment_key(match.group(0)), match.group(0))
        for match in _EQUIPMENT_ID.finditer(text)
    ]
    remainder = _EQUIPMENT_ID.sub(" ", text)
    for word in _WORD.findall(remainder.lower().replace("ё", "е")):
        if word in STOPWORDS or len(word) < 3:
            continue
        terms.append(_Term(_concept(word) or _stem(word), word))
    return terms


def _equipment_key(raw: str) -> str:
    compact = re.sub(r"[\s-]", "", raw).upper().replace("Ё", "Е")
    return "#" + compact.translate(_LATIN_LOOKALIKES)


def _concept(word: str) -> str | None:
    for concept, prefixes in THESAURUS.items():
        if word.startswith(prefixes):
            return "@" + concept
    return None


def _stem(word: str) -> str:
    for ending in _REFLEXIVE:
        if word.endswith(ending) and len(word) - len(ending) >= _MIN_STEM + 1:
            word = word[: -len(ending)]
            break
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= _MIN_STEM:
            return word[: -len(ending)]
    return word


def _idf(document_frequency: int, documents: int) -> float:
    # A term no case contains weighs the most: an unknown problem stays unmatched.
    return math.log(1 + (documents + 1) / (document_frequency + 1))


def _dedupe(terms: Iterable[_Term]) -> list[_Term]:
    seen: dict[str, _Term] = {}
    for term in terms:
        seen.setdefault(term.key, term)
    return list(seen.values())


def _explain(matched_in: dict[str, list[str]]) -> str:
    order = [*PROBLEM_FIELDS, "equipment"]
    parts = [
        f"{FIELD_NAMES[name]}: {', '.join(dict.fromkeys(matched_in[name]))}"
        for name in order
        if name in matched_in
    ]
    return "Совпадения — " + "; ".join(parts)

"""Shared Russian text normalization for extraction and search (Issues #6, #8).

Both need the same view of a repair message: word forms folded by a light
suffix stripper, maintenance synonyms mapped onto shared concepts, and
equipment ids recognized regardless of dash and Latin/Cyrillic look-alikes.
"""

import re
from dataclasses import dataclass

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
    "запуск": ("запуск", "запуст", "запущ", "пуск", "старт"),
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
class Term:
    key: str
    surface: str


def terms(text: str) -> list[Term]:
    terms = [
        Term(equipment_key(match.group(0)), match.group(0))
        for match in _EQUIPMENT_ID.finditer(text)
    ]
    remainder = _EQUIPMENT_ID.sub(" ", text)
    for word in _WORD.findall(remainder.lower().replace("ё", "е")):
        if word in STOPWORDS or len(word) < 3:
            continue
        terms.append(Term(_concept(word) or _stem(word), word))
    return terms


def equipment_key(raw: str) -> str:
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


def term_keys(text: str) -> set[str]:
    """Normalized content terms of a text: concepts, stems and equipment ids."""
    return {term.key for term in terms(text)}


def equipment_ids(text: str) -> list[str]:
    """Equipment ids mentioned in a text, in canonical form ("КЛ3" -> "КЛ-3")."""
    return [_canonical(match) for match in _EQUIPMENT_ID.finditer(text)]


def canonical_equipment_id(raw: str) -> str | None:
    """ "КЛ3", "кл-3", Latin "H-204" -> "КЛ-3", "Н-204"; None if `raw` is not an id."""
    match = _EQUIPMENT_ID.fullmatch(raw.strip())
    return _canonical(match) if match else None


def _canonical(match: re.Match[str]) -> str:
    letters = match.group(1).upper().replace("Ё", "Е").translate(_LATIN_LOOKALIKES)
    return f"{letters}-{match.group(2)}"

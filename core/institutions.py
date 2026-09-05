"""Canonical licensed-institution identity and provenance."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .text_norm import fold

INSTITUTION_SOURCE_URL = "https://www.bankofalbania.org/Mbikeqyrja/Subjekte_te_licencuara/"
INSTITUTION_SOURCE_TITLE = "Regjistri i subjekteve të licencuara — Banka e Shqipërisë"


@dataclass(frozen=True, slots=True)
class Institution:
    canonical_name: str
    aliases: tuple[str, ...]
    status: str = "licensed"
    source_url: str = INSTITUTION_SOURCE_URL
    source_title: str = INSTITUTION_SOURCE_TITLE
    source_as_of: str | None = None


LICENSED_INSTITUTIONS: tuple[Institution, ...] = (
    Institution("Banka Amerikane e Investimeve Shqiperi", ("BAI", "AIB", "Banka Amerikane e Investimeve")),
    Institution("Banka Credins", ("BC", "Credins")),
    Institution("Banka e Bashkuar e Shqipërisë", ("BBSH", "Banka e Bashkuar")),
    Institution("Banka e Parë e Investimeve Albania", ("BPI", "Banka e Pare e Investimeve Albania")),
    Institution("Banka Intesa SanPaolo e Shqipërisë", ("BIS", "Intesa", "Intesa Sanpaolo")),
    Institution("Banka Jet", ("JET", "Jet")),
    Institution("Banka Kombëtare Tregtare", ("BKT",)),
    Institution("Banka OTP Albania", ("OTP",)),
    Institution("Banka Procredit", ("BPC", "ProCredit")),
    Institution("Banka Raiffeisen", ("BR", "Raiffeisen")),
    Institution("Banka Tirana", ("BT", "Tirana")),
    Institution("Banka Union", ("BU", "Union")),
    Institution("Dega e Bankës Turkiye Cumhuriyeti Ziraat Bankasi A.S., Albania", ("Ziraat",)),
)

INSTITUTION_REGISTER_SOURCE: dict[str, object] = {
    "id": "boa-licensed-institutions",
    "doc": "Banka e Shqipërisë — Subjektet e licencuara",
    "title": INSTITUTION_SOURCE_TITLE,
    "url": INSTITUTION_SOURCE_URL,
    "as_of": None,
}


@lru_cache(maxsize=1)
def institution_forms() -> tuple[tuple[str, str], ...]:
    """Return normalized aliases ordered so longer forms win overlapping matches."""
    forms: dict[str, str] = {}
    for institution in LICENSED_INSTITUTIONS:
        for value in (institution.canonical_name, *institution.aliases):
            forms.setdefault(fold(value), institution.canonical_name)
    return tuple(sorted(forms.items(), key=lambda item: (-len(item[0]), item[0])))


def institution_match_terms() -> tuple[str, ...]:
    return tuple(form for form, _canonical in institution_forms())


def resolve_institutions(text: str) -> tuple[str, ...]:
    folded = fold(text)
    matches: list[str] = []
    for form, canonical_name in institution_forms():
        if re.search(rf"(?<!\w){re.escape(form)}(?!\w)", folded):
            if canonical_name not in matches:
                matches.append(canonical_name)
    return tuple(matches)


def canonicalize_institution(text: str) -> str | None:
    matches = resolve_institutions(text)
    return matches[0] if len(matches) == 1 else None


def institution_catalog() -> tuple[Institution, ...]:
    return LICENSED_INSTITUTIONS


def institution_catalog_message() -> str:
    names = ", ".join(item.canonical_name for item in institution_catalog())
    return (
        "Bankat e licencuara dhe dega e bankës së huaj në Shqipëri janë: " + names + ". Burimi: "
        + INSTITUTION_SOURCE_TITLE + "."
    )

"""Albanian entity inflection must not weaken exact identity comparison."""

import pytest

from voice.shared.fidelity_guard import FidelityGuard


@pytest.mark.parametrize("form", ("bankën", "bankës"))
def test_bank_inflection_matches_same_nominative_entity(form: str) -> None:
    sentence = f"{form} Credins"
    assert FidelityGuard().extract_entities(sentence) == (f"{form} Credins",)
    result = FidelityGuard().verify(
        sentence,
        ("Banka Credins",),
    )
    assert result.approved


def test_bank_inflection_rejects_different_bank() -> None:
    sentence = "bankën Raiffeisen"
    assert FidelityGuard().extract_entities(sentence) == ("bankën Raiffeisen",)
    result = FidelityGuard().verify(
        sentence,
        ("Banka Credins",),
    )
    assert not result.approved


def test_document_inflection_matches_same_nominative_entity() -> None:
    sentence = "rregulloren Drita"
    assert FidelityGuard().extract_entities(sentence) == ("rregulloren Drita",)
    result = FidelityGuard().verify(
        sentence,
        ("Rregullorja Drita",),
    )
    assert result.approved


def test_document_inflection_rejects_different_document() -> None:
    sentence = "rregullores Hëna"
    assert FidelityGuard().extract_entities(sentence) == ("rregullores Hëna",)
    result = FidelityGuard().verify(
        sentence,
        ("Rregullorja Drita",),
    )
    assert not result.approved


def test_generic_regulation_owned_by_bank_is_not_a_document_title_entity() -> None:
    sentence = "rregulloren e Bankës së Shqipërisë për administrimin e rrezikut"
    assert FidelityGuard().extract_entities(sentence) == ()


def test_regulation_number_matches_ocr_document_marker() -> None:
    sentence = "Versioni i integruar i Rregullores Nr. 62 është ndryshuar në vitin 2022."
    evidence = (
        "RREG 62 date 14 09 2011 P R ADM E RREZIKUT T KREDIS "
        "e ndryshuar 1 1 2022 doc 201 — Neni 22"
    )
    assert FidelityGuard().verify(sentence, (evidence,)).approved

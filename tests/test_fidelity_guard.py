"""Fidelity-guard regression tests for table-derived fee answers (2026-08-30)."""

import pytest

from voice.shared.fidelity_guard import FidelityGuard

GUARD = FidelityGuard()


def evidence(passages: list[str]) -> tuple[dict, ...]:
    return tuple({
        "doc": "Rregullore_Nr_59_2008_Mbi_transparencen_per_produktet_bankare_e_financiare_30366.pdf",
        "article": "Neni 3",
        "passage_text": passage,
    } for passage in passages)


# A genuine fee-table chunk: column header carries the service; rows are per-bank
# "Banka X: value" lines (the exact shape that used to fail label-subset).
CASH_TABLE = (
    "Karte debiti\n"
    "Terheqje Cash nga terminalet e bankave te tjera MIN\n"
    "Banka Amerikane e Investimeve Shqiperi: 2.00\n"
    "Banka Credins: 3.50\n"
    "Banka Union: 2.00\n"
    "Banka OTP Albania: 2.00\n"
)

TRUE_CASH_SENTENCE = (
    "Sipas tabelave të publikuara, Banka Union aplikon një komision prej 2.00 "
    "për tërheqje cash me kartë debiti nga terminalet e bankave të tjera."
)


@pytest.mark.parametrize("sentence", [
    TRUE_CASH_SENTENCE,
    "Banka OTP Albania aplikon një komision prej 2.00 për tërheqje cash me kartë debiti nga terminalet e bankave të tjera.",
    # value + bank pair is what binds; generic claim-frame words are tolerated
    "Komisioni për tërheqje cash nga bankat e tjera është 2.00 te Banka Union.",
])
def test_true_table_fee_sentence_approves(sentence) -> None:
    verdict = GUARD.verify_sources(sentence, evidence([CASH_TABLE]))
    assert verdict.approved, verdict.reason


def test_fabricated_figure_is_dropped() -> None:
    sentence = TRUE_CASH_SENTENCE.replace("2.00", "99.99")
    verdict = GUARD.verify_sources(sentence, evidence([CASH_TABLE]))
    assert not verdict.approved
    assert "mismatch" in verdict.reason


def test_wrong_bank_is_dropped() -> None:
    # Credins row is 3.50; claiming 2.00 (Union's value) for Credins must fail
    # the value binding, and claiming a Credins row at all must fail bank binding.
    sentence = (
        "Sipas tabelave, Banka Credins aplikon një komision prej 2.00 për tërheqje "
        "cash me kartë debiti nga terminalet e bankave të tjera."
    )
    verdict = GUARD.verify_sources(sentence, evidence([CASH_TABLE]))
    assert not verdict.approved
    assert "mismatch" in verdict.reason


def test_wrong_service_is_dropped() -> None:
    # "pagesa pos" service wording absent from the chunk vocabulary -> drop
    sentence = (
        "Sipas tabelave, Banka Union aplikon një komision prej 2.00 për pagesa POS."
    )
    verdict = GUARD.verify_sources(sentence, evidence([CASH_TABLE]))
    assert not verdict.approved
    assert "mismatch" in verdict.reason


def test_figure_absent_from_evidence_is_dropped() -> None:
    sentence = (
        "Sipas tabelave, Banka Union aplikon një komision prej 5.00 për tërheqje "
        "cash me kartë debiti nga terminalet e bankave të tjera."
    )
    verdict = GUARD.verify_sources(sentence, evidence([CASH_TABLE]))
    assert not verdict.approved


def test_plain_kredimarrje_answer_without_claims_stays_open() -> None:
    # No numeric claim, no entity -> approve (dialog-style sentence)
    verdict = GUARD.verify_sources(
        "Faleminderit, ky informacion më ndihmoi.", evidence([CASH_TABLE]),
    )
    assert verdict.approved


def test_prose_claim_against_chunk_still_bound() -> None:
    # Non-table evidence: prose chunk with clear label; sentence must not invent
    # a figure: 2.00 is absent from the prose -> drop
    prose = (
        "Sipas rregullores, komisioni për tërheqje cash në banka të tjera "
        "mbulohet nga transparenca e produkteve bankare. Banka Union publikon "
        "tarifat komerciale për kartat e debitit."
    )
    sentence = (
        "Sipas rregullores, komisioni për tërheqje cash është 2.00 për Banka Union."
    )
    verdict = GUARD.verify_sources(sentence, evidence([prose]))
    assert not verdict.approved
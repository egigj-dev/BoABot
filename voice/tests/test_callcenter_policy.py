"""Deterministic call-center routes for user-suite safety and session prompts."""

import numpy as np

import core.callcenter as callcenter

from core.callcenter import Outcome, decide


def test_repeat_phrases_cover_requested_albanian_forms() -> None:
    previous = "Përgjigjja e mëparshme."
    for question in ("Ma thuaj edhe një herë.", "Përsërit përgjigjen e parë."):
        decision = decide(question, previous, [])
        assert decision.outcome is Outcome.REPEAT
        assert decision.message == previous


def test_repeat_without_previous_answer_uses_empty_session_message() -> None:
    decision = decide("Përsërit përgjigjen e parë.", "", [])
    assert decision.outcome is Outcome.REPEAT
    assert "Nuk kam ende" in decision.message


def test_disclosed_pin_and_otp_are_handoffs_marked_redacted() -> None:
    for question in (
        "PIN-i im 4821 nuk funksionon; ma rregulloni.",
        "Ia tregova dikujt OTP-në 654321 dhe tani kam frikë.",
    ):
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.HANDOFF
        assert decision.handoff
        assert decision.pii_redacted
        assert not decision.question


def test_business_current_account_rates_reach_measured_retrieval_gate() -> None:
    decision = decide(
        "Cilat janë normat e interesit për llogaritë rrjedhëse të korporatave?",
        "",
        [],
    )
    assert decision.outcome is None


def test_deictic_regulation_without_session_context_reaches_retrieval() -> None:
    decision = decide(
        "A është norma e interesit 4.75% apo 4,75% te kjo rregullore?",
        "",
        [],
    )
    assert decision.outcome is None


def test_coverage_is_not_hardcoded_in_the_router() -> None:
    for question in (
        "Cila është banka më e mirë në Tiranë?",
        "Si mund të deklaroj qiranë te tatimet?",
    ):
        assert decide(question, "", []).outcome is None


def test_generic_card_maintenance_question_clarifies_card_type_and_segment() -> None:
    decision = decide(
        "Sa kushton mirëmbajtja e kartës te Raiffeisen?", "", []
    )
    assert decision.outcome is Outcome.CLARIFY
    assert decision.question == "Sa kushton mirëmbajtja e kartës te Raiffeisen?"
    assert "debiti apo krediti" in decision.message


def test_rewritten_card_question_with_multiple_choices_remains_ambiguous() -> None:
    decision = decide(
        "Sa kushton mirëmbajtja e kartës së debitit apo kreditit për individ ose biznes te BKT?",
        "",
        [],
    )
    assert decision.outcome is Outcome.CLARIFY


def test_classifier_verdict_is_not_bypassed_by_pricing_shape(monkeypatch) -> None:
    monkeypatch.setattr(callcenter, "_encode_question", lambda _text: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _embedding: 1.0)
    history = [{
        "role": "user",
        "content": "Sa kushton mirëmbajtja e kartës te Raiffeisen?",
    }]
    decision = decide("Po te BKT?", "", history)
    assert decision.outcome is Outcome.HANDOFF

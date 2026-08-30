"""Safety precedence and privacy regressions for the call-center router."""

import re

import numpy as np
import pytest

import core.callcenter as callcenter
from core.callcenter import DecisionReason, Outcome, decide


def _positive_classifier(monkeypatch) -> None:
    monkeypatch.setattr(callcenter, "_encode_question", lambda _text: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _embedding: None)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _embedding: 1.0)


def test_price_shaped_account_questions_cannot_bypass_classifier(monkeypatch) -> None:
    _positive_classifier(monkeypatch)
    for question in (
        "Sa është gjendja e llogarisë sime në bankë?",
        "Sa është limiti i kartës sime të kreditit?",
    ):
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.HANDOFF
        assert decision.reason is DecisionReason.SEMANTIC_ACCOUNT_ACTION


def test_exported_classifier_routes_the_two_audited_account_probes() -> None:
    for question in (
        "Sa është gjendja e llogarisë sime në bankë?",
        "Sa është limiti i kartës sime të kreditit?",
    ):
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.HANDOFF
        assert decision.reason is DecisionReason.SEMANTIC_ACCOUNT_ACTION


def test_pan_is_redacted_before_card_disambiguation(monkeypatch) -> None:
    _positive_classifier(monkeypatch)
    decision = decide(
        "Sa kushton mirëmbajtja e kartës 4111111111111111 te BKT?", "", [],
    )
    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason is DecisionReason.PII_DETECTED
    assert not re.search(r"\d{8,}", decision.question)


def test_account_action_outranks_business_deposit_coverage(monkeypatch) -> None:
    _positive_classifier(monkeypatch)
    decision = decide(
        "Mbylle llogarinë time të depozitës së biznesit.", "", [],
    )
    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason is DecisionReason.SEMANTIC_ACCOUNT_ACTION


def test_repeat_preserves_a_pending_transfer_flag() -> None:
    decision = decide(
        "Përsërite.", "Po jua kaloj një agjenti.", [],
        Outcome.HANDOFF, True,
    )
    assert decision.outcome is Outcome.REPEAT
    assert decision.handoff


def test_repeat_word_requires_a_word_boundary() -> None:
    assert not callcenter._is_repeat("repeatedly")
    assert callcenter._is_repeat("repeat")


@pytest.mark.parametrize(
    ("question", "reason", "handoff"),
    (
        (
            "OTP qe me derguat eshte 99120",
            DecisionReason.CREDENTIAL_DISCLOSURE,
            True,
        ),
        (
            "OTP që më dërguat është 99120",
            DecisionReason.CREDENTIAL_DISCLOSURE,
            True,
        ),
        (
            "sa eshte komisioni i dergimit te parave?",
            DecisionReason.DENSE_RETRIEVAL,
            False,
        ),
    ),
)
def test_credential_fast_path_send_verb_floor(
        monkeypatch, question, reason, handoff) -> None:
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _e: None)

    decision = decide(question, "", [])
    assert decision.reason is reason
    assert decision.handoff is handoff


def test_deictic_bare_np_after_rate_clarify_stays_informational(
        monkeypatch) -> None:
    first_question = "Sa eshte komisioni i mirembajtjes se karta?"
    continuation = "Per karte debiti, per person fizik"
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(
        callcenter, "_classify_turn",
        lambda question, *_a: (
            "clarify" if "mirembajtjes" in callcenter.fold(question) else "answer"
        ),
    )
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: 1.0)

    first = decide(first_question, "", [])
    second = decide(
        continuation, first.message,
        [
            {"role": "user", "content": first_question},
            {"role": "assistant", "content": first.message},
        ],
        last_outcome=first.outcome,
    )

    assert first.outcome is Outcome.CLARIFY
    assert second.outcome is None
    assert second.reason is DecisionReason.DENSE_RETRIEVAL
    assert not second.handoff


def test_bare_np_after_incident_handoff_still_reaches_backstop(
        monkeypatch) -> None:
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: 1.0)

    decision = decide(
        "Per karte debiti", "Po jua kaloj biseden nje agjenti.",
        [
            {"role": "user", "content": "Me humbi karta."},
            {"role": "assistant", "content": "Po jua kaloj biseden nje agjenti."},
        ],
        last_outcome=Outcome.HANDOFF, last_handoff=True,
    )

    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason is DecisionReason.INCIDENT_BACKSTOP


def test_incident_bare_np_does_not_enter_informational_floor(monkeypatch) -> None:
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: 1.0)

    decision = decide(
        "Humbi kartela", "Mund ta sqaroni pak pyetjen?",
        [
            {"role": "user", "content": "Sa eshte komisioni i kartes?"},
            {"role": "assistant", "content": "Mund ta sqaroni pak pyetjen?"},
        ],
        last_outcome=Outcome.CLARIFY,
    )

    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason is DecisionReason.INCIDENT_BACKSTOP

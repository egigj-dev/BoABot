"""Safety precedence and privacy regressions for the call-center router."""

import re

import numpy as np

import core.callcenter as callcenter
from core.callcenter import Outcome, decide


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
        assert decision.reason == "account_action"


def test_exported_classifier_routes_the_two_audited_account_probes() -> None:
    for question in (
        "Sa është gjendja e llogarisë sime në bankë?",
        "Sa është limiti i kartës sime të kreditit?",
    ):
        decision = decide(question, "", [])
        assert decision.outcome is Outcome.HANDOFF
        assert decision.reason == "account_action"


def test_pan_is_redacted_before_card_disambiguation(monkeypatch) -> None:
    _positive_classifier(monkeypatch)
    decision = decide(
        "Sa kushton mirëmbajtja e kartës 4111111111111111 te BKT?", "", [],
    )
    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason == "pii"
    assert not re.search(r"\d{8,}", decision.question)


def test_account_action_outranks_business_deposit_coverage(monkeypatch) -> None:
    _positive_classifier(monkeypatch)
    decision = decide(
        "Mbylle llogarinë time të depozitës së biznesit.", "", [],
    )
    assert decision.outcome is Outcome.HANDOFF
    assert decision.reason == "account_action"


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

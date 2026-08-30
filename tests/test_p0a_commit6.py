"""P0A commit 6: deterministic personal-record capability boundary."""

from types import SimpleNamespace

import numpy as np
import pytest

import core.callcenter as callcenter
from core.callcenter import DecisionReason, Outcome, decide


PERSONAL_RECORD_QUESTIONS = (
    "a kam kredi aktive?",
    "a kam kredi te keqija ne emrin tim?",
    "a nuk kam kredi?",
    "a figuroj pa kredi ne regjister?",
    "nuk kam kredi, apo jo?",
    "a kam kredi ne emrin tim?",
)


@pytest.mark.parametrize(
    "analysis",
    (
        None,
        SimpleNamespace(label="catalog"),
        SimpleNamespace(label="incident"),
    ),
)
@pytest.mark.parametrize("question", PERSONAL_RECORD_QUESTIONS)
def test_personal_record_preflight_is_router_agnostic(
        monkeypatch, analysis, question) -> None:
    monkeypatch.setattr(
        callcenter, "_analyze_turn", lambda *_args, **_kwargs: analysis,
    )

    decision = decide(question, "", [])

    assert decision.reason is DecisionReason.PERSONAL_RECORD_CAPABILITY_BOUNDARY
    assert decision.outcome is Outcome.ANSWER
    assert decision.handoff is False
    assert decision.message == callcenter.PERSONAL_RECORD_CAPABILITY_MESSAGE
    assert decision.message != callcenter.LEGAL_ADVICE_MESSAGE


@pytest.mark.parametrize(
    "question",
    (
        "ka kredi aktive per mua ne regjistrin e kredive?",
        "dua informacionin tim per kredi problematike",
    ),
)
def test_contextual_personal_record_forms_require_registry_vocabulary(
        monkeypatch, question) -> None:
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)

    decision = decide(question, "", [])

    assert decision.reason is DecisionReason.PERSONAL_RECORD_CAPABILITY_BOUNDARY


@pytest.mark.parametrize(
    ("question", "expected_reason"),
    (
        ("a duhet ta paguaj kete kredi?", DecisionReason.LEGAL_ADVICE_EXPLICIT),
        ("nuk kam kredi.", DecisionReason.DENSE_RETRIEVAL),
        ("cfare thote rregullorja per mua si garant?", DecisionReason.DENSE_RETRIEVAL),
        ("a mund te marr nje kredi?", DecisionReason.DENSE_RETRIEVAL),
        ("mbyll llogarine ne emrin tim", DecisionReason.SEMANTIC_ACCOUNT_ACTION),
    ),
)
def test_non_personal_record_precedence(
        monkeypatch, question, expected_reason) -> None:
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _e: None)
    if expected_reason is DecisionReason.SEMANTIC_ACCOUNT_ACTION:
        monkeypatch.setattr(
            callcenter, "_classify_turn", lambda *_a, **_k: "account_action",
        )

    decision = decide(question, "", [])

    assert decision.reason is expected_reason
    assert decision.reason is not DecisionReason.PERSONAL_RECORD_CAPABILITY_BOUNDARY


def test_incident_with_personal_record_phrase_is_ceded(monkeypatch) -> None:
    monkeypatch.setattr(
        callcenter, "_analyze_turn",
        lambda *_a, **_k: SimpleNamespace(label="incident"),
    )

    decision = decide("me humbi karta ne emrin tim", "", [])

    assert decision.reason is DecisionReason.SEMANTIC_INCIDENT
    assert decision.outcome is Outcome.HANDOFF

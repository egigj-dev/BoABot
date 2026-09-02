"""Regression coverage for the deterministic transfer-fee amount seam."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

import core.api as api
import core.callcenter as callcenter


def _events(response):
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _done(response):
    return next(event for event in _events(response) if event["type"] == "done")


@pytest.fixture
def deterministic(monkeypatch):
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _e: None)


@pytest.mark.parametrize("question", [
    "Cilat janë tarifat për transfertat bankare?",
    "Cilat jane tarifat per transfertat bankare?",
])
def test_ambiguous_transfer_fee_clarifies_without_retrieval(
        deterministic, monkeypatch, question):
    store = callcenter.SessionStore()
    monkeypatch.setattr(api, "sessions", store)
    monkeypatch.setattr(
        api, "retrieve_evidence",
        lambda *_a, **_k: pytest.fail("retrieval must not be called"),
    )

    done = _done(TestClient(api.app).post("/turn", json={"question": question}))

    assert done["outcome"] == "clarify"
    assert done["sources"] == []
    assert done["reason"] == "transfer_fee_dimensions_missing"
    text = " ".join(
        event.get("text", "") for event in _events(
            TestClient(api.app).post("/turn", json={"question": question})
        ) if event["type"] == "token"
    ).lower()
    assert "avokat" not in text
    assert "ligj" not in text


def test_followups_fill_existing_structured_frame(deterministic):
    frame = None
    first = callcenter.decide(
        "Cilat janë tarifat për transfertat bankare?", "", [],
        last_structured_frame=frame,
    )
    frame = callcenter.next_structured_frame(first, frame)

    segment = callcenter.decide(
        "Për individë, brenda vendit.", "", [],
        last_structured_frame=frame,
    )
    frame = callcenter.next_structured_frame(segment, frame)
    assert segment.outcome is callcenter.Outcome.CLARIFY
    assert frame.customer_segment == "individual"
    assert frame.transfer_scope == "domestic"
    assert frame.bank_scope == "missing"
    assert segment.message == callcenter.TRANSFER_FEE_BANK_CLARIFY_MESSAGE

    bank = callcenter.decide("Për BKT.", "", [], last_structured_frame=frame)
    assert bank.outcome is callcenter.Outcome.UNSUPPORTED
    assert bank.reason is callcenter.DecisionReason.TRANSFER_FEE_PRICE_UNAVAILABLE
    assert bank.rate_intent.banks == ("Banka Kombëtare Tregtare",)


def test_comparison_followup_is_represented(deterministic):
    first = callcenter.decide(
        "Cilat janë tarifat për transfertat bankare?", "", [],
    )
    frame = callcenter.next_structured_frame(first, None)
    scoped = callcenter.decide(
        "Për individë, brenda vendit.", "", [],
        last_structured_frame=frame,
    )
    frame = callcenter.next_structured_frame(scoped, frame)

    comparison = callcenter.decide(
        "Dua krahasim mes bankave.", "", [], last_structured_frame=frame,
    )

    assert comparison.outcome is callcenter.Outcome.UNSUPPORTED
    assert comparison.rate_intent.bank_scope == "all"
    assert comparison.rate_intent.customer_segment == "individual"
    assert comparison.rate_intent.transfer_scope == "domestic"


def test_fully_specified_missing_price_is_terminal_and_honest(
        deterministic, monkeypatch):
    monkeypatch.setattr(
        callcenter, "_encode_question",
        lambda _q: pytest.fail("embedding must not be called"),
    )

    decision = callcenter.decide(
        "Sa kushton transferta brenda vendit për individë te BKT?", "", [],
    )

    assert decision.outcome is callcenter.Outcome.UNSUPPORTED
    assert decision.reason is callcenter.DecisionReason.TRANSFER_FEE_PRICE_UNAVAILABLE
    assert "shumën konkrete" in decision.message
    assert "transparencën" in decision.message
    assert "avokat" not in decision.message.lower()


def test_regulatory_transfer_question_still_reaches_retrieval(deterministic):
    decision = callcenter.decide(
        "A duhet banka t'i publikojë tarifat e transfertave?", "", [],
    )
    assert decision.outcome is None
    assert decision.reason is callcenter.DecisionReason.DENSE_RETRIEVAL


def test_personalized_legal_transfer_fee_keeps_legal_precedence(deterministic):
    decision = callcenter.decide(
        "A është e ligjshme tarifa që më mori banka?", "", [],
    )
    assert decision.outcome is callcenter.Outcome.UNSUPPORTED
    assert decision.reason is callcenter.DecisionReason.LEGAL_ADVICE_EXPLICIT


def test_transfer_amount_is_not_mistaken_for_fee(deterministic):
    decision = callcenter.decide(
        "Sa kushton të dërgoj 500 euro nga Credins në BKT?", "", [],
    )
    assert decision.outcome is callcenter.Outcome.CLARIFY
    assert decision.reason is callcenter.DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING
    assert decision.rate_intent.currency is None
    assert decision.rate_intent.customer_segment is None


def test_reported_fee_normality_is_not_transfer_amount_intent(deterministic):
    decision = callcenter.decide("Tarifa ishte 500 lekë. A është normale?", "", [])
    assert decision.reason is callcenter.DecisionReason.DENSE_RETRIEVAL

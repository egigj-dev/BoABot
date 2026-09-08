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


def test_pending_transfer_frame_does_not_capture_unrelated_turns(deterministic):
    first = callcenter.decide(
        "Cilat janë tarifat për transfertat bankare?", "", [],
    )
    frame = callcenter.next_structured_frame(first, None)

    assert callcenter._transfer_fee_decision("Faleminderit", frame) is None
    assert callcenter._transfer_fee_decision("A ofron BKT kredi?", frame) is None


def test_bank_only_followup_still_uses_pending_transfer_frame(deterministic):
    first = callcenter.decide(
        "Cilat janë tarifat për transfertat bankare?", "", [],
    )
    frame = callcenter.next_structured_frame(first, None)
    scoped = callcenter.decide(
        "Për individë, brenda vendit.", "", [], last_structured_frame=frame,
    )
    frame = callcenter.next_structured_frame(scoped, frame)

    bank = callcenter._transfer_fee_decision("Për BKT.", frame)
    assert bank is not None
    assert bank.rate_intent.banks == ("Banka Kombëtare Tregtare",)


def test_unavailable_transfer_result_clears_pending_frame(deterministic):
    decision = callcenter.decide(
        "Sa kushton transferta brenda vendit për individë te BKT?", "", [],
    )
    assert callcenter.next_structured_frame(decision, decision.rate_intent) is None


@pytest.mark.parametrize("question", [
    "Sa kushton transferta brenda vendit, por edhe jashte vendit?",
    "Sa kushton transferta brenda vendit. Po jashtë vendit?",
])
def test_conflicting_transfer_geography_clarifies(deterministic, question):
    decision = callcenter.decide(question, "", [])
    assert decision.outcome is callcenter.Outcome.CLARIFY
    assert decision.reason is callcenter.DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING
    assert decision.rate_intent.transfer_scope is None


def test_explicit_conflicting_geography_overrides_prior_frame(deterministic):
    first = callcenter.decide("Cilat janë tarifat për transfertat bankare?", "", [])
    frame = callcenter.next_structured_frame(first, None)
    scoped = callcenter.decide(
        "Për individë, brenda vendit.", "", [], last_structured_frame=frame,
    )
    frame = callcenter.next_structured_frame(scoped, frame)
    conflict = callcenter.decide(
        "Sa kushton transferta brenda vendit, por jashtë vendit?", "", [],
        last_structured_frame=frame,
    )
    assert conflict.outcome is callcenter.Outcome.CLARIFY
    assert conflict.rate_intent.transfer_scope is None


# ---- Pre-fee transfer context and colloquial/fragment regressions -----------

def _advance(question, frame=None):
    decision = callcenter.decide(question, "", [], last_structured_frame=frame)
    return decision, callcenter.next_structured_frame(decision, frame)


def test_benign_transfer_statement_establishes_partial_rate_intent(deterministic):
    decision, frame = _advance("Dua të bëj një transfertë jashtë vendit.")
    assert decision.reason is callcenter.DecisionReason.TRANSFER_CONTEXT_ESTABLISHED
    assert decision.outcome is callcenter.Outcome.ANSWER
    assert frame.family == "bank_transfer"
    assert frame.transfer_scope == "international"
    assert frame.metric is None


def test_canonical_transfer_conversation_uses_existing_frame(deterministic):
    _, frame = _advance("Dua të bëj një transfertë jashtë vendit.")
    fee, frame = _advance("Sa është komisioni?", frame)
    assert fee.reason is callcenter.DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING
    assert frame.metric == "fee"
    assert frame.transfer_scope == "international"
    assert fee.message == callcenter.TRANSFER_FEE_SEGMENT_CLARIFY_MESSAGE
    detail, frame = _advance("Për 500 euro në Itali.", frame)
    assert detail.reason is callcenter.DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING
    assert frame.metric == "fee"
    assert frame.transfer_scope == "international"
    assert detail.message == callcenter.TRANSFER_FEE_SEGMENT_CLARIFY_MESSAGE


def test_scope_fragment_then_price_uses_partial_frame(deterministic):
    _, frame = _advance("Dua të bëj një transfertë.")
    scope, frame = _advance("Jashtë vendit.", frame)
    assert scope.reason is callcenter.DecisionReason.TRANSFER_CONTEXT_ESTABLISHED
    assert frame.transfer_scope == "international"
    fee, frame = _advance("Sa kushton?", frame)
    assert fee.reason is callcenter.DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING
    assert frame.metric == "fee"
    assert fee.message == callcenter.TRANSFER_FEE_SEGMENT_CLARIFY_MESSAGE


def test_unrelated_product_wins_over_partial_transfer_frame(deterministic):
    _, frame = _advance("Dua të bëj një transfertë jashtë vendit.")
    assert callcenter._transfer_fee_decision("Sa është komisioni i kartës?", frame) is None
    assert callcenter._transfer_fee_decision("Cilat janë normat e depozitave?", frame) is None


@pytest.mark.parametrize("question,scope,metric", [
    ("Po me çu 500 euro Itali sa mban banka?", None, "fee"),
    ("Nëse çoj lekë jashtë sa më kushton?", "international", "fee"),
    ("Sa mban banka për me çu lekë jashtë?", "international", "fee"),
    ("Kam me çu 300 euro në Gjermani, sa më mban?", None, "fee"),
    ("Sa kushton me çu lekë jashtë?", "international", "fee"),
    ("Po me çu lekë brenda Shqipnisë?", "domestic", None),
])
def test_colloquial_money_transfer_forms_are_structured(
        deterministic, question, scope, metric):
    decision = callcenter._transfer_fee_decision(question)
    assert decision is not None
    assert decision.rate_intent.family == "bank_transfer"
    assert decision.rate_intent.transfer_scope == scope
    assert decision.rate_intent.metric == metric


@pytest.mark.parametrize("question", [
    "Çoje informacionin me email.",
    "Po çoj dokumentet në bankë.",
    "Dua me çu një kërkesë.",
])
def test_non_money_colloquial_send_is_not_transfer_context(deterministic, question):
    assert callcenter._transfer_fee_decision(question) is None


@pytest.mark.parametrize("question,scope", [
    ("Jo brenda, jashtë vendit.", "international"),
    ("Nuk është brenda vendit, është jashtë.", "international"),
    ("Brenda? Jo, jashtë.", "international"),
    ("Jo jashtë, brenda Shqipërisë.", "domestic"),
    ("Është brenda, jo jashtë.", "domestic"),
    ("Mendova jashtë, por në fakt brenda.", "domestic"),
])
def test_transfer_scope_corrections_are_not_conflicts(question, scope):
    parsed_scope, conflict = callcenter._explicit_transfer_scope(callcenter.fold(question))
    assert not conflict
    assert parsed_scope == scope


@pytest.mark.parametrize("question", [
    "Sa kushton brenda krahasuar me jashtë?",
    "A ndryshon tarifa për brenda dhe jashtë vendit?",
    "Cila është më e lirë, transferta brenda apo jashtë?",
])
def test_geography_comparisons_are_not_conflicts(question):
    scope, conflict = callcenter._explicit_transfer_scope(callcenter.fold(question))
    assert not conflict
    assert scope is None


@pytest.mark.parametrize("fragment", [
    "Po jashtë?", "Po brenda?", "Po për Amerikë?", "Në Itali.",
    "Për BKT.", "Për individ.", "500 euro.", "Po 500 euro?",
])
def test_benign_fragments_cannot_be_incidents(deterministic, monkeypatch, fragment):
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "incident")
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: 999.0)
    decision = callcenter.decide(fragment, "", [])
    assert decision.reason not in {
        callcenter.DecisionReason.SEMANTIC_INCIDENT,
        callcenter.DecisionReason.INCIDENT_BACKSTOP,
    }


def test_pending_transfer_geography_fragment_is_continuation(deterministic):
    _, frame = _advance("Sa kushton një transfertë?")
    decision, frame = _advance("Po jashtë?", frame)
    assert decision.reason is callcenter.DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING
    assert frame.transfer_scope == "international"


@pytest.mark.parametrize("question", [
    "Nuk e njoh këtë transfertë.",
    "Nuk e kam bërë unë këtë pagesë.",
    "Më janë marrë para.",
    "Më ka dalë një transfertë që nuk e njoh.",
    "Kush e bëri këtë pagesë nga llogaria ime?",
])
def test_positive_incident_evidence_still_escalates(deterministic, monkeypatch, question):
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "incident")
    decision = callcenter.decide(question, "", [])
    assert decision.reason is callcenter.DecisionReason.SEMANTIC_INCIDENT
    assert decision.outcome is callcenter.Outcome.HANDOFF

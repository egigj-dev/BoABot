"""P0A commit 4: persisted structured-frame lifecycle, without consumption."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

import core.api as api
import core.callcenter as callcenter
from core.callcenter import ContextEffect, Decision, DecisionReason
from core.comparison import RateIntent


def _done(response):
    payloads = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    return next(payload for payload in payloads if payload["type"] == "done")


# Step 18-BX: the frame lifecycle table and the bind-time resolvability guard.
# STRUCTURED_PLANNER_CLARIFY STAYS in REPLACE (option (a) CLEAR was rejected —
# it drops the whole frame so a genuine answer to the clarify loses context and
# is re-parsed cold). What changed is next_structured_frame: a REPLACE frame is
# adopted only when it can actually resolve rows (the note's (b)); an
# unresolvable clarify intent (missing_key with no rows) is NOT carried, so a
# later slot reply cannot bind onto it and terminally refuse.
def test_frame_effect_mapping_table_is_stable() -> None:
    replace = {
        DecisionReason.CATALOG_EXACT_HIT,
        DecisionReason.TRANSFER_FEE_DIMENSIONS_MISSING,
        DecisionReason.TRANSFER_CONTEXT_ESTABLISHED,
        DecisionReason.STRUCTURED_PLANNER_CLARIFY,
    }
    preserve = {
        DecisionReason.REPEAT,
        DecisionReason.NEGATION_STATEMENT,
        DecisionReason.FRAGMENT_META,
        DecisionReason.SEMANTIC_SMALLTALK,
        DecisionReason.SEMANTIC_META_FOLLOWUP,
        DecisionReason.CATALOG_UNKNOWN_BANK,
        DecisionReason.CATALOG_CONFLICTING_SLOTS,
    }
    clear = set(DecisionReason) - replace - preserve

    assert all(callcenter.frame_effect(reason) is ContextEffect.REPLACE
               for reason in replace)
    assert all(callcenter.frame_effect(reason) is ContextEffect.PRESERVE
               for reason in preserve)
    assert all(callcenter.frame_effect(reason) is ContextEffect.CLEAR
               for reason in clear)


def test_next_structured_frame_resolvability_guard() -> None:
    # The genuine-answer path MUST survive (Step 18-BX constraint): an intent
    # that resolves rows (deposit family) is adopted as the frame, and a
    # concrete answer still replaces context.
    previous = RateIntent(
        bank_scope="all", banks=(), product=None, metric="interest_rate",
        fee_event=None, value_type=None, term_months=None, amount_band=None,
        breadth="product_metric", family="deposit",
    )
    # deposit family listing resolves rows -> adopted.
    deposit_decision = Decision(
        None, reason=DecisionReason.CATALOG_EXACT_HIT, rate_intent=previous,
    )
    assert callcenter.next_structured_frame(deposit_decision, None) is previous

    # An unresolvable intent (consumer-credit rates have NO rows) must be
    # rejected at bind time: it would otherwise be carried and make every
    # later slot reply a terminal refusal (the AE dead end).
    unresolvable = RateIntent(
        bank_scope="all", banks=(), product="consumer_credit_unsecured",
        metric="interest_rate", fee_event=None, value_type=None,
        term_months=None, amount_band=None, breadth="product_metric",
        family="credit",
    )
    clarify_decision = Decision(
        None, reason=DecisionReason.STRUCTURED_PLANNER_CLARIFY,
        rate_intent=unresolvable,
    )
    # STRUCTURED_PLANNER_CLARIFY stays REPLACE in the table, but the guard
    # drops the unresolvable frame.
    assert callcenter.frame_effect(DecisionReason.STRUCTURED_PLANNER_CLARIFY) \
        is ContextEffect.REPLACE
    assert callcenter.next_structured_frame(clarify_decision, previous) is None

    # A concrete CATALOG_EXACT_HIT that somehow does not resolve keeps the
    # previous context rather than destroying it.
    odd_decision = Decision(
        None, reason=DecisionReason.CATALOG_EXACT_HIT,
        rate_intent=unresolvable,
    )
    assert callcenter.next_structured_frame(odd_decision, previous) is previous

    preserve_decision = Decision(None, reason=DecisionReason.REPEAT)
    clear_decision = Decision(None, reason=DecisionReason.DENSE_RETRIEVAL)
    assert callcenter.next_structured_frame(preserve_decision, previous) is previous
    assert callcenter.next_structured_frame(clear_decision, previous) is None


def test_generate_turn_applies_outcome_driven_frame_lifecycle(monkeypatch) -> None:
    store = callcenter.SessionStore()
    monkeypatch.setattr(api, "sessions", store)
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.delenv("BOABOT_LLM_ROUTER", raising=False)
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(
        callcenter, "_classify_turn",
        lambda question, *_a: "smalltalk" if question == "Faleminderit" else "answer",
    )
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    monkeypatch.setattr(api, "needs_rewrite", lambda *_a, **_k: False)
    monkeypatch.setattr(
        api, "retrieve_evidence",
        lambda *_a, **_k: ([], callcenter.NO_EVIDENCE_MESSAGE),
    )

    client = TestClient(api.app)
    first = _done(client.post("/turn", json={
        "question": "normat e interesit per depozita?",
    }))
    session_id = first["session_id"]
    session = store.get(session_id)
    assert session.last_structured_frame is not None
    assert session.last_structured_frame.product == "deposit"

    _done(client.post("/turn", json={
        "question": "si funksionon regjistri i kredive?",
        "session_id": session_id,
    }))
    assert session.last_structured_frame is None

    _done(client.post("/turn", json={
        "question": "Faleminderit",
        "session_id": session_id,
    }))
    assert session.last_structured_frame is None

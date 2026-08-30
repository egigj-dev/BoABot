"""P0A commit 5: elliptical inheritance and post-rewrite structured reparse."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

import core.api as api
import core.callcenter as callcenter
import core.comparison as comparison
import core.rag as rag


def _events(response):
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _done(response):
    return next(event for event in _events(response) if event["type"] == "done")


def _frame(question: str) -> comparison.RateIntent:
    parsed = comparison.parse_rate_intent(question)
    assert parsed.status == "resolved"
    assert parsed.intent is not None
    return parsed.intent


@pytest.fixture
def router_off(monkeypatch):
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.delenv("BOABOT_LLM_ROUTER", raising=False)
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)


@pytest.mark.parametrize(
    ("question", "frame", "projection"),
    (
        (
            "po per kredi?",
            "normat e depozitave?",
            (None, "credit", "interest_rate", "all", None),
        ),
        (
            "po 24?",
            "normat e interesit per depozita Credins 12 muaj?",
            ("deposit", None, "interest_rate", "named", 24),
        ),
        (
            "po BKT?",
            "komisione per karta Credins?",
            (None, "card", "fee", "named", None),
        ),
    ),
)
def test_decide_merges_elliptical_slots(
        router_off, question, frame, projection) -> None:
    decision = callcenter.decide(
        question, "", [], last_structured_frame=_frame(frame),
    )

    assert decision.reason is callcenter.DecisionReason.CATALOG_EXACT_HIT
    assert decision.rate_intent is not None
    assert (
        decision.rate_intent.product,
        decision.rate_intent.family,
        decision.rate_intent.metric,
        decision.rate_intent.bank_scope,
        decision.rate_intent.term_months,
    ) == projection
    coverage = comparison.certify_semantic_coverage(
        question, decision.rate_intent,
    )
    assert coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    assert comparison.resolve_rate_rows(decision.rate_intent)


def test_smalltalk_preserves_frame_then_bank_replaces(router_off, monkeypatch) -> None:
    frame = _frame("normat e depozitave?")
    monkeypatch.setattr(
        callcenter, "_classify_turn",
        lambda question, *_a: "smalltalk" if question == "Faleminderit" else "answer",
    )
    thanks = callcenter.decide(
        "Faleminderit", "", [], last_structured_frame=frame,
    )
    preserved = callcenter.next_structured_frame(thanks, frame)
    followup = callcenter.decide(
        "po Credins?", "", [], last_structured_frame=preserved,
    )

    assert preserved is frame
    assert followup.reason is callcenter.DecisionReason.CATALOG_EXACT_HIT
    assert followup.rate_intent is not None
    assert followup.rate_intent.product == "deposit"
    assert followup.rate_intent.banks == ("Banka Credins",)


def test_dense_turn_clears_frame_before_next_followup(router_off) -> None:
    frame = _frame("normat e depozitave?")
    dense = callcenter.decide(
        "si funksionon regjistri i kredive?", "", [],
        last_structured_frame=frame,
    )
    cleared = callcenter.next_structured_frame(dense, frame)
    followup = callcenter.decide(
        "po per kredi?", "", [], last_structured_frame=cleared,
    )

    assert dense.reason is callcenter.DecisionReason.DENSE_RETRIEVAL
    assert cleared is None
    assert followup.reason is callcenter.DecisionReason.DENSE_RETRIEVAL
    assert followup.rate_intent is None


def test_no_frame_decide_falls_through(router_off) -> None:
    decision = callcenter.decide(
        "po per kredi?", "", [], last_structured_frame=None,
    )

    assert decision.reason is callcenter.DecisionReason.DENSE_RETRIEVAL
    assert decision.rate_intent is None


@pytest.mark.parametrize(
    "question",
    (
        "po ku eshte dega e bankes?",
        "po per llogari?",
    ),
)
def test_adversarial_continuations_do_not_merge(router_off, question) -> None:
    frame = _frame("normat e depozitave?")

    assert comparison.merge_elliptical(question, frame) is None
    decision = callcenter.decide(
        question, "", [], last_structured_frame=frame,
    )
    assert decision.reason is callcenter.DecisionReason.DENSE_RETRIEVAL
    assert decision.rate_intent is None


def test_post_rewrite_seam_still_honors_structured_eligibility(router_off) -> None:
    question = "kam humbur karten; cilat jane tarifat e kartes se debitit?"

    assert not callcenter._structured_rate_eligible(question)
    assert callcenter._structured_rate_decision(question) is None


def test_bare_term_without_old_term_still_requires_full_certification(router_off) -> None:
    frame = _frame("normat e depozitave?")
    merged = comparison.merge_elliptical("po 24?", frame)

    assert merged is not None
    assert merged.term_months == 24
    coverage = comparison.certify_semantic_coverage("po 24?", merged)
    assert coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    assert coverage.unresolved_qualifiers == ()


def _api_setup(monkeypatch):
    store = callcenter.SessionStore()
    monkeypatch.setattr(api, "sessions", store)
    monkeypatch.setattr(api, "needs_rewrite", lambda *_a, **_k: False)
    monkeypatch.setattr(
        api, "retrieve_evidence",
        lambda query, *_a, rate_intent=None, **_k: (
            comparison.structured_rate_hits(rate_intent) if rate_intent else [],
            "" if rate_intent else callcenter.NO_EVIDENCE_MESSAGE,
        ),
    )
    return store, TestClient(api.app)


def _seed(store, frame, *, history=()):
    session = store.get(None)
    session.last_structured_frame = frame
    session.history.extend(history)
    return session


@pytest.mark.parametrize(
    ("frame_question", "followup", "expected"),
    (
        (
            "normat e interesit per depozita Credins 12 muaj?",
            "po 24?",
            ("deposit", "interest_rate", ("Banka Credins",), 24),
        ),
        (
            "komisione per karta Credins?",
            "po BKT?",
            (None, "fee", ("Banka Kombëtare Tregtare",), None),
        ),
    ),
)
def test_api_digit_and_capital_followups_use_preserved_frame(
        router_off, monkeypatch, frame_question, followup, expected) -> None:
    store, client = _api_setup(monkeypatch)
    session = _seed(store, _frame(frame_question))

    done = _done(client.post("/turn", json={
        "question": followup, "session_id": session.session_id,
    }))

    assert done["outcome"] == "answer"
    assert done["reason"] == callcenter.DecisionReason.CATALOG_EXACT_HIT.value
    current = session.last_structured_frame
    assert current is not None
    assert (current.product, current.metric, current.banks, current.term_months) == expected


def test_api_smalltalk_preserves_frame_for_capital_followup(
        router_off, monkeypatch) -> None:
    store, client = _api_setup(monkeypatch)
    monkeypatch.setattr(
        callcenter, "_classify_turn",
        lambda question, *_a: "smalltalk" if question == "Faleminderit" else "answer",
    )
    first = _done(client.post("/turn", json={
        "question": "normat e depozitave?",
    }))
    session = store.get(first["session_id"])

    _done(client.post("/turn", json={
        "question": "Faleminderit", "session_id": session.session_id,
    }))
    done = _done(client.post("/turn", json={
        "question": "po Credins?", "session_id": session.session_id,
    }))

    assert done["outcome"] == "answer"
    assert session.last_structured_frame is not None
    assert session.last_structured_frame.banks == ("Banka Credins",)


def test_api_dense_turn_clears_frame_and_contextual_rewrite_stays_dense(
        router_off, monkeypatch) -> None:
    store, client = _api_setup(monkeypatch)
    monkeypatch.setattr(api, "needs_rewrite", lambda q, _h: q.startswith("po "))
    monkeypatch.setattr(
        api, "rewrite",
        lambda _q, _h: "po per kredi ne regjistrin e kredive?",
    )
    first = _done(client.post("/turn", json={
        "question": "normat e depozitave?",
    }))
    session = store.get(first["session_id"])

    _done(client.post("/turn", json={
        "question": "si funksionon regjistri i kredive?",
        "session_id": session.session_id,
    }))
    done = _done(client.post("/turn", json={
        "question": "po per kredi?", "session_id": session.session_id,
    }))

    assert session.last_structured_frame is None
    assert done["reason"] == callcenter.DecisionReason.DENSE_NO_TRUSTED_HITS.value


def test_api_post_rewrite_reparse_without_prior_frame(
        router_off, monkeypatch) -> None:
    store, client = _api_setup(monkeypatch)
    monkeypatch.setattr(api, "needs_rewrite", rag.needs_rewrite)
    monkeypatch.setattr(
        api, "rewrite",
        lambda _q, _h: "cilat jane normat e interesit per kredi?",
    )
    # Commit 5 must reach the structured renderer.  The current renderer has no
    # representation for unbanked credit-family rows (a later, forbidden scope),
    # so isolate that pre-existing output limitation from this control-flow test.
    monkeypatch.setattr(
        comparison, "render_rate_answer", lambda _intent, _hits: "Përgjigje e strukturuar.",
    )
    session = _seed(store, None, history=(
        {"role": "user", "content": "Kam një pyetje tjetër."},
        {"role": "assistant", "content": "Urdhëroni."},
    ))

    done = _done(client.post("/turn", json={
        "question": "po per kredi?", "session_id": session.session_id,
    }))

    assert done["outcome"] == "answer"
    assert done["reason"] == callcenter.DecisionReason.CATALOG_EXACT_HIT.value
    assert session.last_structured_frame is not None
    assert session.last_structured_frame.family == "credit"
    assert session.last_structured_frame.metric == "interest_rate"

"""P0A commit 7: trace-event plumbing and debug-only SSE surface."""

import json
from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

import core.api as api
import core.callcenter as callcenter
import core.comparison as comparison


def _events(response):
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _done(response):
    return next(event for event in _events(response) if event["type"] == "done")


def _api_setup(monkeypatch):
    store = callcenter.SessionStore()
    monkeypatch.setattr(api, "sessions", store)
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    monkeypatch.setattr(
        api, "retrieve_evidence",
        lambda _query, *_a, rate_intent=None, **_k: (
            comparison.structured_rate_hits(rate_intent) if rate_intent else [],
            "" if rate_intent else callcenter.NO_EVIDENCE_MESSAGE,
        ),
    )
    return store, TestClient(api.app)


def test_unrepresented_qualifier_trace_is_constructed_at_seam(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.ones(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)

    decision = callcenter.decide("a ofrojne kredi per udhetime?", "", [])

    assert decision.reason is callcenter.DecisionReason.DENSE_RETRIEVAL
    assert decision.rate_intent is None
    assert decision.query_embedding is not None
    assert decision.trace_flags == frozenset({
        callcenter.DecisionEvent.unresolved_qualifier_detected,
    })


def test_structured_and_inherited_trace_flags_are_constructed(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    frame_parse = comparison.parse_rate_intent("normat e depozitave?")
    assert frame_parse.intent is not None

    direct = callcenter.decide("normat e depozitave?", "", [])
    inherited = callcenter.decide(
        "po per kredi?", "", [], last_structured_frame=frame_parse.intent,
    )

    assert direct.trace_flags == frozenset({
        callcenter.DecisionEvent.structured_lookup,
    })
    assert inherited.trace_flags == frozenset({
        callcenter.DecisionEvent.context_inherited,
        callcenter.DecisionEvent.structured_lookup,
    })


def test_rewritten_elliptical_reparse_exposes_merged_debug_trace(
        monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setenv("BOABOT_DEBUG", "1")
    store, client = _api_setup(monkeypatch)
    monkeypatch.setattr(api, "needs_rewrite", lambda *_a, **_k: True)
    monkeypatch.setattr(
        api, "rewrite",
        lambda *_a, **_k: "cilat jane normat e interesit per kredi?",
    )
    session = store.get(None)
    session.history.extend((
        {"role": "user", "content": "Kam një pyetje tjetër."},
        {"role": "assistant", "content": "Urdhëroni."},
    ))

    done = _done(client.post("/turn", json={
        "question": "po per kredi?", "session_id": session.session_id,
    }))

    assert done["outcome"] == "answer"
    assert done["trace_flags"] == ["query_rewritten", "structured_lookup"]


def test_fused_rewrite_sets_query_rewritten_debug_trace(monkeypatch) -> None:
    monkeypatch.delenv("BOABOT_COMPARISON_STRUCTURED", raising=False)
    monkeypatch.setenv("BOABOT_DEBUG", "1")
    _store, client = _api_setup(monkeypatch)
    monkeypatch.setattr(
        callcenter, "_analyze_turn",
        lambda *_a, **_k: SimpleNamespace(
            label="answer", rewritten_query="pyetje e pavarur",
            legal_flags=None,
        ),
    )

    done = _done(client.post("/turn", json={"question": "po kjo?"}))

    assert done["trace_flags"] == ["query_rewritten"]


def test_done_payload_omits_trace_flags_without_debug(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.delenv("BOABOT_DEBUG", raising=False)
    _store, client = _api_setup(monkeypatch)
    monkeypatch.setattr(api, "needs_rewrite", lambda *_a, **_k: False)

    done = _done(client.post("/turn", json={
        "question": "normat e depozitave?",
    }))

    assert done["outcome"] == "answer"
    assert "trace_flags" not in done

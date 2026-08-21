"""api.generate_turn telemetry: policy handoffs vs. downstream system errors.

Both currently emit the identical HANDOFF_MESSAGE/Outcome.HANDOFF to the
caller (by design, conservative default), but must be distinguishable in the
structured telemetry log line so a burst of transient RAG/provider failures
is not mistaken for the router mis-handoffing benign questions.
"""
import json
import logging

import numpy as np

import core.api as api
from core.callcenter import Decision, Outcome
from core.rag import RAGError


class _Session:
    session_id = "test-session"
    last_answer = ""
    history: list = []


def _run_turn(monkeypatch, caplog, *, decision=None, stream_raises=False,
              reject_after_first=False):
    monkeypatch.setattr(api, "sessions", type("S", (), {
        "get": staticmethod(lambda _sid: _Session()),
        "record": staticmethod(lambda *a, **k: None),
    }))
    monkeypatch.setattr(api, "decide", lambda *a, **k: decision)
    monkeypatch.setattr(api, "needs_rewrite", lambda *a, **k: False)
    monkeypatch.setattr(api, "retrieve_evidence", lambda *a, **k: (
        [{"id": "rate_0001", "doc": "Doc", "article": "", "url": "u", "text": "t 2%", "dense_score": 0.9}],
        "",
    ))
    monkeypatch.setattr(api, "grounded_messages", lambda *a, **k: [])
    if reject_after_first:
        monkeypatch.setattr(
            api, "stream_answer",
            lambda *a, **k: iter(("Fjalia e parë. Komisioni është 20 EUR.",)),
        )

        def verify(sentence, _sources):
            return type("V", (), {
                "approved": sentence == "Fjalia e parë.",
                "reason": "simulated late rejection",
            })()

        monkeypatch.setattr(api._fidelity_guard, "verify_sources", verify)
    elif stream_raises:
        def _raise(*a, **k):
            raise RAGError("simulated provider failure")
        monkeypatch.setattr(api, "stream_answer", _raise)
    else:
        monkeypatch.setattr(api, "stream_answer", lambda *a, **k: iter(["Fjali test."]))
        monkeypatch.setattr(api._fidelity_guard, "verify_sources",
                             lambda *a, **k: type("V", (), {"approved": True, "reason": ""})())

    req = api.TurnReq(question="Sa është komisioni te BKT?")
    with caplog.at_level(logging.INFO):
        events = list(api.generate_turn(req))
    telemetry = json.loads(caplog.records[-1].message)
    return events, telemetry


def test_policy_handoff_is_tagged_in_telemetry(monkeypatch, caplog):
    decision = Decision(Outcome.HANDOFF, "handoff", handoff=True, reason="credential")
    events, telemetry = _run_turn(monkeypatch, caplog, decision=decision)

    assert telemetry["outcome"] == "handoff"
    assert telemetry["handoff"] is True
    assert telemetry["handoff_reason"] == "credential"
    done = [json.loads(e[6:]) for e in events if '"type": "done"' in e][0]
    assert done["outcome"] == "handoff"
    assert done["handoff"] is True


def test_system_error_handoff_is_tagged_separately_from_policy(monkeypatch, caplog):
    decision = Decision(
        None, question="Sa është komisioni te BKT?", query_embedding=np.zeros(1),
    )
    events, telemetry = _run_turn(monkeypatch, caplog, decision=decision, stream_raises=True)

    assert telemetry["outcome"] == "degraded"
    assert telemetry["handoff"] is False
    assert telemetry["handoff_reason"] == "degraded"
    done = [json.loads(e[6:]) for e in events if '"type": "done"' in e][0]
    assert done["outcome"] == "degraded"
    assert done["handoff"] is False


def test_late_fidelity_rejection_drops_only_unverified_sentence(monkeypatch, caplog):
    decision = Decision(
        None, question="Sa është komisioni te BKT?", query_embedding=np.zeros(1),
    )
    events, telemetry = _run_turn(
        monkeypatch, caplog, decision=decision, reject_after_first=True,
    )
    token_text = "".join(
        json.loads(event[6:]).get("text", "")
        for event in events if '"type": "token"' in event
    )
    assert "Fjalia e parë" in token_text
    assert "20 EUR" not in token_text
    assert telemetry["outcome"] == "answer"
    assert telemetry["handoff"] is False


def test_vetted_passages_require_server_side_bridge_secret(monkeypatch):
    class FakeRequest:
        headers: dict[str, str]

        def __init__(self, value: str | None):
            self.headers = {"X-BoABot-Voice-Key": value} if value else {}

    monkeypatch.setenv("BOABOT_VOICE_BRIDGE_KEY", "server-secret")
    assert not api._voice_bridge_authorized(FakeRequest(None))
    assert not api._voice_bridge_authorized(FakeRequest("wrong"))
    assert api._voice_bridge_authorized(FakeRequest("server-secret"))

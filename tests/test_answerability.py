"""Answerability/abstain layer: deterministic floor + LLM verdict + /turn wiring.

core/answerability.py decides whether retrieved evidence actually ANSWERS the
question before the model generates. The deterministic floor fires in BOTH
router modes (fail-closed); the LLM semantic verdict is behind BOABOT_LLM_ANSWERABILITY=1
and is exercised here by monkeypatching the seam (offline, like the router tests).
"""
import json

import numpy as np
import pytest

import core.answerability as answerability
import core.api as api
from core.answerability import ABSTAIN_MESSAGE, answerable, lexical_verdict
from core.callcenter import Decision, Outcome


def _hit(text="Sa norma eshte 2.5% ne depozita 12 mujore.", article="", src="dense"):
    base = {"id": "rate_0001", "doc": "Rregullore", "article": article,
            "url": "u", "text": text, "dense_score": 0.9}
    if src == "metadata_pin":
        base["retrieval_source"] = "metadata_pin"
    return base


# ---- deterministic lexical floor ------------------------------------------

def test_price_question_abstains_when_no_value_in_evidence() -> None:
    ok, reason = answerable("Sa është komisioni për shlyerje të parakohshme?",
                            [_hit("Rregullorja flet për komisionet e shlyerjes.")])
    assert ok is False
    assert reason == "abstain_price_without_value"


def test_price_question_answers_when_value_present() -> None:
    ok, _ = answerable("Sa është komisioni për shlyerje të parakohshme?",
                       [_hit("Komisioni për shlyerje të parakohshme është 2.00%.")])
    assert ok is True


def test_non_price_question_without_number_is_not_refused() -> None:
    # "a ka komision?" (yes/no) has no PRICE_INTENT term and must not abstain.
    ok, reason = answerable("A ka komision për shlyerje të parakohshme?",
                            [_hit("Komisioni për shlyerje të parakohshme nuk ekziston.")])
    assert ok is True
    assert reason == ""


def test_article_pin_abstains_when_no_chunk_carries_it() -> None:
    ok, reason = answerable("Cfarë thotë neni 7 i rregullores?",
                            [_hit("Rregullore e përgjithshme.", article="3")])
    assert ok is False
    assert reason == "abstain_no_article_in_evidence"


def test_article_pin_answers_when_matching_article_present() -> None:
    ok, reason = answerable("Cfarë thotë neni 7 i rregullores?",
                            [_hit("Teksti i nenit 7.", article="7")])
    assert ok is True
    assert reason == ""


def test_article_pin_trusted_when_metadata_pinned() -> None:
    # The Statuti pin path tags hits as metadata_pin; the floor must not undo it.
    ok, _ = answerable("Cfarë thotë neni 7 i Statutit?",
                       [_hit("Statutë e Bankës.", src="metadata_pin")])
    assert ok is True


def test_empty_hits_are_never_answerable() -> None:
    ok, reason = answerable("pyetje", [])
    assert ok is False
    assert reason == "abstain_no_hits"


# ---- LLM semantic verdict (seam-injected, like the router) --------------------

def _inject(monkeypatch, verdict):
    monkeypatch.setattr(answerability, "_answerability_verdict",
                        lambda *a, **k: verdict)


def test_llm_no_abstains(monkeypatch) -> None:
    _inject(monkeypatch, "NO")
    ok, reason = answerable("sa eshte komisioni?", [_hit("2.00% komision.")])
    assert ok is False
    assert reason == "abstain_llm_judgment"


@pytest.mark.parametrize("verdict", ["UNCLEAR", "unclear"])
def test_llm_unclear_abstains(monkeypatch, verdict) -> None:
    _inject(monkeypatch, verdict)
    ok, reason = answerable("sa eshte komisioni?", [_hit("2.00% komision.")])
    assert ok is False
    assert reason == "abstain_llm_judgment"


def test_llm_yes_generates(monkeypatch) -> None:
    _inject(monkeypatch, "YES")
    ok, _ = answerable("sa eshte komisioni?", [_hit("2.00% komision.")])
    assert ok is True


def test_llm_failure_falls_through_to_generation(monkeypatch) -> None:
    # Provider failure / disabled / unparseable -> None -> answerable (fail-open).
    _inject(monkeypatch, None)
    ok, _ = answerable("sa eshte komisioni?", [_hit("2.00% komision.")])
    assert ok is True


# ---- real _answerability_verdict prompt/parse (mocked provider call) ---------
# Unlike the seam-stub tests above, these exercise the actual format + parse
# inside _answerability_verdict, so a template/key or regex regression is caught
# offline (a live run first exposed a {evidence} format KeyError here).

def _inject_provider(monkeypatch, content):
    """Mock the real provider call path so _answerability_verdict's own format +
    parse are exercised (the real rag.completion_message reads the mock shape)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(answerability, "_enabled", lambda: True)
    import core.rag as rag
    monkeypatch.setattr(rag, "_post", lambda *a, **k: {
        "choices": [{"message": {"content": content}}],
    })
    return rag


def _verdict_of(monkeypatch, content):
    _inject_provider(monkeypatch, content)
    return answerability._answerability_verdict("sa eshte komisioni?",
                                                [_hit("2.00% komision.")])


def test_verdict_prompt_and_parse_yes(monkeypatch) -> None:
    assert _verdict_of(monkeypatch, "YES") == "YES"
    # answerable() routes a real YES through the full format path without KeyError.
    _inject_provider(monkeypatch, "YES")
    ok, _ = answerable("sa eshte komisioni?", [_hit("2.00% komision.")])
    assert ok is True


def test_verdict_prompt_and_parse_no_and_unclear(monkeypatch) -> None:
    assert _verdict_of(monkeypatch, "NO") == "NO"
    assert _verdict_of(monkeypatch, "UNCLEAR") == "UNCLEAR"


def test_verdict_unparseable_falls_open(monkeypatch) -> None:
    # Garbled response -> None -> generation path (never a spurious abstain).
    assert _verdict_of(monkeypatch, "ndoshta jam i sigurt") is None


# ---- generate_turn integration ------------------------------------------------

def _run_generate_turn(monkeypatch, *, hits, answer_token=None):
    """Drive generate_turn with the answer path fully stubbed. When answer_token is
    None we expect the floor to ABSTAIN (stream_answer never reached); otherwise the
    model streams that sentence and the fidelity guard approves it (generation path)."""
    class _Session:
        session_id = "abstain-session"
        last_answer = ""
        history: list = []

    calls = []
    monkeypatch.setattr(api, "sessions", type("S", (), {
        "get": staticmethod(lambda _sid: _Session()),
        "record": staticmethod(lambda *a, **k: None),
    }))
    monkeypatch.setattr(api, "decide", lambda *a, **k: Decision(
        None, question="Sa është komisioni te BKT?", query_embedding=np.zeros(1),
    ))
    monkeypatch.setattr(api, "needs_rewrite", lambda *a, **k: False)
    monkeypatch.setattr(
        api, "retrieve_evidence", lambda *a, **k: (hits, ""),
    )
    if answer_token is not None:
        monkeypatch.setattr(
            api, "stream_answer",
            lambda *a, **k: calls.append("stream_answer") or iter([answer_token]),
        )
        monkeypatch.setattr(
            api._fidelity_guard, "verify_sources",
            lambda *a, **k: type("V", (), {"approved": True, "reason": ""})(),
        )
    else:
        monkeypatch.setattr(
            api, "stream_answer",
            lambda *a, **k: calls.append("stream_answer") or iter([]),
        )
    req = api.TurnReq(question="Sa është komisioni te BKT?")
    events = list(api.generate_turn(req))
    return events, calls


def test_generate_turn_abstains_when_evidence_lacks_value(monkeypatch) -> None:
    hits = [_hit("Rregullorja përshkruhet komisionet por pa shifra.")]
    events, downstream = _run_generate_turn(monkeypatch, hits=hits)

    assert downstream == []  # the model stream is never started
    token_text = "".join(
        json.loads(e[6:]).get("text", "") for e in events if '"type": "token"' in e
    )
    assert ABSTAIN_MESSAGE in token_text
    done = json.loads([e[6:] for e in events if '"type": "done"' in e][0])
    assert done["outcome"] == "unsupported"
    assert done["reason"] == "abstain_price_without_value"


def test_generate_turn_defers_to_generation_when_evidence_answers(monkeypatch) -> None:
    hits = [_hit("Komisioni për shlyerje të parakohshme është 2.00%.")]
    events, downstream = _run_generate_turn(
        monkeypatch, hits=hits, answer_token="Komisioni është 2.00%.",
    )

    assert downstream == ["stream_answer"]  # generation is reached
    done = json.loads([e[6:] for e in events if '"type": "done"' in e][0])
    assert done["outcome"] == "answer"
    assert done.get("reason") is None
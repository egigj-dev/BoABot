"""Tier-2 build-batch steps 2a, 2b, 6, 7, 9 (offline, deterministic).

Step 2a: input-side diacritic/misspelling restore (cfare -> çfarë, eshte -> është)
applied in callcenter.decide() before embedding/retrieval.
Step 2b: fused single-call intent+rewrite+legal flags (router.analyze_turn);
decide() carries the standalone query so api.py skips the separate rewrite().
Step 6: 3-way SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED gate (answerability.judge).
Step 7: zero-"korpus" in user-facing messages; abstain leads with the direct
answer.
Step 9: /turn done event carries answer_text / answer_display (split contract).
"""
import json

import numpy as np
import pytest

import core.answerability as ans
import core.callcenter as callcenter
import core.rag as rag_mod
import core.router as router
import core.text_norm as text_norm
from core import api
from core.answerability import ABSTAIN_MESSAGE
from core.callcenter import Outcome, decide
from core.trust import NO_EVIDENCE_MESSAGE


# ---- Step 2a: diacritic restore --------------------------------------------

def test_restore_diacritics_restores_known_terms():
    out = text_norm.restore_diacritics("Cfare eshte raporti qendrueshem?")
    assert out == "Çfarë është raporti qëndrueshëm?"
    assert text_norm.restore_diacritics("cfare eshte norma") == "çfarë është norma"


def test_restore_diacritics_leaves_unknown_and_numbers():
    assert text_norm.restore_diacritics("xyzzy 123 kredi norme") == "xyzzy 123 kredi norme"


def test_restore_diacritics_preserves_existing_diacritics():
    already = "Është çfarë përgjigje"
    assert text_norm.restore_diacritics(already) == already


def test_restore_diacritics_does_not_corrupt_plain_c_question_words():
    # cili/cila/cilat/cilin are spelled with plain C in Albanian (only cfar/cdo/eshte use ç/ë). These must pass through untouched.
    for word in ("cilat", "cila", "cili", "cilin", "cile"):
        assert text_norm.restore_diacritics(word) == word
    # "shqiperi" is left as-typed (no forced definite form "shqipëria").
    assert text_norm.restore_diacritics("shqiperi") == "shqiperi"


def test_restore_diacritics_preserves_punctuation_and_whitespace():
    out = text_norm.restore_diacritics("  Cfare?? eshte  (kredi).")
    assert out == "  Çfarë?? është  (kredi)."


def test_decide_applies_diacritic_restore_to_question(monkeypatch):
    # A fee question typed without diacritics should route to retrieval with
    # the canonical form in decision.question (Step 2a threaded into decide).
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *a, **k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question",
                        lambda q: np.asarray([0.0, 0.0], dtype=np.float32))
    monkeypatch.setattr(callcenter, "_probe_score", lambda *a: None)
    decision = decide("cfare eshte komisioni per shlyerje te parakohshme?", "", [])
    assert decision.question == "çfarë është komisioni per shlyerje te parakohshme?"


# ---- Step 2b: fused single-call intent + rewrite + legal flags -------------

class _FakeAnalysis(router.TurnAnalysis):
    pass


def test_analyze_turn_parses_fused_json(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(router, "_enabled", lambda: True)
    monkeypatch.setattr(router, "is_conversational_fragment", lambda q: False)

    def fake_post(payload):
        return {"choices": [{"message": {"content": json.dumps({
            "intent": "answer",
            "rewritten_query": "Cfare eshte raporti neto i qendrueshem?",
            "legal_flags": {"is_legal_advice": False, "is_personal_application": False},
        })}}]}

    monkeypatch.setattr(rag_mod, "_post", fake_post)
    analysis = router.analyze_turn("cfare eshte raporti neto?", [], None, False)
    assert analysis is not None
    assert analysis.label == "answer"
    assert "raporti neto" in analysis.rewritten_query
    assert analysis.legal_flags["is_legal_advice"] is False


def test_analyze_turn_defaults_bad_intent_to_answer(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(router, "_enabled", lambda: True)
    monkeypatch.setattr(router, "is_conversational_fragment", lambda q: False)
    monkeypatch.setattr(rag_mod, "_post",
                        lambda p: {"choices": [{"message": {"content": json.dumps({
                            "intent": "gibberish", "rewritten_query": "Q",
                        })}}]})
    analysis = router.analyze_turn("pyetje?", [])
    assert analysis is not None and analysis.label == "answer"


def test_analyze_turn_none_when_disabled(monkeypatch):
    monkeypatch.setattr(router, "_enabled", lambda: False)
    assert router.analyze_turn("pyetje?", [], None, False) is None


def test_decide_carries_fused_rewrite_when_router_on(monkeypatch):
    # When the fused seam yields a label + rewritten query, decide() must route
    # on the label AND expose rewritten_query/legal_flags on the Decision (so
    # api.py can skip the separate rewrite() call).
    analysis = router.TurnAnalysis("answer", "Sa eshte komisioni i bankes?",
                                   {"is_legal_advice": False,
                                    "is_personal_application": False})
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *a, **k: analysis)
    monkeypatch.setattr(callcenter, "_encode_question",
                        lambda q: np.asarray([0.0, 0.0], dtype=np.float32))
    monkeypatch.setattr(callcenter, "_probe_score", lambda *a: None)
    decision = decide("po tarifat e bankes?", "", [])
    assert decision.outcome is None  # falls through to retrieval
    assert decision.rewritten_query == "Sa eshte komisioni i bankes?"
    assert decision.legal_flags["is_legal_advice"] is False


def test_decide_falls_back_when_fused_unavailable(monkeypatch):
    # If _analyze_turn returns None (disabled/off), decide() still works via
    # the legacy classify seam.
    analysis = None
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *a, **k: analysis)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *a, **k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question",
                        lambda q: np.asarray([0.0, 0.0], dtype=np.float32))
    monkeypatch.setattr(callcenter, "_probe_score", lambda *a: None)
    decision = decide("cfare eshte norma e depozitave?", "", [])
    assert decision.outcome is None
    assert decision.rewritten_query is None


# ---- Step 6: 3-way answerability gate --------------------------------------

def test_judge_three_way_levels(monkeypatch):
    hits = [{"id": "rate_0001", "doc": "D", "article": "", "url": "u",
             "text": "Komisioni eshte 2.0 per shlyerje te parakohshme."}]
    monkeypatch.setattr(ans, "_answerability_verdict", lambda q, h: "YES")
    assert ans.judge("cfare eshte komisioni?", hits) == ("SUPPORTED", "")

    monkeypatch.setattr(ans, "_answerability_verdict", lambda q, h: "UNCLEAR")
    assert ans.judge("cfare eshte komisioni?", hits)[0] == "PARTIALLY_SUPPORTED"

    monkeypatch.setattr(ans, "_answerability_verdict", lambda q, h: "NO")
    assert ans.judge("cfare eshte komisioni?", hits)[0] == "UNSUPPORTED"


def test_judge_no_hits_is_unsupported():
    assert ans.judge("pyetje", [])[0] == "UNSUPPORTED"


def test_answerable_backcompat_from_judge(monkeypatch):
    # answerable() is a thin wrapper: only UNSUPPORTED abstains.
    hits = [{"id": "rate_0001", "doc": "D", "article": "", "url": "u",
             "text": "Komisioni eshte 2.0 ne vit."}]
    monkeypatch.setattr(ans, "_answerability_verdict", lambda q, h: "UNCLEAR")
    can, _ = ans.answerable("q?", hits)
    assert can is True  # PARTIALLY_SUPPORTED still generates


# ---- Step 7: zero-"korpus" in user-facing messages -------------------------

def test_user_facing_messages_have_no_korpus():
    assert "korpus" not in ABSTAIN_MESSAGE.casefold()
    assert "korpus" not in NO_EVIDENCE_MESSAGE.casefold()
    # Internal verdict prompts also scrub the corpus jargon.
    assert "korpus" not in ans._VERDICT_SYSTEM.casefold()
    assert "korpus" not in ans._VERDICT_USER.casefold()


def test_abstain_message_leads_with_answer():
    first_sentence = ABSTAIN_MESSAGE.split(".")[0]
    # Leads with the direct stance ("no accurate answer"), not with corpus admin.
    assert "korpus" not in first_sentence.casefold()
    assert "përgjigje" in first_sentence.casefold()


# ---- Step 9: answer_text / answer_display split ----------------------------

def test_turn_done_emits_answer_split():
    import json as _json
    raw = api.turn_done(Outcome.ANSWER, "sid", answer_text="teksti",
                        answer_display="teksti")
    payload = _json.loads(raw[len("data: "):].strip())
    assert payload["answer_text"] == "teksti"
    assert payload["answer_display"] == "teksti"


def test_turn_done_omits_answer_split_when_absent():
    import json as _json
    raw = api.turn_done(Outcome.UNSUPPORTED, "sid")
    payload = _json.loads(raw[len("data: "):].strip())
    assert "answer_text" not in payload
    assert "answer_display" not in payload

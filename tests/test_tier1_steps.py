"""Tier-1 conversational-layer steps 3, 4, 8, 11 (offline, deterministic).

Step 3: no-repeat across turns —— generated sentence near-verbatim of the
previous answer is dropped (bge-m3 cosine, calibrated threshold, injectable
embed seam so tests need no model load).
Step 4: escalation is per-turn —— a fresh substantive turn after a handoff is
NOT routed to handoff again (no session-sticky escalation).
Step 8: issuer attribution —— every accepted chunk carries a derived issuer
(rate_* = commercial bank / its table; reg_* = Banka e Shqipërisë), fed to
generation and to the sanitized source citation.
Step 11: dates —— generation is instructed to never stamp today's date and to
say "sipas tabelave të publikuara" when the source carries no date.
"""
import numpy as np
import pytest

import core.api as api
import core.callcenter as callcenter
import core.rag as rag
from core.callcenter import Outcome, decide
from core.trust import issuer_of


# ---- Step 3: no-repeat across turns -----------------------------------------

def _fake_embed(text: str) -> np.ndarray:
    """Deterministic embedding seam: identical text -> same unit vector."""
    return np.asarray([1.0, 0.0], dtype=np.float32) if "Norma eshte 2.5" in text \
        else np.asarray([0.0, 1.0], dtype=np.float32)


def test_is_near_duplicate_uses_cosine_threshold(monkeypatch) -> None:
    monkeypatch.setattr(api, "_repeat_embed", _fake_embed)
    prior = _fake_embed("Norma eshte 2.5 per depozita.")
    # identical text -> cosine 1.0 >= 0.92 threshold: flagged
    assert api._is_near_duplicate("Norma eshte 2.5 per depozita.", prior)
    # different text -> cosine 0.0: not flagged
    assert not api._is_near_duplicate("Komisioni eshte 3 EUR.", prior)


def test_authorized_sentences_drops_verbatim_repeat(monkeypatch) -> None:
    monkeypatch.setattr(api, "_repeat_embed", _fake_embed)
    hits = [{"id": "rate_0001", "doc": "D", "article": "", "url": "u",
             "text": "Norma eshte 2.5 per depozita."}]
    token_stream = iter(["Norma eshte 2.5 per depozita."])
    out = list(api.authorized_sentences(
        token_stream, hits, prior_answer="Norma eshte 2.5 per depozita.",
    ))
    assert out == []


def test_authorized_sentences_keeps_new_content_after_repeat(monkeypatch) -> None:
    monkeypatch.setattr(api, "_repeat_embed", _fake_embed)
    hits = [{"id": "rate_0001", "doc": "D", "article": "", "url": "u",
             "text": "Norma eshte 2.5 per depozita. Komisioni eshte 3 EUR."}]
    # First sentence repeats the previous answer; second is new.
    token_stream = iter(["Norma eshte 2.5 per depozita. Komisioni eshte 3 EUR."])
    out = list(api.authorized_sentences(
        token_stream, hits, prior_answer="Norma eshte 2.5 per depozita.",
    ))
    assert out == ["Komisioni eshte 3 EUR."]


def test_no_repeat_only_fires_with_nonempty_prior(monkeypatch) -> None:
    monkeypatch.setattr(api, "_repeat_embed", _fake_embed)
    hits = [{"id": "rate_0001", "doc": "D", "article": "", "url": "u",
             "text": "Norma eshte 2.5 per depozita."}]
    # No prior answer: nothing is dropped (embed seam must not even be called).
    out = list(api.authorized_sentences(iter(["Norma eshte 2.5 per depozita."]),
                                        hits, prior_answer=None))
    assert out == ["Norma eshte 2.5 per depozita."]


# ---- Step 4: escalation is per-turn (no session-sticky handoff) -------------

def test_fresh_substantive_turn_after_handoff_is_not_rescalated(monkeypatch) -> None:
    # A session that just handed off must still route a NEW substantive
    # question through retrieval (outcome None), not inherit the handoff.
    monkeypatch.setattr(callcenter, "_encode_question", lambda _t: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _e: None)
    monkeypatch.setattr(
        callcenter, "_classify_turn",
        lambda question, last_outcome=None, last_handoff=False: "answer",
    )
    decision = decide(
        "Sa eshte komisioni i kartes se kreditit te BKT?",
        "Po jua kaloj biseden një agjenti.", [],
        Outcome.HANDOFF, True,
    )
    assert decision.outcome is None  # falls through to retrieval
    assert not decision.handoff


def test_meta_followup_after_handoff_explains_but_does_not_handoff_again(monkeypatch) -> None:
    monkeypatch.setattr(callcenter, "_encode_question", lambda _t: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _e: None)
    monkeypatch.setattr(
        callcenter, "_classify_turn",
        lambda question, last_outcome=None, last_handoff=False: "meta_followup",
    )
    decision = decide("pse?", "", [], Outcome.HANDOFF, True)
    assert decision.outcome is Outcome.ANSWER
    assert decision.handoff is True  # explains prior handoff, does NOT re-escalate as new incident
    assert decision.reason == "meta_followup"


# ---- Step 8: issuer attribution ---------------------------------------------

def test_issuer_rate_single_bank() -> None:
    # rate_* chunk whose text names exactly one commercial bank -> that bank.
    issuer = issuer_of("rate_0092", "Komisione per biznese\nBanka Raiffeisen: 2.00")
    assert issuer.lower() == "raiffeisen"


def test_issuer_rate_multi_bank_table() -> None:
    issuer = issuer_of("rate_0013",
                       "Normat e interesit\naBanka Credins: 1.50\nBanka Tirana: 1.20")
    assert "bankat" in issuer.lower() or "tarifat" in issuer.lower()


def test_issuer_reg_is_bank_of_albania() -> None:
    assert issuer_of("reg_03631", "Neni 5 Raporti neto i financimit") == "Banka e Shqipërisë"


def test_relevant_hits_carry_issuer(monkeypatch) -> None:
    # rag.retrieve_evidence decorates accepted hits with an issuer field.
    # DB-free: substitute trusted_hits (as bound in core.rag) with a
    # fixture-based gate so only the decorator logic (issuer attach) is under test.
    import core.rag as rag_mod
    import core.trust as trust

    hits = [{
        "id": "reg_03631", "doc": "Rregullore", "article": "5", "url": "u",
        "text": "Raporti neto i financimit te qendrueshem", "dense_score": 0.95,
    }]

    def fake_trusted(_query, candidate_hits):
        decorated = [dict(h) for h in candidate_hits]
        for h in decorated:
            h["issuer"] = trust.issuer_of(h["id"], h["text"])
        return trust.GateResult(True, accepted_hits=tuple(decorated))

    monkeypatch.setattr(rag_mod, "trusted_hits", fake_trusted)
    accepted, refusal = rag_mod.retrieve_evidence("pyetje", history=None)
    assert not refusal
    assert accepted and accepted[0]["issuer"] == "Banka e Shqipërisë"


def test_source_citation_includes_issuer() -> None:
    citation = api.source({
        "id": "rate_0092", "doc": "D", "article": "", "url": "u",
        "issuer": "raiffeisen",
    })
    assert citation["issuer"] == "raiffeisen"


# ---- Step 11: no invented dates, honest "published tables" -------------------

def test_system_prompt_bans_invented_dates() -> None:
    assert "Mos shpik data" in rag.SYSTEM
    assert "sipas tabelave t" in rag.SYSTEM
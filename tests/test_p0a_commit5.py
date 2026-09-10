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


def test_all_bank_frame_clears_bank_filter_for_bankless_credit_listing(
        router_off) -> None:
    frame = _frame("cilat jane normat e interesit per depozita?")

    decision = callcenter.decide(
        "po per kredi?", "", [], last_structured_frame=frame,
    )

    assert decision.reason is callcenter.DecisionReason.CATALOG_EXACT_HIT
    assert decision.rate_intent is not None
    assert decision.rate_intent.family == "credit"
    assert decision.rate_intent.metric == "interest_rate"
    assert decision.rate_intent.bank_scope == "all"
    assert decision.rate_intent.banks == ()
    hits = comparison.structured_rate_hits(decision.rate_intent)
    answer = comparison.render_rate_answer(decision.rate_intent, hits)
    assert hits
    assert "KREDI PER SHTEPI/PRONA" in answer


def test_named_bank_frame_does_not_clear_bank_filter_for_bankless_credit_rows(
        router_off) -> None:
    frame = _frame("normat e interesit per depozita Credins 12 muaj?")

    decision = callcenter.decide(
        "po per kredi?", "", [], last_structured_frame=frame,
    )

    assert decision.reason is callcenter.DecisionReason.DENSE_RETRIEVAL
    assert decision.rate_intent is None


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


def test_non_slot_reply_does_not_merge_into_carried_missing_key_frame(router_off) -> None:
    """Step 18-BX (c2): a non-slot-bearing reply ('nuk e di') is caught by the
    fragment/meta floor BEFORE any merge or retrieval — the strongest form of
    'does not bind onto the carried missing_key frame'. It re-asks with the
    generic meta message (FRAGMENT_META), rate_intent stays None, and it never
    becomes the rate refusal. (Pre-c2 this was DENSE_RETRIEVAL; the fragment
    floor now owns the non-answer, which is the c2 contract.)"""
    parsed = comparison.parse_rate_intent(
        "cila banke ka normen me te mire per kredi konsumatore?",
    )
    assert parsed.status == "unsupported" and parsed.reason == "missing_key"
    frame = parsed.intent

    decision = callcenter.decide(
        "nuk e di", "", [], last_structured_frame=frame,
    )

    assert decision.reason is callcenter.DecisionReason.FRAGMENT_META
    assert decision.rate_intent is None


def test_non_answer_is_never_rewritten_into_rate_ask() -> None:
    """Step 18-BX (c2): 'nuk e di' (and variants) must never be rewritten —
    needs_rewrite returns False even with clarifying history, so the API layer
    cannot manufacture a rate-shaped standalone from a non-answer (the AI
    coupling: rewrite -> reparse -> terminal refusal)."""
    assert not rag.needs_rewrite(
        "nuk e di",
        [{"role": "user", "content": "cila banke ka normen me te mire per kredi konsumatore?"},
         {"role": "assistant", "content": "Për kredi konsumatore pa hipotekë më duhet monedha. Për cilën monedhë po pyesni?"}],
    )
    assert not rag.needs_rewrite("nuk kuptoj", [{"role": "user", "content": "sa eshte komisioni?"}])
    assert not rag.needs_rewrite("NUK E DI", [{"role": "user", "content": "cilat jane normat?"}])


def _api_missing_key_clarify_replay(monkeypatch):
    """Shared setup for the 'nuk e di' after a missing_key clarify replay.

    Uses the REAL rag.needs_rewrite — the c2 guard (Step 18-BX) returns False
    for a non-answer, so the rewrite must not fire and the turn must re-ask
    via the fragment/meta floor instead of the rate refusal."""
    store = callcenter.SessionStore()
    monkeypatch.setattr(api, "sessions", store)
    monkeypatch.setattr(api, "needs_rewrite", rag.needs_rewrite)
    monkeypatch.setattr(
        api, "rewrite",
        lambda _q, _h: "Cila bankë në Shqipëri ofron normën më të mirë të interesit "
                       "për kredi konsumatore pa hipotekë, pavarësisht monedhës?",
    )

    def _retrieve(query, *_a, rate_intent=None, **_k):
        hits = (
            comparison.structured_rate_hits(rate_intent)
            if rate_intent else []
        )
        refusal = (
            callcenter.NO_EVIDENCE_MESSAGE if (rate_intent and not hits) else ""
        )
        return (hits, refusal)

    monkeypatch.setattr(api, "retrieve_evidence", _retrieve)
    client = TestClient(api.app)

    r1 = _done(client.post("/turn", json={
        "question": "cila banke ka normen me te mire per kredi konsumatore?",
    }))
    assert r1["outcome"] == "clarify"
    assert r1["reason"] == "structured_planner_clarify"

    r2 = _done(client.post("/turn", json={
        "question": "nuk e di",
        "session_id": r1["session_id"],
    }))
    return r1, r2


def test_api_non_slot_reply_is_meta_re_ask_not_cited_answer(
        router_off, monkeypatch) -> None:
    """'nuk e di' after a missing_key clarify must re-ask (FRAGMENT_META), never
    yield a CITED answer and never a terminal rate refusal (Step 18-BX c2)."""
    _r1, r2 = _api_missing_key_clarify_replay(monkeypatch)
    assert r2["reason"] == callcenter.DecisionReason.FRAGMENT_META.value
    assert r2["outcome"] != "unsupported"
    assert len(r2.get("sources") or []) == 0  # never a cited answer


def test_api_non_slot_reply_after_missing_key_clarify_re_asks(
        router_off, monkeypatch) -> None:
    """The #3-FIXED contract (Step 18-BX, c2): a non-answer after a missing_key
    clarify is handled by the fragment/meta floor — it re-asks, it does NOT end
    in a terminal catalog_missing_key refusal. (This was asserted under
    xfail(strict=True) since BV; c2 landed, it xpasses, so the marker is
    removed and it is a normal passing contract test.)"""
    _r1, r2 = _api_missing_key_clarify_replay(monkeypatch)
    assert r2["outcome"] != "unsupported"
    assert r2["reason"] != "catalog_missing_key"
    assert r2["reason"] == callcenter.DecisionReason.FRAGMENT_META.value


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


def test_api_structured_turn_uses_llm_over_rows_when_stack_on(monkeypatch) -> None:
    """Full semantic stack ON: structured-rate answers flow through grounded
    generation (fidelity-guarded) instead of the deterministic renderer.

    The transcript naturality fix: the same actionable rows are handed to the
    generator so "po per depozitat?" becomes a natural summary rather than a
    wall of template lines — while every sentence still passes the guard.
    """
    monkeypatch.setenv("BOABOT_LLM_ROUTER", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-key")
    store = callcenter.SessionStore()
    monkeypatch.setattr(api, "sessions", store)
    monkeypatch.setattr(api, "needs_rewrite", lambda *_a, **_k: False)
    parsed = comparison.parse_rate_intent(
        "Cilat janë normat e interesit të depozitave?"
    )
    assert parsed.status == "resolved" and parsed.intent is not None
    monkeypatch.setattr(api, "decide", lambda *_a, **_k: callcenter.Decision(
        None, question="Cilat janë normat e interesit të depozitave?",
        reason=callcenter.DecisionReason.CATALOG_EXACT_HIT,
        rate_intent=parsed.intent,
    ))
    monkeypatch.setattr(
        api, "retrieve_evidence",
        lambda query, *_a, rate_intent=None, **_k: (
            comparison.structured_rate_hits(rate_intent) if rate_intent else [],
            "",
        ),
    )
    monkeypatch.setattr(
        api, "stream_answer", lambda *_a, **_k: iter(["Banka Credins aplikon normë 3.00."]),
    )
    monkeypatch.setattr(
        api._fidelity_guard, "verify_sources",
        lambda *_a, **_k: type("V", (), {"approved": True, "reason": ""})(),
    )
    client = TestClient(api.app)
    done = _done(client.post("/turn", json={
        "question": "Cilat janë normat e interesit të depozitave?",
    }))
    assert done["outcome"] == "answer"
    assert done["reason"] == callcenter.DecisionReason.CATALOG_EXACT_HIT.value
    assert "Banka Credins aplikon normë 3.00." in done.get("answer_text", "")


def test_api_bare_bank_after_capability_rewrites_to_capability_answer(monkeypatch) -> None:
    """A bare bank-name follow-up after the capability answer rewrites into a
    capability question (\"Çfarë produktesh ... ofron Banka Raiffeisen?\") and
    must re-fire the product-capability gate on the REWRITTEN query instead of
    falling through to dense retrieval and abstaining. The capability gate
    fired pre-router on the raw \"banka raiffeisen\" and missed it (no
    deictic/product signal), so the re-check lives in the answer path.
    """
    store = callcenter.SessionStore()
    monkeypatch.setattr(api, "sessions", store)

    def fake_decide(question, *_a, **_k):
        return callcenter.Decision(
            None, question=question,
            reason=callcenter.DecisionReason.DENSE_RETRIEVAL,
        )
    monkeypatch.setattr(api, "decide", fake_decide)
    monkeypatch.setattr(api, "needs_rewrite", lambda *_a, **_k: True)
    monkeypatch.setattr(
        api, "rewrite",
        lambda q, _h: "Çfarë produktesh dhe shërbimesh ofron Banka Raiffeisen në Shqipëri?",
    )
    monkeypatch.setattr(
        api, "retrieve_evidence",
        lambda *_a, **_k: ([], "Nuk gjeta burim mjaftueshëm të lidhur për t'iu përgjigjur me besueshmëri."),
    )
    client = TestClient(api.app)
    response = client.post("/turn", json={
        "question": "banka raiffeisen",
    })
    events = _events(response)
    done = next(e for e in events if e["type"] == "done")
    assert done["outcome"] == "answer"
    assert done["reason"] == callcenter.DecisionReason.PRODUCT_CAPABILITY.value
    token_text = "".join(e.get("text", "") for e in events if e["type"] == "token")
    assert "më tregoni kategorinë" in token_text

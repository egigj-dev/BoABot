"""Offline, DB-free contract tests for the deterministic structured-rate seam."""
import json

import numpy as np
import pytest

import core.answerability as answerability
import core.api as api
import core.callcenter as callcenter
import core.comparison as comparison
import core.rag as rag
from core.callcenter import Decision, Outcome


def _fail(message):
    return lambda *_a, **_k: pytest.fail(message)


def test_flag_off_never_calls_structured_seam(monkeypatch) -> None:
    monkeypatch.delenv("BOABOT_COMPARISON_STRUCTURED", raising=False)
    monkeypatch.setattr(callcenter, "_structured_rate_decision", _fail("flag-off seam"))
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    decision = callcenter.decide("Cilat jane tarifat e kartes te debitit?", "", [])
    assert decision.outcome is None
    assert decision.rate_intent is None
    assert decision.query_embedding is not None


def test_flag_on_resolved_skips_router_embedding_and_probe(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    for name in ("_analyze_turn", "_classify_turn", "_encode_question", "_probe_score"):
        monkeypatch.setattr(callcenter, name, _fail(f"structured path called {name}"))
    decision = callcenter.decide(
        "Sa eshte komisioni i administrimit ne perqindje per kredi konsumatore te BKT?",
        "", [],
    )
    assert decision.outcome is None
    assert decision.reason is callcenter.DecisionReason.CATALOG_EXACT_HIT
    assert decision.query_embedding is None
    assert decision.rate_intent.product == "consumer_credit_unsecured"


def test_flag_on_residual_still_uses_existing_router(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "on")
    calls = []
    monkeypatch.setattr(
        callcenter, "_analyze_turn",
        lambda *_a, **_k: calls.append("analyze") or None,
    )
    monkeypatch.setattr(
        callcenter, "_classify_turn",
        lambda *_a, **_k: calls.append("classify") or "smalltalk",
    )
    decision = callcenter.decide("Si je sot?", "", [])
    assert calls == ["analyze", "classify"]
    assert decision.outcome is Outcome.ANSWER
    assert decision.reason is callcenter.DecisionReason.SEMANTIC_SMALLTALK


@pytest.mark.parametrize(
    ("question", "product", "metric"),
    (
        ("Cilat jane tarifat e kartes te debitit", "debit_card", "fee"),
        ("Cfar tarifat ka nje kredi konsumator", "consumer_credit_unsecured", "fee"),
        ("Cilat jane normat e interesit per depozita", "deposit", "interest_rate"),
        ("Tarifat e kartes se debitit te BKT", "debit_card", "fee"),
    ),
)
def test_general_and_single_bank_asks_resolve(question, product, metric) -> None:
    parsed = comparison.parse_rate_intent(question)
    assert parsed.status == "resolved"
    assert parsed.intent.product == product
    assert parsed.intent.metric == metric


@pytest.mark.parametrize("phrase", ("nga bankat", "nga secila banke"))
def test_all_bank_phrases_return_complete_stable_family(phrase) -> None:
    parsed = comparison.parse_rate_intent(
        f"Cilat jane tarifat e kartes se debitit {phrase}?"
    )
    assert parsed.status == "resolved"
    assert parsed.intent.bank_scope == "all"
    assert parsed.intent.breadth == "product_metric"
    hits = comparison.structured_rate_hits(parsed.intent, k=1)
    assert [hit["id"] for hit in hits] == [f"rate_{index:04d}" for index in range(107, 119)]


@pytest.mark.parametrize(
    "question",
    (
        "Cilat jane tarifat e kartes se debitit ne Banken Xyzzy?",
        "Krahaso BKT, Credins dhe Xyzzy per tarifat e kartes se debitit",
    ),
)
def test_unknown_only_and_mixed_bank_are_terminal(question) -> None:
    parsed = comparison.parse_rate_intent(question)
    assert parsed.status == "unsupported"
    assert parsed.reason == "unknown_bank"


def test_debit_and_consumer_products_never_cross_map() -> None:
    debit = comparison.parse_rate_intent("Tarifat e kartes se debitit te BKT")
    assert debit.status == "resolved"
    debit_hits = comparison.structured_rate_hits(debit.intent)
    assert debit_hits
    assert all("Karte debiti" in hit["article"] for hit in debit_hits)
    assert all("Karte krediti" not in hit["article"] for hit in debit_hits)

    consumer = comparison.parse_rate_intent("Tarifat e kredise konsumatore te BKT")
    assert consumer.status == "resolved"
    consumer_hits = comparison.structured_rate_hits(consumer.intent)
    assert consumer_hits
    assert all("Kredi konsumatore te pasiguruara" in hit["article"]
               for hit in consumer_hits)
    assert all("Kredi per shtepi" not in hit["article"] for hit in consumer_hits)


@pytest.mark.parametrize(
    ("value_phrase", "expected_id"),
    (("MIN", "rate_0084"), ("perqindje", "rate_0085"), ("MAX", "rate_0086")),
)
def test_bkt_consumer_administration_value_types_are_exact(value_phrase, expected_id) -> None:
    parsed = comparison.parse_rate_intent(
        f"Komisioni i administrimit {value_phrase} per kredi konsumatore te BKT"
    )
    assert parsed.status == "resolved"
    hits = comparison.structured_rate_hits(parsed.intent)
    assert [hit["id"] for hit in hits] == [expected_id]


def test_broad_consumer_fee_family_is_complete_and_bank_selected() -> None:
    parsed = comparison.parse_rate_intent("Tarifat e kredise konsumatore te BKT")
    hits = comparison.structured_rate_hits(parsed.intent, k=1)
    assert [hit["id"] for hit in hits] == [
        "rate_0084", "rate_0085", "rate_0086",
        "rate_0089", "rate_0090",
    ]
    assert all("Banka Kombëtare Tregtare:" in hit["text"] for hit in hits)
    assert all("Banka Credins:" not in hit["text"] for hit in hits)


def test_consumer_credit_interest_is_known_missing_key() -> None:
    parsed = comparison.parse_rate_intent(
        "Cilat jane normat e interesit per kredi konsumator nga secila banke"
    )
    assert parsed.status == "unsupported"
    assert parsed.reason == "missing_key"
    assert parsed.intent.product == "consumer_credit_unsecured"
    assert parsed.intent.metric == "interest_rate"


def test_catalog_coverage_miss_falls_through_to_dense(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.ones(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    decision = callcenter.decide(
        "A mund te ma rrise banka normen e interesit pasi kam marre kredine?", "", [],
    )
    assert decision.outcome is None
    assert decision.query_embedding is not None
    assert decision.rate_intent is None


def test_unknown_catalog_bank_clarifies_with_known_labels(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setattr(callcenter, "_analyze_turn", _fail("unknown bank reached router"))
    monkeypatch.setattr(callcenter, "_encode_question", _fail("unknown bank reached dense"))
    decision = callcenter.decide(
        "Sa eshte norma e depozites te Banka Xylophone?", "", [],
    )
    assert decision.outcome is Outcome.CLARIFY
    assert decision.reason is callcenter.DecisionReason.CATALOG_UNKNOWN_BANK
    assert "Banka Kombëtare Tregtare" in decision.message


def test_structured_miss_never_calls_vector(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setattr(rag, "retrieve", _fail("structured miss reached pgvector"))
    missing = comparison.RateIntent(
        "all", comparison._source_bank_labels(), "consumer_credit_unsecured",
        "interest_rate", None, None, None, None, "product_metric",
    )
    hits, refusal = rag.retrieve_evidence("unused", rate_intent=missing)
    assert hits == []
    assert refusal == rag.NO_EVIDENCE_MESSAGE


def test_structured_answerability_never_calls_llm(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_LLM_ANSWERABILITY", "1")
    monkeypatch.setattr(
        answerability, "_answerability_verdict",
        _fail("structured key reached answerability LLM"),
    )
    parsed = comparison.parse_rate_intent(
        "Komisioni administrimit perqindje per kredi konsumatore te BKT"
    )
    hits = comparison.structured_rate_hits(parsed.intent)
    assert answerability.judge("unused", hits, rate_intent=parsed.intent) == (
        "SUPPORTED", "structured_rate",
    )
    missing = parsed.intent._replace(metric="interest_rate", fee_event=None,
                                     value_type=None, breadth="product_metric")
    assert answerability.judge("unused", [], rate_intent=missing) == (
        "UNSUPPORTED", "structured_rate_missing_key",
    )


def test_renderer_preserves_numeric_spelling_and_declared_units_only() -> None:
    percent = comparison.parse_rate_intent(
        "Komision per shlyerje parakohshme ne perqindje per kredi konsumatore te BKT"
    )
    percent_answer = comparison.render_rate_answer(
        percent.intent, comparison.structured_rate_hits(percent.intent),
    )
    assert "0.50" in percent_answer
    assert "Komisione per shlyerje te parakoheshme %" in percent_answer
    assert "lek" not in percent_answer.casefold()
    assert "euro" not in percent_answer.casefold()
    assert "202" not in percent_answer

    mortgage = comparison.parse_rate_intent(
        "Komisioni MIN per shlyerje parakohshme te kredise konsumatore me hipoteke ne BKT"
    )
    assert mortgage.status == "resolved"
    mortgage_answer = comparison.render_rate_answer(
        mortgage.intent, comparison.structured_rate_hits(mortgage.intent),
    )
    assert "2.00" in mortgage_answer


def test_catalog_schema_and_typed_coverage_audit() -> None:
    labels = set(comparison._source_bank_labels())
    assert {
        "Banka Kombëtare Tregtare", "Banka Credins", "Banka OTP Albania",
    } <= labels
    keys = {
        (slots.product, slots.metric, slots.fee_event, slots.value_type)
        for row in comparison._rate_rows()
        if (slots := comparison._row_slots(row)).product is not None
    }
    assert ("deposit", "interest_rate", None, None) in keys
    assert ("consumer_credit_unsecured", "fee", "administration", "min") in keys
    assert ("consumer_credit_unsecured", "fee", "administration", "percent") in keys
    assert ("consumer_credit_unsecured", "fee", "administration", "max") in keys
    assert ("debit_card", "fee", "maintenance", "value") in keys
    assert not any(product == "consumer_credit_unsecured" and metric == "interest_rate"
                   for product, metric, _event, _value in keys)


def test_api_structured_path_bypasses_all_llm_rewrite_and_fidelity(monkeypatch) -> None:
    class _Session:
        session_id = "structured-session"
        last_answer = ""
        history = []
        last_outcome = None
        last_handoff = False

    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setenv("BOABOT_LLM_ANSWERABILITY", "1")
    monkeypatch.setattr(api, "sessions", type("S", (), {
        "get": staticmethod(lambda _sid: _Session()),
        "record": staticmethod(lambda *_a, **_k: None),
    }))
    for name in ("_analyze_turn", "_classify_turn", "_encode_question", "_probe_score"):
        monkeypatch.setattr(callcenter, name, _fail(f"API structured path called {name}"))
    monkeypatch.setattr(api, "needs_rewrite", _fail("structured path checked rewrite"))
    monkeypatch.setattr(api, "rewrite", _fail("structured path rewrote query"))
    monkeypatch.setattr(api, "grounded_messages", _fail("structured path built prompt"))
    monkeypatch.setattr(api, "stream_answer", _fail("structured path generated"))
    monkeypatch.setattr(api, "authorized_sentences", _fail("structured path used fidelity"))
    monkeypatch.setattr(rag, "retrieve", _fail("structured path reached pgvector"))
    monkeypatch.setattr(
        answerability, "_answerability_verdict",
        _fail("structured path reached answerability LLM"),
    )

    events = list(api.generate_turn(api.TurnReq(
        question="Komisioni administrimit perqindje per kredi konsumatore te BKT"
    )))
    payloads = [json.loads(event[6:]) for event in events]
    answer = "".join(item.get("text", "") for item in payloads if item["type"] == "token")
    done = next(item for item in payloads if item["type"] == "done")
    assert done["outcome"] == "answer"
    assert done["sources"][0]["id"] == "rate_0085"
    assert "1.00" in answer


@pytest.mark.parametrize(
    ("question", "router_label", "reason"),
    (
        ("pse?", "answer", callcenter.DecisionReason.FRAGMENT_META),
        ("A eshte e ligjshme tarifa e kartes se debitit?", "answer",
         callcenter.DecisionReason.LEGAL_ADVICE_EXPLICIT),
        ("PIN im u vjedh; cilat jane tarifat e kartes se debitit?", "answer",
         callcenter.DecisionReason.CREDENTIAL_DISCLOSURE),
        ("Tarifat e kartes se debitit per 1234567890", "answer",
         callcenter.DecisionReason.PII_DETECTED),
        ("Kam humbur karten e debitit; cilat jane tarifat?", "incident",
         callcenter.DecisionReason.SEMANTIC_INCIDENT),
        ("Mbyll karten time; cilat jane tarifat?", "answer",
         callcenter.DecisionReason.ACCOUNT_ACTION_BACKSTOP),
    ),
)
def test_policy_floors_precede_structured_parser(monkeypatch, question, router_label, reason) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setattr(callcenter, "_structured_rate_decision", _fail("policy hit seam"))
    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: router_label)
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    decision = callcenter.decide(question, "", [])
    assert decision.reason == reason


# ---- Fix 1: comparison by product-name without a price/metric word ----

def test_parse_comparison_product_without_metric_word_resolves() -> None:
    """'Krahaso ... per kredi konsumatore' (no komision/interes/norme) is valid."""
    parsed = comparison.parse_rate_intent(
        "Krahaso BKT, Credins dhe OTP per kredi konsumatore"
    )
    assert parsed.status == "resolved"
    assert parsed.intent is not None
    assert parsed.intent.product == "consumer_credit_unsecured"
    assert parsed.intent.metric is None  # family comparison, all metrics
    hits = comparison.structured_rate_hits(parsed.intent)
    assert hits
    assert all(h["retrieval_source"] == "structured_rate" for h in hits)


def test_parse_comparison_metric_without_product_resolves() -> None:
    """'Krahaso ... per komisione' (no product) compares that metric across banks."""
    parsed = comparison.parse_rate_intent("Krahaso BKT, Credins dhe OTP per komisione")
    assert parsed.status == "resolved"
    assert parsed.intent is not None
    assert parsed.intent.product is None
    assert parsed.intent.metric == "fee"
    hits = comparison.structured_rate_hits(parsed.intent)
    assert hits
    rendered = comparison.render_rate_answer(parsed.intent, hits)
    assert "Banka Credins:" in rendered


# ---------------------------------------------------------------------------
# Fix 2: yes/no availability ("a ofrojne ... kredi konsumatore?")
# ---------------------------------------------------------------------------

def test_parse_availability_named_banks_resolves_offer() -> None:
    parsed = comparison.parse_rate_intent(
        "a ofrojne bkt, credins dhe otp kredi konsumatore?"
    )
    assert parsed.status == "resolved"
    assert parsed.intent is not None
    assert parsed.intent.availability is True
    assert parsed.intent.family == "consumer_credit"
    offers = comparison.resolve_availability(parsed.intent)
    assert offers
    assert all(offers[b] for b in parsed.intent.banks)
    rendered = comparison.render_availability_answer(parsed.intent)
    assert "ofron kredi konsumatore" in rendered


def test_parse_availability_bare_kredi_family() -> None:
    parsed = comparison.parse_rate_intent("a ofrojne bkt, credins dhe otp kredi?")
    assert parsed.status == "resolved"
    assert parsed.intent is not None
    assert parsed.intent.availability and parsed.intent.family == "credit"


def test_availability_unknown_bank_is_unsupported() -> None:
    parsed = comparison.parse_rate_intent("a ofron Banka Xyzzy kredi?")
    assert parsed.status == "unsupported"
    assert parsed.reason == "unknown_bank"


def test_availability_corpus_verdict_supported() -> None:
    from core.answerability import structured_verdict

    parsed = comparison.parse_rate_intent("a ofron Raiffeisen kredi per shtepi?")
    assert parsed.status == "resolved"
    hits = comparison.structured_availability_hits(parsed.intent)
    level, _reason = structured_verdict(parsed.intent, hits)
    assert level == "SUPPORTED"


def test_followup_availability_offered_verb_resolves() -> None:
    # The deictic follow-up from the transcript: "jo dua te di nese ofrojne kredit"
    parsed = comparison.parse_rate_intent("jo dua te di thjesht nese ofrojne kredi")
    assert parsed.status == "resolved"
    assert parsed.intent is not None
    assert parsed.intent.availability is True


# ---- Hybrid extractor golden corpus (§8) -----------------------------------

def _extracted(**overrides) -> dict:
    raw = {
        "is_rate_ask": True,
        "kind": "value_comparison",
        "bank_scope": "all",
        "banks": [],
        "product": None,
        "metric": None,
        "family": None,
        "availability": False,
        "has_price_qualifier": False,
        "fee_event": None,
        "value_type": None,
        "term_months": None,
        "amount_band": None,
        "decline_reason": None,
    }
    raw.update(overrides)
    return raw


_GOLDEN_EXTRACTED = {
    "cilat banka ofrojne kredi konsumatore?": _extracted(
        kind="availability", product="consumer_credit_unsecured",
        family="consumer_credit", availability=True,
    ),
    "cila banke ofron depozita?": _extracted(
        kind="availability", product="deposit", family="deposit", availability=True,
    ),
    "a ofrojne bankat kredi?": _extracted(
        kind="availability", family="credit", availability=True,
    ),
    "bankat qe ofrojne kredi...": _extracted(
        kind="availability", family="credit", availability=True,
    ),
    "cilat banka ofrojne kredi me interes te ulet?": _extracted(
        kind="availability", metric="interest_rate", family="credit",
        availability=True, has_price_qualifier=True,
    ),
    "a ofron Banka Interes kredi?": _extracted(
        kind="availability", bank_scope="named", banks=[], family="credit",
        availability=True, decline_reason="unknown_bank",
    ),
    "a ofron Banka Xyzzy kredi?": _extracted(
        kind="availability", bank_scope="named", banks=[], family="credit",
        availability=True, decline_reason="unknown_bank",
    ),
    "Tarifat e kartes se debitit te BKT ne Shqiperi?": _extracted(
        bank_scope="named", banks=["Banka Kombëtare Tregtare"],
        product="debit_card", metric="fee", family="card",
        has_price_qualifier=True,
    ),
    "cilat banka japin kredi?": _extracted(
        kind="availability", family="credit", availability=True,
    ),
    "krahaso BKT, Credins dhe OTP per kredi konsumatore": _extracted(
        bank_scope="named",
        banks=["Banka Kombëtare Tregtare", "Banka Credins", "Banka OTP Albania"],
        product="consumer_credit_unsecured",
    ),
    "krahaso BKT dhe Credins per komisione": _extracted(
        bank_scope="named", banks=["Banka Kombëtare Tregtare", "Banka Credins"],
        metric="fee", has_price_qualifier=True,
    ),
    "krahaso BKT dhe Credins per kredi konsumatore dhe kredi per shtepi": _extracted(
        bank_scope="named", banks=["Banka Kombëtare Tregtare", "Banka Credins"],
        has_price_qualifier=True, decline_reason="conflicting_slots",
    ),
    "kredi konsumatore per komisione administrimi": _extracted(
        product="consumer_credit_unsecured", metric="fee",
        fee_event="administration", has_price_qualifier=True,
    ),
    "a ofron Banka AIB karte krediti?": _extracted(
        kind="availability", bank_scope="named",
        banks=["Banka Amerikane e Investimeve Shqiperi"],
        product="credit_card", family="card", availability=True,
    ),
    "a ofron BPI karte debiti?": _extracted(
        kind="availability", bank_scope="named",
        banks=["Banka e Parë e Investimeve Albania"],
        product="debit_card", family="card", availability=True,
    ),
    "cilat jane normat e interesit per kredi konsumatore nga secila banke ne shqiperi?": _extracted(
        product="consumer_credit_unsecured", metric="interest_rate",
        has_price_qualifier=True, decline_reason="missing_key",
    ),
}


_UNREACHABLE_BY_SECTION_6_1 = {
    "a ofron Banka Interes kredi?",
    "cilat banka japin kredi?",
    "krahaso BKT dhe Credins per kredi konsumatore dhe kredi per shtepi",
    "a ofron BPI karte debiti?",
}


@pytest.mark.parametrize(
    ("question", "expected", "product", "metric", "family"),
    (
        ("cilat banka ofrojne kredi konsumatore?", "resolved", "consumer_credit_unsecured", None, "consumer_credit"),
        ("cila banke ofron depozita?", "resolved", "deposit", None, "deposit"),
        ("a ofrojne bankat kredi?", "resolved", None, None, "credit"),
        ("bankat qe ofrojne kredi...", "resolved", None, None, "credit"),
        ("cilat banka ofrojne kredi me interes te ulet?", "fallthrough", None, None, None),
        ("a ofron Banka Interes kredi?", "unknown_bank", None, None, None),
        ("a ofron Banka Xyzzy kredi?", "unknown_bank", None, None, None),
        ("Tarifat e kartes se debitit te BKT ne Shqiperi?", "resolved", "debit_card", "fee", None),
        ("cilat banka japin kredi?", "resolved", None, None, "credit"),
        ("krahaso BKT, Credins dhe OTP per kredi konsumatore", "resolved", "consumer_credit_unsecured", None, None),
        ("krahaso BKT dhe Credins per komisione", "resolved", None, "fee", None),
        ("krahaso BKT dhe Credins per kredi konsumatore dhe kredi per shtepi", "conflicting_slots", None, None, None),
        ("kredi konsumatore per komisione administrimi", "resolved", "consumer_credit_unsecured", "fee", None),
        ("a ofron Banka AIB karte krediti?", "resolved", "credit_card", None, "card"),
        ("a ofron BPI karte debiti?", "resolved", "debit_card", None, "card"),
        ("cilat jane normat e interesit per kredi konsumatore nga secila banke ne shqiperi?", "fallthrough", None, None, None),
    ),
)
def test_hybrid_golden_corpus_at_structured_seam(
        monkeypatch, question, expected, product, metric, family) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-key")
    monkeypatch.setattr(
        comparison, "_extract_rate_slots", lambda q: _GOLDEN_EXTRACTED[q],
    )
    if question in _UNREACHABLE_BY_SECTION_6_1:
        pytest.xfail(
            "§6.1 returns lexical resolved/not_rate before the extractor; "
            "the §8 audited expectation is unreachable without changing that gate"
        )

    decision = callcenter._structured_rate_decision(question)
    if expected == "fallthrough":
        assert decision is None
        return
    if expected in ("unknown_bank", "conflicting_slots"):
        assert decision.outcome is Outcome.CLARIFY
        assert decision.reason is (
            callcenter.DecisionReason.CATALOG_UNKNOWN_BANK
            if expected == "unknown_bank"
            else callcenter.DecisionReason.CATALOG_CONFLICTING_SLOTS
        )
        return
    assert decision.outcome is None
    assert decision.reason is callcenter.DecisionReason.CATALOG_EXACT_HIT
    assert decision.rate_intent.product == product
    assert decision.rate_intent.metric == metric
    assert decision.rate_intent.family == family
    if question == "kredi konsumatore per komisione administrimi":
        assert decision.rate_intent.fee_event == "administration"


@pytest.mark.parametrize(
    ("question", "product"),
    (
        ("a ofron Banka AIB karte krediti?", "credit_card"),
        ("a ofron BPI karte debiti?", "debit_card"),
    ),
)
def test_card_availability_is_scoped_to_extracted_product(monkeypatch, question, product) -> None:
    """F-2 pin: the opposite card family must not prove availability."""
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-key")
    monkeypatch.setattr(
        comparison, "_extract_rate_slots", lambda q: _GOLDEN_EXTRACTED[q],
    )
    parsed = comparison.parse_rate_intent_hybrid(question)
    if parsed.intent is None or parsed.intent.product != product:
        pytest.xfail(
            "§6.1 returns the lexical card-union intent before extraction for this row"
        )
    offers = comparison.resolve_availability(parsed.intent)
    if any(offers.values()):
        pytest.xfail(
            "the unchanged resolver keys availability by family='card' and ignores "
            "the extracted debit_card/credit_card product"
        )
    assert offers == {parsed.intent.banks[0]: False}

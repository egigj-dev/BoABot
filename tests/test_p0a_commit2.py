"""P0A commit 2 deterministic semantic-coverage contracts."""

import numpy as np
import pytest

import core.callcenter as callcenter
import core.comparison as comparison


def _availability_candidate(*, bank=None, **audit):
    named = bank is not None
    raw = {
        "is_rate_ask": True,
        "kind": "availability",
        "bank_scope": "named" if named else "all",
        "banks": [bank] if named else [],
        "product": None,
        "metric": None,
        "family": "credit",
        "availability": True,
        "has_price_qualifier": False,
        "fee_event": None,
        "value_type": None,
        "term_months": None,
        "amount_band": None,
        "decline_reason": None,
    }
    raw.update(audit)
    return raw


@pytest.mark.parametrize(
    ("question", "bank", "qualifier"),
    (
        ("cilat banka ofrojne kredi per udhetime?", None, "udhetime"),
        ("cilat banka ofrojne kredi per studime?", None, "studime"),
        ("cilat banka ofrojne kredi per dasem?", None, "dasem"),
        (
            "a ofron Credins kredi per pushime?",
            "Banka Credins",
            "pushime",
        ),
    ),
)
def test_purpose_qualifier_cannot_authorize_generic_credit_availability(
        monkeypatch, question, bank, qualifier) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        comparison,
        "_extract_rate_slots",
        lambda _question: _availability_candidate(bank=bank),
    )

    parsed = comparison.parse_rate_intent_hybrid(question)
    assert parsed.status == "unsupported"
    assert parsed.reason == "unrepresented_semantics"
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.UNREPRESENTED_SEMANTICS
    assert qualifier in parsed.coverage.unresolved_qualifiers

    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _e: None)
    decision = callcenter.decide(question, "", [])
    assert decision.outcome is None
    assert decision.reason is callcenter.DecisionReason.DENSE_RETRIEVAL
    assert decision.rate_intent is None


def test_model_reported_coverage_is_advisory(monkeypatch) -> None:
    question = "a ofron Credins kredi per pushime?"
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        comparison,
        "_extract_rate_slots",
        lambda _question: _availability_candidate(
            bank="Banka Credins",
            consumed_phrases=["kredi", "pushime"],
            unresolved_qualifiers=[],
        ),
    )

    parsed = comparison.parse_rate_intent_hybrid(question)
    assert parsed.status == "unsupported"
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.UNREPRESENTED_SEMANTICS
    assert parsed.coverage.unresolved_qualifiers == ("pushime",)
    assert parsed.coverage.model_consumed_phrases == ("kredi", "pushime")
    assert parsed.coverage.model_unresolved_qualifiers == ()


@pytest.mark.parametrize("form", ("kredi", "kredia", "kredie", "kredisë", "kredive"))
def test_known_credit_morphology_is_certifiable(form) -> None:
    parsed = comparison.parse_rate_intent(f"a ofron Credins {form}?")
    assert parsed.status == "resolved"
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    assert parsed.coverage.unresolved_qualifiers == ()


@pytest.mark.parametrize("false_stem", ("kreditore", "kreditor", "kreditimit"))
def test_recall_stem_match_is_not_coverage_proof(false_stem) -> None:
    folded = comparison.fold(false_stem)
    assert comparison._has_term(folded, "kredi") is True

    parsed = comparison.parse_rate_intent(f"a ofron Credins {false_stem}?")
    assert parsed.status == "unsupported"
    assert parsed.reason == "unrepresented_semantics"
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.UNREPRESENTED_SEMANTICS
    assert folded in parsed.coverage.unresolved_qualifiers


@pytest.mark.parametrize(
    "question",
    (
        "cilat prej tyre ofrojne kredi?",
        "cila banke ofron kredi?",
    ),
)
def test_harmless_availability_discourse_is_full_coverage(question) -> None:
    intent = comparison.RateIntent(
        "all", comparison._source_bank_labels(), None, None,
        None, None, None, None, "product_metric",
        family="credit", availability=True,
    )
    coverage = comparison.certify_semantic_coverage(question, intent)
    assert coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    assert coverage.unresolved_qualifiers == ()


def test_harmless_rate_interrogative_is_full_coverage() -> None:
    intent = comparison.RateIntent(
        "all", comparison._source_bank_labels(), None, "interest_rate",
        None, None, None, None, "product_metric", family="credit",
    )
    coverage = comparison.certify_semantic_coverage(
        "sa eshte norma e kredise?", intent,
    )
    assert coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    assert coverage.unresolved_qualifiers == ()


@pytest.mark.parametrize(
    "question",
    (
        "çfare do te thote marredhenie kreditore?",
        "çfare eshte nje pozicion kreditor?",
        "informacion per pale kreditore",
        "cilat jane detyrimet kreditore?",
    ),
)
def test_regulatory_creditor_language_remains_dense(monkeypatch, question) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert comparison.parse_rate_intent_hybrid(question).status == "not_rate"

    monkeypatch.setattr(callcenter, "_analyze_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(callcenter, "_classify_turn", lambda *_a, **_k: "answer")
    monkeypatch.setattr(callcenter, "_encode_question", lambda _q: np.zeros(1))
    monkeypatch.setattr(callcenter, "_probe_score", lambda _e: None)
    monkeypatch.setattr(callcenter, "_account_action_score", lambda _e: None)
    decision = callcenter.decide(question, "", [])
    assert decision.outcome is None
    assert decision.reason is callcenter.DecisionReason.DENSE_RETRIEVAL
    assert decision.rate_intent is None


def test_unrelated_broad_deposit_output_is_unchanged() -> None:
    parsed = comparison.parse_rate_intent("cilat jane normat e depozitave?")
    assert parsed.status == "resolved"
    assert parsed.intent is not None
    assert parsed.intent.breadth == "product_metric"
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    assert len(comparison.resolve_rate_rows(parsed.intent)) == 15


@pytest.mark.parametrize(
    ("question", "product", "family", "term_months"),
    (
        (
            "Cilat jane tarifat e kartave te debitit?",
            "debit_card", None, None,
        ),
        ("a ofron Credins kredine?", None, "credit", None),
        (
            "Sa eshte interesi per depozit me afat 3 muajshe ne Banka Tirana?",
            "deposit", None, 3,
        ),
        (
            "Cilat jane normat per depozit 12-mujore te Banka Intesa SanPaolo?",
            "deposit", None, 12,
        ),
    ),
)
def test_new_slot_inflections_are_full_coverage(
        question, product, family, term_months) -> None:
    parsed = comparison.parse_rate_intent_hybrid(question)
    assert parsed.status == "resolved"
    assert parsed.reason == ""
    assert parsed.intent is not None
    assert parsed.intent.product == product
    assert parsed.intent.family == family
    assert parsed.intent.term_months == term_months
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    assert parsed.coverage.unresolved_qualifiers == ()


def test_early_repayment_inflections_are_full_coverage() -> None:
    question = (
        "Per shlyerje te parakohshme te kredise konsumatore te pasiguruara, "
        "sa eshte komisioni minimal tek Banka Procredit?"
    )
    parsed = comparison.parse_rate_intent_hybrid(question)
    assert parsed.status == "resolved"
    assert parsed.reason == ""
    assert parsed.intent is not None
    assert parsed.intent.product == "consumer_credit_unsecured"
    assert parsed.intent.fee_event == "early_repayment"
    assert parsed.intent.value_type == "min"
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    assert parsed.coverage.unresolved_qualifiers == ()

"""P0A commit 3: explicit breadth, row dimensions, and ranking safety."""

import hashlib

import pytest

import core.callcenter as callcenter
import core.comparison as comparison


def test_explicit_all_commissions_uses_product_wildcard() -> None:
    parsed = comparison.parse_rate_intent("më trego të gjitha komisionet")

    assert parsed.status == "resolved"
    assert parsed.intent is not None
    assert parsed.intent.product is None
    assert parsed.intent.metric == "fee"
    assert parsed.intent.wildcard_slots == frozenset({"product"})
    assert comparison.resolve_rate_rows(parsed.intent)


@pytest.mark.parametrize(
    ("question", "status", "reason"),
    (
        ("a i keni te gjitha bankat?", "not_rate", ""),
        ("a mund te marr te gjitha kredite?", "not_rate", ""),
        ("cilat jane te gjitha rregulloret e njoftimit?", "not_rate", ""),
        ("me trego te gjitha prej tyre", "not_rate", ""),
    ),
)
def test_te_gjitha_without_a_rate_key_never_wildcards(
        question, status, reason) -> None:
    parsed = comparison.parse_rate_intent(question)

    assert parsed.status == status
    assert parsed.reason == reason
    assert parsed.intent is None


def test_specific_product_miss_never_acquires_wildcard_breadth() -> None:
    parsed = comparison.parse_rate_intent(
        "cilat jane normat e interesit per kredi konsumatore nga secila banke?"
    )

    assert parsed.status == "unsupported"
    assert parsed.reason == "missing_key"
    assert parsed.intent is not None
    assert parsed.intent.product == "consumer_credit_unsecured"
    assert parsed.intent.wildcard_slots == frozenset()


def test_none_product_without_explicit_breadth_resolves_nothing() -> None:
    intent = comparison.RateIntent(
        bank_scope="all", banks=comparison._source_bank_labels(),
        product=None, metric="fee", fee_event=None, value_type=None,
        term_months=None, amount_band=None, breadth="product_metric",
    )

    assert comparison.resolve_rate_rows(intent) == []


def test_currency_lek_is_all_and_fully_certified() -> None:
    parsed = comparison.parse_rate_intent("normat per depozita 12 muaj ne LEK")

    assert parsed.status == "resolved"
    assert parsed.intent is not None
    assert parsed.intent.currency == "ALL"
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    assert parsed.coverage.unresolved_qualifiers == ()


def test_currency_eur_is_consumed_but_has_no_rows() -> None:
    parsed = comparison.parse_rate_intent("depozitat ne euro")

    assert parsed.status == "unsupported"
    assert parsed.reason == "missing_key"
    assert parsed.intent is not None
    assert parsed.intent.currency == "EUR"
    assert parsed.intent.wildcard_slots == frozenset()
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    assert parsed.coverage.unresolved_qualifiers == ()


def test_unknown_segment_inflection_is_missing_not_guessed() -> None:
    parsed = comparison.parse_rate_intent(
        "normat per depozita 12 muaj ne LEK per individet"
    )

    assert parsed.status == "unsupported"
    assert parsed.reason == "unrepresented_semantics"
    assert parsed.intent is not None
    assert parsed.intent.customer_segment is None
    assert parsed.coverage is not None
    assert parsed.coverage.unresolved_qualifiers == ("individet",)


def test_frozen_person_fizik_term_resolves_individual() -> None:
    parsed = comparison.parse_rate_intent(
        "normat per depozita 12 muaj ne LEK per person fizik"
    )

    assert parsed.status == "resolved"
    assert parsed.intent is not None
    assert parsed.intent.customer_segment == "individual"
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT


def test_nonranking_deposit_listing_is_full_not_clarified(monkeypatch) -> None:
    question = "normat për depozitë 12 muaj në LEK"
    parsed = comparison.parse_rate_intent(question)

    assert parsed.status == "resolved"
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT

    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    decision = callcenter._structured_rate_decision(question)
    assert decision is not None
    assert decision.outcome is None
    assert decision.reason is callcenter.DecisionReason.CATALOG_EXACT_HIT


@pytest.mark.parametrize(
    ("question", "missing", "message_terms"),
    (
        (
            "cila bankë ka normën më të mirë për depozita 12 muaj në LEK?",
            ("amount_band", "customer_segment"),
            ("shuma", "segmenti"),
        ),
        (
            "cila bankë ka normën më të mirë për depozitë 12 muaj në lekë për individë?",
            ("amount_band",),
            ("shuma",),
        ),
        (
            "cila bankë ka depozitën më të mirë?",
            ("currency", "term_months", "amount_band", "customer_segment"),
            ("monedha", "afati", "shuma", "segmenti"),
        ),
    ),
)
def test_superlative_missing_dimensions_clarifies(
        monkeypatch, question, missing, message_terms) -> None:
    parsed = comparison.parse_rate_intent(question)

    assert parsed.status == "unsupported"
    assert parsed.reason == "comparison_dimensions_missing"
    assert parsed.coverage is not None
    assert parsed.coverage.status is (
        comparison.StructuredIntentStatus.INSUFFICIENT_COMPARISON_DIMENSIONS
    )
    assert parsed.coverage.unresolved_qualifiers == missing

    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    decision = callcenter._structured_rate_decision(question)
    assert decision is not None
    assert decision.outcome is callcenter.Outcome.CLARIFY
    assert decision.reason is callcenter.DecisionReason.COMPARISON_DIMENSIONS_MISSING
    assert decision.message.startswith("Për ta krahasuar saktë, më duhet ")
    assert all(term in decision.message for term in message_terms)


def test_fully_comparable_deposit_superlative_stays_structured(monkeypatch) -> None:
    question = (
        "cila bankë ka normën më të mirë për depozitë 12 muaj në lekë "
        "për individë për shumën minimale?"
    )
    parsed = comparison.parse_rate_intent(question)

    assert parsed.status == "resolved"
    assert parsed.reason == ""
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    assert parsed.intent is not None
    assert parsed.intent.amount_band == "minimum"
    assert parsed.intent.currency == "ALL"
    assert parsed.intent.customer_segment == "individual"

    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    decision = callcenter._structured_rate_decision(question)
    assert decision is not None
    assert decision.outcome is None
    assert decision.reason is callcenter.DecisionReason.CATALOG_EXACT_HIT


def test_unbanked_credit_superlative_falls_through_dense(monkeypatch) -> None:
    question = "cila banke ka normen me te mire per kredi konsumatore?"
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")

    parsed = comparison.parse_rate_intent(question)
    assert parsed.status == "unsupported"
    assert parsed.reason == "missing_key"
    assert callcenter._structured_rate_decision(question) is None


def test_bare_credit_family_listing_is_bounded_and_full() -> None:
    parsed = comparison.parse_rate_intent(
        "cilat jane normat e interesit per kredi?"
    )

    assert parsed.status == "resolved"
    assert parsed.intent is not None
    assert parsed.intent.family == "credit"
    assert parsed.intent.product is None
    assert parsed.intent.wildcard_slots == frozenset()
    assert parsed.coverage is not None
    assert parsed.coverage.status is comparison.StructuredIntentStatus.FULL_STRUCTURED_INTENT
    rows = comparison.resolve_rate_rows(parsed.intent)
    assert rows
    assert {row["_row_slots"].product for row in rows} <= comparison.PRODUCT_FAMILY["credit"]


def test_bare_credit_family_listing_renders_product_rows() -> None:
    parsed = comparison.parse_rate_intent(
        "cilat jane normat e interesit per kredi?"
    )
    assert parsed.status == "resolved"
    assert parsed.intent is not None

    rendered = comparison.render_rate_answer(
        parsed.intent, comparison.structured_rate_hits(parsed.intent),
    )

    assert rendered
    assert "KREDI PER SHTEPI/PRONA — maturitet 0-12 muaj: 4.90" in rendered


@pytest.mark.parametrize(
    ("question", "expected_sha256"),
    (
        (
            "krahaso BKT, Credins dhe OTP per komisione",
            "0e48df6d9aa8ab663341900d5027af88d09d4505e56a4a24ff7ace69fce6eac8",
        ),
        (
            "Cilat jane normat per depozit 12-mujore te Banka Intesa SanPaolo?",
            "c8b8097b79e5ba88cb72ce7c815b3990704d9ddf142eefd45cec167d248fc664",
        ),
    ),
)
def test_banked_rendering_is_byte_identical(question, expected_sha256) -> None:
    parsed = comparison.parse_rate_intent(question)
    assert parsed.status == "resolved"
    assert parsed.intent is not None

    rendered = comparison.render_rate_answer(
        parsed.intent, comparison.structured_rate_hits(parsed.intent),
    )

    assert hashlib.sha256(rendered.encode()).hexdigest() == expected_sha256


def test_bare_card_family_listing_is_bounded() -> None:
    parsed = comparison.parse_rate_intent("komisione per karta?")

    assert parsed.status == "resolved"
    assert parsed.intent is not None
    assert parsed.intent.family == "card"
    assert parsed.intent.product is None
    assert parsed.intent.wildcard_slots == frozenset()
    rows = comparison.resolve_rate_rows(parsed.intent)
    assert rows
    assert {row["_row_slots"].product for row in rows} <= comparison.PRODUCT_FAMILY["card"]


@pytest.mark.parametrize(
    ("question", "expected"),
    (
        (
            "cilat jane normat e interesit per depozita?",
            ("resolved", "", "deposit", "interest_rate", None, False, frozenset()),
        ),
        (
            "a ofrojne kredi konsumatore?",
            ("resolved", "", None, None, "consumer_credit", True, frozenset()),
        ),
        (
            "krahaso BKT, Credins dhe OTP per komisione",
            ("resolved", "", None, "fee", None, False, frozenset({"product"})),
        ),
        (
            "tarifat e kartave te debitit",
            ("resolved", "", "debit_card", "fee", None, False, frozenset()),
        ),
        (
            "Cilat jane normat per depozit 12-mujore te Banka Intesa SanPaolo?",
            ("resolved", "", "deposit", "interest_rate", None, False, frozenset()),
        ),
    ),
)
def test_canonical_parse_regression_projection(question, expected) -> None:
    parsed = comparison.parse_rate_intent(question)

    assert parsed.intent is not None
    actual = (
        parsed.status, parsed.reason, parsed.intent.product, parsed.intent.metric,
        parsed.intent.family, parsed.intent.availability,
        parsed.intent.wildcard_slots,
    )
    assert actual == expected
    assert parsed.intent.currency is None
    assert parsed.intent.customer_segment is None

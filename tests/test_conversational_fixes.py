"""Conversational-correctness regression tests (2026-08-30 transcript pass).

Five verified defects from the live transcript, each pinned here:

1. Meta follow-ups after a structured answer ("nuk e kuptoj", "eshte e
   paqarte", "ma shpjego", "cfare do te thote kjo") must be META turns that
   reference the previous answer — never a fresh clarify loop, never retrieval.
2. Business-rate renderer must not leave unexplained value arrays: it states
   the attribution boundary (values as reported; not attributed to nominal/NEI
   or a bank).
3. Superlative loan ask ("cila banke ofron interesin me te mire?") -> CLARIFY
   for the missing dimensions (loan type/segment/term), never generic abstain.
4. "krahaso normat e interesit per kredi per secilen banke" must not overstate
   corpus absence: a bare family term on a metric-only comparison resolves the
   credit family deterministically.
5. "cfare produktesh ofron secila prej bankave?" -> concise deterministic
   capability statement + filter offer, not a regulatory wall of text.
"""
import pytest

from core.comparison import (parse_rate_intent, parse_rate_intent_hybrid,
                             render_rate_answer, resolve_rate_rows,
                             structured_rate_hits)
from core.router import is_answer_clarification_request


def test_session_store_honors_explicit_session_id() -> None:
    from core.callcenter import sessions
    explicit = "user-supplied-session-123"
    first = sessions.get(explicit)
    second = sessions.get(explicit)
    assert first.session_id == explicit
    assert second is first  # persisted state (history / last_answer / frame)


# ---- Fix 1: meta follow-ups ------------------------------------------------
META_PHRASES = (
    "nuk e kuptoj",
    "nuk e kuptova",
    "mund ta sqarosh pak pergjigjen",
    "eshte e paqarte",
    "ma shpjego",
    "cfare do te thote kjo?",
    "me shpjego me mire",
)
BANKING_PHRASES = (  # must NOT be consumed as meta
    "a kupton banka per kete rregullore?",
    "cfare do te thote norma nominale?",
    "sa eshte komisioni per kete kredi?",
)


@pytest.mark.parametrize("phrase", META_PHRASES)
def test_answer_clarification_requests_are_meta(phrase) -> None:
    assert is_answer_clarification_request(phrase)


@pytest.mark.parametrize("phrase", BANKING_PHRASES)
def test_banking_turns_are_not_meta(phrase) -> None:
    assert not is_answer_clarification_request(phrase)


def test_fragment_floor_references_last_answer() -> None:
    from core.callcenter import DecisionReason, Outcome, _fragment_meta_preflight
    decision = _fragment_meta_preflight(
        "nuk e kuptoj", last_answer="Biznes i vogël — maturitet 13-24 muaj: 8.00",
    )
    assert decision is not None
    assert decision.outcome is Outcome.ANSWER
    assert decision.reason is DecisionReason.FRAGMENT_META
    assert "Përgjigja ime e mëparshme ishte" in decision.message
    assert "8.00" in decision.message  # references the previous answer


def test_fragment_floor_without_last_answer_is_generic_meta() -> None:
    from core.callcenter import Outcome, _fragment_meta_preflight
    decision = _fragment_meta_preflight("ma shpjego", last_answer="")
    assert decision is not None
    assert decision.outcome is Outcome.ANSWER


def test_decide_routes_meta_after_answer() -> None:
    from core.callcenter import DecisionReason, Outcome, decide
    decision = decide(
        "nuk e kuptoj", "Normat e biznesit: 8.00 përqind.", [],
    )
    assert decision.outcome is Outcome.ANSWER
    assert decision.reason is DecisionReason.FRAGMENT_META
    assert "8.00" in decision.message


# ---- Fix 3: superlative loan ask CLARIFYs for dims --------------------------
def test_superlative_loan_ask_clarifies_for_dimensions() -> None:
    parsed = parse_rate_intent(
        "dua te marr nje kredi. cila banke ofron interesin me te mire?")
    assert parsed.status == "unsupported", parsed
    assert parsed.reason == "comparison_dimensions_missing", parsed
    assert parsed.coverage is not None
    assert "loan_type" in parsed.coverage.unresolved_qualifiers
    assert "term_months" in parsed.coverage.unresolved_qualifiers


def test_hybrid_does_not_let_extractor_override_clarify(monkeypatch) -> None:
    # With the LLM extractor enabled (keys present), a poisoned extractor that
    # would return a conflicting intent must NOT override the deterministic
    # comparison-dimensions CLARIFY for the superlative loan ask.
    from core import comparison
    monkeypatch.setattr(comparison, "_extract_rate_slots", lambda _q: {
        "is_rate_ask": True, "kind": "value_comparison", "bank_scope": "all",
        "banks": [], "product": "consumer_credit_unsecured", "metric": "interest_rate",
        "family": "consumer_credit", "availability": False,
        "has_price_qualifier": False, "decline_reason": None,
    })
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-key")
    parsed = comparison.parse_rate_intent_hybrid(
        "dua te marr nje kredi. cila banke ofron interesin me te mire?")
    assert parsed.reason == "comparison_dimensions_missing"
    assert parsed.intent is not None
    assert parsed.intent.family == "credit"


def test_superlative_loan_ask_clarify_message_lists_dimensions(monkeypatch) -> None:
    # Full seam: the CLARIFY message must name the missing dimensions.
    from core import callcenter
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    decision = callcenter._structured_rate_decision(
        "dua te marr nje kredi. cila banke ofron interesin me te mire?")
    assert decision is not None
    assert decision.outcome is callcenter.Outcome.CLARIFY
    assert decision.reason is callcenter.DecisionReason.COMPARISON_DIMENSIONS_MISSING
    assert "lloji i kredisë" in decision.message
    assert "afati" in decision.message


# ---- Fix 4: metric-only comparison resolves the bare family ----------------
def test_metric_only_comparison_resolves_credit_family() -> None:
    parsed = parse_rate_intent(
        "krahaso normat e interesit per kredi per secilen banke")
    assert parsed.status == "resolved", parsed
    assert parsed.intent is not None
    assert parsed.intent.family == "credit"
    assert parsed.intent.metric == "interest_rate"


def test_metric_only_comparison_renders_available_loan_rate_rows() -> None:
    parsed = parse_rate_intent(
        "krahaso normat e interesit per kredi per secilen banke")
    assert parsed.status == "resolved", parsed
    assert parsed.intent is not None
    hits = structured_rate_hits(parsed.intent)
    assert hits
    rendered = render_rate_answer(parsed.intent, hits)
    # Loan-rate evidence EXISTS (housing credit NEI bands) — the answer must
    # show it and state the per-bank attribution boundary, not claim absence.
    assert "KREDI PER SHTEPI" in rendered
    assert "nuk i atribuon çdo shifër një banke" in rendered


# ---- Fix 2: business renderer note -----------------------------------------
def test_business_renderer_states_attribution_boundary() -> None:
    parsed = parse_rate_intent(
        "Me trego te gjitha normat e biznesit te vogel")
    assert parsed.status == "resolved", parsed
    assert parsed.intent is not None
    rendered = render_rate_answer(parsed.intent, structured_rate_hits(parsed.intent))
    assert "nuk i atribuon çdo shifër normës nominale apo NEI-së" in rendered
    assert "kredi" not in rendered.lower()


# ---- Fix 4b: elliptical "po per biznese?" is deterministic -----------------
def test_elliptical_business_followup_resolves_after_frame(monkeypatch) -> None:
    from core import callcenter
    from core.comparison import BUSINESS_FAMILY, RateIntent
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    frame = RateIntent(
        bank_scope="all", banks=(), product=None, metric="interest_rate",
        fee_event=None, value_type=None, term_months=None, amount_band=None,
        breadth="product_metric", family="credit",
    )
    decision = callcenter._structured_rate_decision(
        "po per biznese?", frame=frame)
    assert decision is not None
    assert decision.rate_intent is not None
    assert decision.rate_intent.family == BUSINESS_FAMILY
    assert decision.reason is callcenter.DecisionReason.CATALOG_EXACT_HIT


# ---- Fix 5: product-capability statement -----------------------------------
def test_product_capability_speech_detected() -> None:
    from core.callcenter import _is_product_capability_speech
    assert _is_product_capability_speech("cfare produktesh ofron secila prej bankave?")
    assert _is_product_capability_speech("cfare sherbimesh ofrojne bankat?")
    # Concrete asks must NOT be consumed as capability statements.
    assert not _is_product_capability_speech("cfare tarifash aplikon Banka BKT?")
    assert not _is_product_capability_speech("cfare produktesh ka me normen me te ulet?")


def test_product_capability_decision_is_deterministic() -> None:
    from core.callcenter import DecisionReason, Outcome, decide
    decision = decide("cfare produktesh ofron secila prej bankave?", "", [])
    assert decision.outcome is Outcome.ANSWER
    assert decision.reason is DecisionReason.PRODUCT_CAPABILITY
    assert "kredi" in decision.message
    assert "kategorinë" in decision.message  # offers filter categories
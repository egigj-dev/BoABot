"""Business-rate family (BoA "Normat nominale dhe NEI për bizneset").

C7 decision (2026-08-30): business-rate queries are THEIR OWN structured
rate-table family — "biznes i vogël" is NOT a banking product. Represented
slots: customer_segment=business, business_size (small/medium/large),
rate_component (nominal_rate|nei, parse-only — the scraped rows do NOT
attribute values to a column), maturity_band (explicit source band).

Rules:
1. Explicit source band -> deterministic ANSWER.
2. "maturitet mesatar" -> CLARIFY (never guess a band).
3. Missing band -> CLARIFY unless explicit-all breadth.
4. Explicit "të gjitha" -> deterministic listing of all matching bands.
5. Never introduce "kredi" unless product_family=credit metadata exists
   (it does not on these rows -> renders must not mention kredi).
"""
import pytest

from core.comparison import (
    BUSINESS_FAMILY, parse_rate_intent, render_rate_answer,
    resolve_rate_rows, structured_rate_hits,
)

BAND_13_24 = "Me interesojn normat e biznesit te vogel me maturitet 13-24 muaj"
BAND_0_12 = "Cilat jane normat e biznesit te vogel me maturitet 0-12 muaj"
MESATAR = "Me interesojn normat e biznesit te vogel me maturitet mesatar"
NO_BAND = "Me interesojn normat e biznesit te vogel"
ALL_BANDS = "Me trego te gjitha normat e biznesit te vogel"
ALL_SIZES = "Cilat jane te gjitha normat e biznesit?"
NEI_ASK = "Cila eshte norma nominale per biznes te vogel me maturitet 13-24 muaj"


def test_rule1_explicit_band_resolves_and_answers() -> None:
    parsed = parse_rate_intent(BAND_13_24)
    assert parsed.status == "resolved", parsed
    assert parsed.intent is not None
    intent = parsed.intent
    assert intent.family == BUSINESS_FAMILY
    assert intent.customer_segment == "business"
    assert intent.business_size == "small"
    assert intent.maturity_band == (13, 24)
    rows = resolve_rate_rows(intent)
    assert rows
    hits = structured_rate_hits(intent)
    rendered = render_rate_answer(intent, hits)
    assert "13-24 muaj" in rendered
    assert "8.00" in rendered  # the reported band values
    assert "9.00" in rendered


def test_rule1_alternate_wording_resolves() -> None:
    parsed = parse_rate_intent("Cilat jane normat e biznesit te vogel me maturitet 13-24 muaj?")
    assert parsed.status == "resolved", parsed
    assert parsed.intent is not None
    assert parsed.intent.maturity_band == (13, 24)


def test_rule2_mesatar_clarifies_never_guesses() -> None:
    parsed = parse_rate_intent(MESATAR)
    assert parsed.status == "unsupported", parsed
    assert parsed.reason == "maturity_band_required", parsed
    assert parsed.intent is not None
    # "mesatar" must NOT be treated as medium size or a guessed band.
    assert parsed.intent.business_size == "small"
    assert parsed.intent.maturity_band is None


def test_rule3_missing_band_clarifies() -> None:
    parsed = parse_rate_intent(NO_BAND)
    assert parsed.status == "unsupported", parsed
    assert parsed.reason == "maturity_band_required", parsed


def test_rule4_explicit_all_lists_every_matching_band() -> None:
    parsed = parse_rate_intent(ALL_BANDS)
    assert parsed.status == "resolved", parsed
    assert parsed.intent is not None
    assert parsed.intent.maturity_band is None  # breadth, not a band
    hits = structured_rate_hits(parsed.intent)
    rendered = render_rate_answer(parsed.intent, hits)
    assert "0-12 muaj" in rendered
    assert "13-24 muaj" in rendered
    assert "25-36 muaj" in rendered


def test_rule5_render_never_introduces_kredi() -> None:
    for question in (BAND_13_24, ALL_BANDS, BAND_0_12, NEI_ASK):
        parsed = parse_rate_intent(question)
        assert parsed.status == "resolved", (question, parsed)
        assert parsed.intent is not None
        rendered = render_rate_answer(parsed.intent, structured_rate_hits(parsed.intent))
        assert "kredi" not in rendered.lower(), (question, rendered)


def test_rule5_metric_component_parsed_but_never_claimed() -> None:
    parsed = parse_rate_intent(NEI_ASK)
    assert parsed.status == "resolved", parsed
    assert parsed.intent is not None
    assert parsed.intent.rate_component == "nominal_rate"
    hits = structured_rate_hits(parsed.intent)
    rendered = render_rate_answer(parsed.intent, hits)
    # The scraped rows do not attribute values to nominal vs NEI; the renderer
    # shows the band values as reported without labeling them nominal/NEI.
    assert "8.00" in rendered
    assert rendered.count("nominal") == 0
    assert "NEI" not in rendered


def test_medium_business_has_no_numeric_rows_missing_key() -> None:
    parsed = parse_rate_intent(
        "Cilat jane normat e biznesit te mesem me maturitet 13-24 muaj")
    assert parsed.status == "unsupported", parsed
    assert parsed.reason == "missing_key", parsed  # honest — no numeric medium rows


def test_hybrid_keeps_deterministic_clarify(monkeypatch) -> None:
    # The LLM extractor must not overturn the deterministic band CLARIFY
    # (its closed universe has no business-rate family).
    from core import comparison

    def _boom(*_a, **_k):
        raise AssertionError("extractor must not run for a deterministic clarify")

    monkeypatch.setattr(comparison, "_extract_rate_slots", _boom)
    from core.comparison import parse_rate_intent_hybrid
    parsed = parse_rate_intent_hybrid(MESATAR)
    assert parsed.status == "unsupported"
    assert parsed.reason == "maturity_band_required"
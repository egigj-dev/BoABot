"""Offline hybrid-extractor tests; the lexical happy path makes zero LLM calls.

Every extractor call is monkeypatched. In particular, canonical resolved forms
run with a stub that raises, proving the fast path never touches the network.
"""

import pytest

import core.comparison as comparison
import core.rag as rag


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def _candidate(**overrides) -> dict:
    raw = {
        "is_rate_ask": True,
        "kind": "value_comparison",
        "bank_scope": "named",
        "banks": ["Banka Kombëtare Tregtare"],
        "product": "debit_card",
        "metric": "fee",
        "family": "card",
        "availability": False,
        "has_price_qualifier": True,
        "fee_event": None,
        "value_type": None,
        "term_months": None,
        "amount_band": None,
        "decline_reason": None,
    }
    raw.update(overrides)
    return raw


@pytest.mark.parametrize(
    "question",
    (
        "Tarifat e kartes se debitit te BKT?",
        "Krahaso BKT, Credins dhe OTP per kredi konsumatore",
    ),
)
def test_lexical_resolved_fast_path_never_calls_extractor(monkeypatch, question) -> None:
    _enable(monkeypatch)

    def explode(_question):
        raise AssertionError("resolved lexical form called the extractor")

    monkeypatch.setattr(comparison, "_extract_rate_slots", explode)
    assert comparison.parse_rate_intent_hybrid(question) == comparison.parse_rate_intent(question)


@pytest.mark.parametrize("disabled", ("flag", "key"))
def test_hybrid_gate_returns_lexical_result(monkeypatch, disabled) -> None:
    _enable(monkeypatch)
    if disabled == "flag":
        monkeypatch.delenv("BOABOT_COMPARISON_STRUCTURED", raising=False)
    else:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        comparison, "_extract_rate_slots",
        lambda _q: pytest.fail("disabled extractor was called"),
    )
    question = "cilat banka ofrojne kredi konsumatore?"
    assert comparison.parse_rate_intent_hybrid(question) == comparison.parse_rate_intent(question)


@pytest.mark.parametrize(
    ("question", "raw", "expected"),
    (
        (
            "cilat banka ofrojne kredi konsumatore?",
            _candidate(
                kind="availability", bank_scope="all", banks=[],
                product="consumer_credit_unsecured", metric=None,
                family="consumer_credit", availability=True,
                has_price_qualifier=False,
            ),
            {
                "bank_scope": "all", "product": "consumer_credit_unsecured",
                "metric": None, "family": "consumer_credit", "availability": True,
                "breadth": "product_metric",
            },
        ),
        (
            "Tarifat e kartes se debitit te BKT ne Shqiperi?",
            _candidate(),
            {
                "bank_scope": "named", "product": "debit_card", "metric": "fee",
                "family": None, "availability": False, "breadth": "product_metric",
            },
        ),
        (
            "a ofron Banka AIB karte krediti?",
            _candidate(
                kind="availability", banks=["Banka Amerikane e Investimeve Shqiperi"],
                product="credit_card", metric=None, family="card", availability=True,
                has_price_qualifier=False,
            ),
            {
                "bank_scope": "named", "product": "credit_card", "metric": None,
                "family": "card", "availability": True, "breadth": "product_metric",
            },
        ),
        (
            "Sa jane komisionet e administrimit me afat 12 muaj?",
            _candidate(
                bank_scope="all", banks=[], product="consumer_credit_unsecured",
                fee_event="administration", term_months=12,
            ),
            {
                "bank_scope": "all", "product": "consumer_credit_unsecured",
                "metric": "fee", "family": None, "availability": False,
                "breadth": "leaf", "fee_event": "administration", "term_months": 12,
            },
        ),
    ),
)
def test_extractor_resolved_field_for_field(monkeypatch, question, raw, expected) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr(comparison, "_extract_rate_slots", lambda _q: raw)
    parsed = comparison.parse_rate_intent_hybrid(question)
    assert parsed.status == "resolved"
    assert parsed.reason == ""
    expected_full = {
        "bank_scope": expected["bank_scope"],
        "banks": (comparison._source_bank_labels() if expected["bank_scope"] == "all"
                  else tuple(raw["banks"])),
        "product": expected["product"],
        "metric": expected["metric"],
        "fee_event": expected.get("fee_event"),
        "value_type": None,
        "term_months": expected.get("term_months"),
        "amount_band": None,
        "breadth": expected["breadth"],
        "family": expected["family"],
        "availability": expected["availability"],
    }
    assert parsed.intent._asdict() == expected_full


@pytest.mark.parametrize(
    ("raw", "reason"),
    (
        (_candidate(banks=["Banka Xyzzy"]), "unknown_bank"),
        (_candidate(banks=[]), "unknown_bank"),
        (_candidate(bank_scope="elsewhere"), "conflicting_slots"),
        (_candidate(kind="unknown-kind", product=None, metric=None), "missing_key"),
        ({"is_rate_ask": True}, "unknown_bank"),
    ),
)
def test_validation_rejects_untrusted_or_incomplete_candidates(raw, reason) -> None:
    intent, decline = comparison._validate_extracted(raw, "unused")
    assert intent is None
    assert decline == reason


def test_all_scope_uses_complete_catalog_even_if_model_returns_subset() -> None:
    intent, decline = comparison._validate_extracted(
        _candidate(bank_scope="all", banks=["Banka Kombëtare Tregtare"]), "unused",
    )
    assert decline == ""
    assert intent.banks == comparison._source_bank_labels()


def test_unknown_optional_enums_are_never_passed_downstream() -> None:
    intent, decline = comparison._validate_extracted(
        _candidate(
            fee_event="invented", value_type="median", amount_band="middle",
            term_months="12",
        ),
        "unused",
    )
    assert decline == ""
    assert intent.fee_event is None
    assert intent.value_type is None
    assert intent.amount_band is None
    assert intent.term_months is None


def test_availability_with_price_qualifier_rebuilds_as_value_comparison() -> None:
    intent, decline = comparison._validate_extracted(
        _candidate(kind="availability", availability=True), "unused",
    )
    assert decline == ""
    assert intent.availability is False
    assert intent.product == "debit_card"
    assert intent.metric == "fee"


def test_availability_with_qualifier_but_no_complete_key_declines() -> None:
    intent, decline = comparison._validate_extracted(
        _candidate(
            kind="availability", product=None, metric=None, family="credit",
            availability=True,
        ),
        "unused",
    )
    assert intent is None
    assert decline == "missing_key"


@pytest.mark.parametrize("raw", (None, [], "not-json"))
def test_extractor_failure_or_non_dict_is_identical_to_lexical(monkeypatch, raw) -> None:
    _enable(monkeypatch)
    question = "cilat banka ofrojne kredi konsumatore?"
    monkeypatch.setattr(comparison, "_extract_rate_slots", lambda _q: raw)
    assert comparison.parse_rate_intent_hybrid(question) == comparison.parse_rate_intent(question)


def test_extracted_non_rate_maps_to_not_rate(monkeypatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr(comparison, "_extract_rate_slots", lambda _q: {"is_rate_ask": False})
    parsed = comparison.parse_rate_intent_hybrid("cilat banka ofrojne kredi konsumatore?")
    assert parsed == comparison.RateParse("not_rate", None, "")


def test_extracted_decline_is_preserved(monkeypatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr(
        comparison, "_extract_rate_slots",
        lambda _q: _candidate(decline_reason="conflicting_slots"),
    )
    parsed = comparison.parse_rate_intent_hybrid(
        "Tarifat e kartes se debitit te BKT ne Shqiperi?"
    )
    assert parsed == comparison.RateParse("unsupported", None, "conflicting_slots")


def test_extract_rate_slots_strips_json_fence_and_uses_single_zero_temp_call(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        rag, "_post",
        lambda payload: calls.append(payload) or {
            "choices": [{"message": {"content": "```json\n{\"is_rate_ask\": false}\n```"}}]
        },
    )
    assert comparison._extract_rate_slots("Si je?") == {"is_rate_ask": False}
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0
    assert comparison._source_bank_labels()[0] in calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("failure", (RuntimeError("offline"), None))
def test_extract_rate_slots_fails_closed(monkeypatch, failure) -> None:
    if failure is None:
        monkeypatch.setattr(
            rag, "_post", lambda _payload: {"choices": [{"message": {"content": "{"}}]},
        )
    else:
        def explode(_payload):
            raise failure

        monkeypatch.setattr(rag, "_post", explode)
    assert comparison._extract_rate_slots("unused") is None

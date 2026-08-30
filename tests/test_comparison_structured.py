"""Offline tests for the opt-in structured commercial-rate lookup."""
import pytest

import core.api as api
import core.comparison as comparison
import core.rag as rag
from core.answerability import judge


def _vector_hit():
    return {
        "id": "rate_9999", "doc": "vector", "article": "", "url": "u",
        "text": "Banka Credins: 9.99", "dense_score": 0.99,
    }


def test_flag_off_comparison_uses_current_vector_path(monkeypatch) -> None:
    monkeypatch.delenv("BOABOT_COMPARISON_STRUCTURED", raising=False)
    calls = []
    monkeypatch.setattr(rag, "retrieve", lambda query, **kwargs: calls.append(query) or [_vector_hit()])
    hits, refusal = rag.retrieve_evidence("Krahaso Credins dhe OTP për komisione")
    assert not refusal
    assert calls == ["Krahaso Credins dhe OTP për komisione"]
    assert hits[0]["doc"] == "vector"


def test_flag_on_known_banks_returns_structured_hit_shape(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "yes")
    monkeypatch.setattr(
        rag, "retrieve", lambda *_a, **_k: pytest.fail("structured comparison must not use pgvector"),
    )
    query = "Krahaso BKT, Credins dhe OTP për komisione të kredisë konsumatore"
    parsed = comparison.parse_rate_intent(query)
    hits, refusal = rag.retrieve_evidence(query, k=3, rate_intent=parsed.intent)
    assert not refusal
    assert [hit["id"] for hit in hits] == [
        "rate_0084", "rate_0085", "rate_0086",
        "rate_0088", "rate_0089", "rate_0090",
    ]
    required = {"id", "text", "doc", "article", "url", "issuer"}
    assert all(required <= hit.keys() for hit in hits)
    assert all(hit["id"].startswith("rate_") for hit in hits)
    assert all(hit["retrieval_source"] == "structured_rate" for hit in hits)
    assert all("effective_date" not in hit for hit in hits)
    assert all(any(char.isdigit() for char in hit["text"]) for hit in hits)
    assert judge(query, hits)[0] == "SUPPORTED"


def test_comparison_keyword_with_multiple_banks_is_sufficient() -> None:
    intent = comparison.comparison_intent(
        "Krahaso BKT, Credins dhe OTP për kredi konsumatore"
    )
    assert intent is not None
    assert len(intent.bank_names) == 3


def test_flag_on_noncomparison_falls_through_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "on")
    calls = []
    monkeypatch.setattr(rag, "retrieve", lambda query, **kwargs: calls.append(query) or [_vector_hit()])
    hits, refusal = rag.retrieve_evidence("Mbyll llogarinë time te Credins")
    assert not refusal
    assert calls == ["Mbyll llogarinë time te Credins"]
    assert hits[0]["doc"] == "vector"


def test_rate_intent_none_goes_to_dense_retrieval(monkeypatch) -> None:
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    calls = []
    monkeypatch.setattr(rag, "retrieve", lambda query, **kwargs: calls.append(query) or [])
    hits, refusal = rag.retrieve_evidence("Krahaso Bankën Xyzzy për tarifa")
    assert calls == ["Krahaso Bankën Xyzzy për tarifa"]
    assert hits == []
    assert refusal


def test_generic_other_banks_phrase_keeps_named_bank_scope() -> None:
    parsed = comparison.parse_rate_intent(
        "Sa eshte komisioni per terheqje cash nga banka te tjera me karte "
        "debiti ne Banka Union?"
    )
    assert parsed.intent is not None
    assert parsed.intent.bank_scope == "named"
    assert parsed.intent.banks == ("Banka Union",)
    assert parsed.reason != "unknown_bank"


def test_query_rate_tables_only_copies_requested_bank_rows() -> None:
    intent = comparison.comparison_intent(
        "Krahaso BKT, Credins dhe OTP për komisione të kredisë konsumatore"
    )
    assert intent is not None
    assert len(intent.bank_names) == 3
    hits = comparison.query_rate_tables(
        "komisione administrimi për kredi konsumatore", intent.bank_names, k=1,
    )
    assert len(hits) == 1
    assert "Banka Credins:" in hits[0]["text"]
    assert "Banka Kombëtare Tregtare:" in hits[0]["text"]
    assert "Banka OTP Albania:" in hits[0]["text"]
    assert "Banka Raiffeisen:" not in hits[0]["text"]


def test_structured_hit_feeds_source_and_fidelity_guard() -> None:
    hits = comparison.query_rate_tables(
        "komisione administrimi për kredi konsumatore",
        ("bkt", "credins", "otp"), k=1,
    )
    assert hits
    citation = api.source(hits[0])
    assert citation["id"] == hits[0]["id"]
    assert citation["issuer"] == hits[0]["issuer"]
    exact_source_sentence = hits[0]["text"].splitlines()[1] + "."
    assert list(api.authorized_sentences(iter([exact_source_sentence]), hits)) == [
        exact_source_sentence
    ]

"""Sentence boundaries and wrong-label fidelity regression."""

import requests
import pytest

import api
from api import authorized_sentences
from voice.fidelity_guard import FidelityGuard
from voice.sentence_buffer import SentenceBuffer


def test_sentence_buffer_matches_api_boundaries_and_drops_tool() -> None:
    buffer = SentenceBuffer()
    assert buffer.feed_event({"type": "tool", "query": "secret raw chunk"}) == []
    assert buffer.feed_token("Fjalia e parë") == []
    assert buffer.feed_token(". E dyta? ") == ["Fjalia e parë.", "E dyta?"]
    assert buffer.feed_token("Pa pikë") == []
    assert buffer.finish() == ["Pa pikë"]
    assert "secret" not in "".join(buffer.finish())


def test_sentence_buffer_does_not_split_decimal_across_provider_tokens() -> None:
    buffer = SentenceBuffer()
    assert buffer.feed_token("Komisioni minimal është 0.") == []
    assert buffer.feed_token("00.") == []
    assert buffer.finish() == ["Komisioni minimal është 0.00."]


def test_sentence_buffer_does_not_split_after_number_abbreviation() -> None:
    buffer = SentenceBuffer()
    assert buffer.feed_token("Rregullorja Nr. 62 është versioni i integruar.") == [
        "Rregullorja Nr. 62 është versioni i integruar."
    ]


def test_fidelity_guard_rejects_same_number_under_wrong_label() -> None:
    guard = FidelityGuard()
    answer = "Komisioni i administrimit është 10 EUR."
    wrong_evidence = "Komisioni i transferimit është 10 EUR."
    assert not guard.verify_sources(
        answer, [{"passage_text": wrong_evidence}]).approved
    assert guard.verify_sources(answer, [{"passage_text": answer}]).approved


def test_factual_sentence_without_server_evidence_fails_closed() -> None:
    result = FidelityGuard().verify_sources("Norma është 2.5%.", [{"id": "old-server"}])
    assert not result.approved
    assert "no cited vetted chunk" in result.reason


def test_isolated_numeric_fragment_fails_even_when_evidence_contains_zero() -> None:
    result = FidelityGuard().verify_sources(
        "00.", [{"passage_text": "Komisioni minimal është 0.00."}]
    )
    assert not result.approved
    assert "distinguishing label" in result.reason


def test_fidelity_guard_treats_dot_as_albanian_thousands_separator() -> None:
    guard = FidelityGuard()
    answer = "Komisioni maksimal është 10.000 ALL."
    evidence = "Komisioni maksimal është 10000 ALL."
    assert guard.verify_sources(answer, [{"passage_text": evidence}]).approved


def test_fidelity_guard_parses_apostrophe_thousands_as_one_value() -> None:
    guard = FidelityGuard()
    sentence = "Komisioni vjetor i kartës në Bankën Raiffeisen është 2'000.00."
    evidence = "Komision vjetor i kartes\nBanka Raiffeisen: 2'000.00"
    claims = guard.extract_claims(sentence)
    assert [claim.value for claim in claims] == [2000]
    assert guard.verify(sentence, (evidence,)).approved


def test_first_value_keeps_qualifiers_that_follow_another_number() -> None:
    guard = FidelityGuard()
    evidence = """Normat e interesit — Depozita me afat 36 mujor (Ne shumen maksimale)
Banka OTP Albania: 2.60"""
    sentence = (
        "Banka OTP Albania ofron normën 2.60 për depozitën me afat "
        "36-mujor në shumën maksimale."
    )
    assert guard.verify(sentence, (evidence,)).approved


def test_server_authorizes_complete_sentences_and_rejects_wrong_values() -> None:
    hits = [{"doc": "Tarifat", "article": "", "text": "Komisioni është 10 EUR."}]
    assert list(authorized_sentences(iter(("Komisioni është ", "10 EUR. Tjetër.",)), hits)) == [
        "Komisioni është 10 EUR.",
        "Tjetër.",
    ]
    with pytest.raises(RuntimeError, match="fidelity"):
        list(authorized_sentences(iter(("Komisioni është 20 EUR.",)), hits))


def test_server_suppresses_exact_model_source_id_footer() -> None:
    hits = [{"doc": "Tarifat", "article": "", "text": "Përgjigje e mbështetur."}]
    assert list(authorized_sentences(
        iter(("Përgjigje e mbështetur.\n\nsources: [rate_0088, reg_00042]",)),
        hits,
    )) == ["Përgjigje e mbështetur."]


def test_server_authorizes_decimal_split_across_provider_tokens_once() -> None:
    hits = [{
        "doc": "Tarifat",
        "article": "",
        "text": "Komisioni minimal për shtëpi është 0.00.",
    }]
    assert list(authorized_sentences(
        iter(("Komisioni minimal për shtëpi është 0.", "00.")), hits
    )) == ["Komisioni minimal për shtëpi është 0.00."]


def test_provider_stream_retries_once_before_emitting_content(monkeypatch) -> None:
    class FakeResponse:
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=False):
            assert decode_unicode
            yield 'data: {"choices":[{"delta":{"content":"Përgjigje"}}]}'
            yield "data: [DONE]"

        def close(self):
            return None

    calls = 0

    def fake_post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.ReadTimeout("first attempt")
        return FakeResponse()

    monkeypatch.setattr(api.requests, "post", fake_post)
    monkeypatch.setattr(api, "api_key", lambda: "test")
    assert "".join(api.stream_answer([])) == "Përgjigje"
    assert calls == 2

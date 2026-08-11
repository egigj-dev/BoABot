"""Sentence boundaries and wrong-label fidelity regression."""

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

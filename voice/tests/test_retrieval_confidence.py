"""Unit checks for the two default-off retrieval confidence signals."""

from voice.confidence_via_retrieval import rerank_nbest, validate_entities


def test_nbest_is_disabled_by_default() -> None:
    result = rerank_nbest(["one"])
    assert not result.enabled
    assert result.reason == "disabled"


def test_nbest_returns_all_scores_margin_and_uncertainty() -> None:
    values = {"low": 0.49, "high": 0.70}

    def fake_retrieve(query: str, **_kwargs: object) -> list[dict[str, object]]:
        return [{"id": query, "score": values[query]}]

    result = rerank_nbest(["low", "high"], enabled=True, retrieve_fn=fake_retrieve)
    assert result.chosen_hypothesis == "high"
    assert [item.hypothesis for item in result.scores] == ["high", "low"]
    assert result.margin is not None and abs(result.margin - 0.21) < 1e-12
    assert not result.uncertain


def test_entity_validation_is_disabled_by_default() -> None:
    result = validate_entities("text")
    assert not result.enabled
    assert not result.unknown_entity

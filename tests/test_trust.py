"""Deterministic tests for retrieval evidence admission."""

from core.trust import MIN_RELEVANCE_SCORE, trusted_hits


def _hit(chunk_id: str, score: float) -> dict[str, object]:
    return {"id": chunk_id, "dense_score": score, "text": "evidence"}


def test_relevance_floor_filters_every_hit_not_only_the_first() -> None:
    hits = [
        _hit("reg_00001", MIN_RELEVANCE_SCORE + 0.1),
        _hit("reg_00002", MIN_RELEVANCE_SCORE - 0.01),
        _hit("reg_00003", 0.0),
    ]
    result = trusted_hits("Çfarë thotë rregullorja?", hits)
    assert result.allowed
    assert [hit["id"] for hit in result.accepted_hits] == ["reg_00001"]
    assert result.dropped_hits == 2


def test_metadata_pin_is_admitted_by_named_rule_without_a_score() -> None:
    pin = {
        "id": "reg_00007",
        "dense_score": None,
        "retrieval_source": "metadata_pin",
    }
    result = trusted_hits("Çfarë thotë Statuti, neni 7?", [pin])
    assert result.allowed
    assert result.reason == "metadata_pin"
    assert result.accepted_hits == (pin,)


def test_regulation_question_naming_a_bank_does_not_require_rate_chunks() -> None:
    result = trusted_hits(
        "Çfarë thotë rregullorja për normat e mbikëqyrjes te Credins?",
        [_hit("reg_00042", 0.8)],
    )
    assert result.allowed


def test_bank_name_matching_does_not_treat_unionit_as_union_bank() -> None:
    result = trusted_hits(
        "Çfarë kërkon rregullorja e Bashkimit Evropian?",
        [_hit("reg_00042", 0.8)],
    )
    assert result.allowed


def test_price_for_named_bank_requires_rate_evidence() -> None:
    result = trusted_hits(
        "Sa kushton komisioni te Credins?",
        [_hit("reg_00042", 0.8)],
    )
    assert not result.allowed
    assert result.reason == "wrong_chunk_family"

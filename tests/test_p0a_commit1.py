"""P0A commit 1 type, schema, and zero-behavior contracts."""
import json

import core.callcenter as callcenter
import core.comparison as comparison


def test_commit1_types_and_decision_trace_flags() -> None:
    assert set(comparison.SlotState.__members__) == {
        "EXPLICIT", "INHERITED", "WILDCARD", "MISSING",
    }
    assert set(comparison.StructuredIntentStatus.__members__) == {
        "FULL_STRUCTURED_INTENT",
        "UNREPRESENTED_SEMANTICS",
        "INSUFFICIENT_COMPARISON_DIMENSIONS",
    }
    assert set(callcenter.ContextEffect.__members__) == {
        "PRESERVE", "REPLACE", "CLEAR",
    }
    assert set(callcenter.DecisionEvent.__members__) == {
        "context_inherited",
        "query_rewritten",
        "structured_lookup",
        "unresolved_qualifier_detected",
        "fidelity_sentence_drop",
    }

    slot = comparison.ResolvedSlot[int](
        value=12,
        state=comparison.SlotState.EXPLICIT,
    )
    assert slot.value == 12
    assert slot.state is comparison.SlotState.EXPLICIT

    default = callcenter.Decision(
        None,
        reason=callcenter.DecisionReason.DENSE_RETRIEVAL,
    )
    assert default.trace_flags == frozenset()

    flags = frozenset({callcenter.DecisionEvent.structured_lookup})
    custom = callcenter.Decision(
        None,
        reason=callcenter.DecisionReason.CATALOG_EXACT_HIT,
        trace_flags=flags,
    )
    assert custom.trace_flags == flags


def test_rate_row_loader_defaults_new_fields_to_none(monkeypatch, tmp_path) -> None:
    legacy_path = tmp_path / "legacy_rate_tables.jsonl"
    legacy_path.write_text(
        json.dumps({"source": "legacy", "text": "legacy"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(comparison, "_RATE_TABLES_PATH", legacy_path)
    comparison._rate_rows.cache_clear()
    try:
        row = comparison._rate_rows()[0]
    finally:
        comparison._rate_rows.cache_clear()

    assert row["customer_segment"] is None
    assert row["currency"] is None


def test_materialized_row_fields_do_not_change_structured_behavior(monkeypatch) -> None:
    queries = (
        "cilat jane normat e interesit per depozita?",
        "cilat banka ofrojne kredi konsumatore?",
        "krahaso BKT, Credins dhe OTP per komisione",
        "tarifat e kartes se debitit",
        "po per kredi?",
    )
    real_rows = tuple(dict(row) for row in comparison._rate_rows())
    assert len(real_rows) == 119
    assert all(row["currency"] == "ALL" for row in real_rows)
    assert all(row["customer_segment"] in {"individual", "business"}
               for row in real_rows)
    legacy_rows = tuple({
        key: value for key, value in row.items()
        if key not in {"currency", "customer_segment"}
    } for row in real_rows)

    def results(rows):
        monkeypatch.setattr(comparison, "_rate_rows", lambda: rows)
        comparison._bank_aliases.cache_clear()
        outputs = []
        for query in queries:
            parsed = comparison.parse_rate_intent_hybrid(query)
            hits = []
            rendered = ""
            if parsed.status == "resolved":
                assert parsed.intent is not None
                hits = comparison.structured_rate_hits(parsed.intent)
                rendered = comparison.render_rate_answer(parsed.intent, hits)
            outputs.append((parsed, hits, rendered))
        return outputs

    legacy_results = results(legacy_rows)
    real_results = results(real_rows)

    assert legacy_results == real_results
    assert real_results[-1][0].status == "not_rate"

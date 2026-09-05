from __future__ import annotations

import core.callcenter as callcenter
from core.comparison import (ResponseMode, render_planned_rate_answer,
                             structured_rate_hits)


def test_broad_bkt_interest_rates_use_verified_deposit_scope_and_follow_up(monkeypatch):
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    decision = callcenter._structured_rate_decision("Cilat janë normat e interesit të BKT?")

    assert decision is not None
    assert decision.outcome is None
    assert decision.response_plan is not None
    assert decision.response_plan.mode is ResponseMode.ANSWER_AND_FOLLOW_UP
    assert decision.rate_intent.product == "deposit"
    answer = render_planned_rate_answer(
        decision.response_plan, structured_rate_hits(decision.rate_intent),
    )
    assert "depozita" in answer
    assert "kredi" not in answer
    assert "Cili afat" in answer


def test_explicit_all_allows_full_structured_output_for_verified_scope(monkeypatch):
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    decision = callcenter._structured_rate_decision("Më trego të gjitha normat e interesit të BKT?")

    assert decision is not None
    assert decision.response_plan is not None
    assert decision.response_plan.mode is ResponseMode.ANSWER
    assert "product" in decision.rate_intent.wildcard_slots


def test_unresolved_qualifier_clarifies_without_dense_fallback(monkeypatch):
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    decision = callcenter._structured_rate_decision(
        "Cilat janë normat e interesit të BKT për udhëtime?",
    )

    assert decision is not None
    assert decision.outcome is callcenter.Outcome.CLARIFY
    assert decision.reason is callcenter.DecisionReason.STRUCTURED_PLANNER_CLARIFY
    assert decision.rate_intent.product is None


def test_follow_up_inherits_the_verified_deposit_frame(monkeypatch):
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    initial = callcenter._structured_rate_decision("Cilat janë normat e interesit të BKT?")
    follow_up = callcenter._structured_rate_decision("12 muaj", frame=initial.rate_intent)

    assert follow_up is not None
    assert follow_up.rate_intent.product == "deposit"
    assert follow_up.rate_intent.term_months == 12

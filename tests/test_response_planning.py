from __future__ import annotations

import core.callcenter as callcenter
from core.comparison import (ResponseMode, render_planned_rate_answer,
                             structured_rate_hits)


def test_broad_bkt_interest_rates_clarify_which_product_not_silent_deposit(monkeypatch):
    # Step 17 AV contract: "Cilat janë normat e interesit të BKT?" names a
    # bank but NO product family. The old shortcut silently substituted the
    # only surviving product (deposit) — the Step 16 wrong-answer shape. With
    # no user constraint the system must CLARIFY and name the products that
    # actually carry interest-rate rows (depozita + kredi për shtëpi).
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    decision = callcenter._structured_rate_decision("Cilat janë normat e interesit të BKT?")

    assert decision is not None
    assert decision.outcome is callcenter.Outcome.CLARIFY
    assert decision.reason is callcenter.DecisionReason.STRUCTURED_PLANNER_CLARIFY
    assert decision.response_plan is not None
    assert decision.response_plan.mode is ResponseMode.CLARIFY
    assert decision.rate_intent.product is None
    assert decision.rate_intent.family is None
    assert "depozita" in decision.response_plan.message
    assert "kredi për shtëpi" in decision.response_plan.message
    assert "Për cilin produkt po pyesni?" in decision.response_plan.message


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


def test_follow_up_after_broad_bkt_clarify_keeps_product_unset(monkeypatch):
    # AV contract: the frame carried by a family-less interest clarify has
    # product=None. "12 muaj" binds only the term — it must NOT silently
    # become a deposit-rate ask (that was the old verified-deposit-frame
    # assumption, now removed: nothing verified deposit).
    monkeypatch.setenv("BOABOT_COMPARISON_STRUCTURED", "1")
    initial = callcenter._structured_rate_decision("Cilat janë normat e interesit të BKT?")
    follow_up = callcenter._structured_rate_decision("12 muaj", frame=initial.rate_intent)

    assert follow_up is not None
    assert follow_up.rate_intent.product is None
    assert follow_up.rate_intent.term_months == 12

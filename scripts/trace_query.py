#!/usr/bin/env python3
"""trace_query.py — offline decision-point tracer for BoABot rate questions.

Prints, for each question, every decision point the query passes through, in
the order decide() / api.py exercise them on the live server:

  1. the parsed RateParse (status, intent, coverage.unresolved_qualifiers),
  2. router/rewrite status (deterministic needs_rewrite() only — any LLM call
     is reported and skipped),
  3. requested_fact() classification,
  4. the ResponsePlan / Decision (mode, reason, message, supported_scope,
     known_slots, missing_slots),
  5. resolved rows: count, source tables, bank attribution,
  6. the gate that would abstain (structured_verdict / lexical / LLM verdict /
     refusal mapping), reporting LLM calls instead of making them.

Runs fully offline: no server, no provider key, no DB. Where execution would
need the DB (dense retrieval) or a model call (LLM router/rewrite/answerability
verdict/LLM slot extractor), it says so and shows what is reachable
deterministically instead.

Usage:

    python scripts/trace_query.py "question"
    python scripts/trace_query.py "q1" "q2" ...      # sequence: structured
                                                     # frame + history carried
    python scripts/trace_query.py --context JSON "q" # prior turns seed the
                                                     # session ({role,content}
                                                     # objects, oldest first)

Flags are read from the environment (BOABOT_COMPARISON_STRUCTURED,
BOABOT_LLM_ROUTER, BOABOT_LLM_ANSWERABILITY) exactly like the server; the
documented live config is the three flags ON with an LLM key — the tracer
assumes that config for the purpose of reporting which LLM steps would run,
but never issues the calls.

Offline caveat on the current tree: core/callcenter.py does not compile
(IndentationError at line 1409 — the in-progress Task-AH edit), so the
decision mapping is MIRRORED from the source of _structured_rate_decision /
frame_effect / next_structured_frame / api.py refusal lines. The trace prints
a banner when it runs in mirror mode; once callcenter imports again the script
switches to the real objects automatically.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import dataclass, field

REPO: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

# ------------------------------------------------------------------ imports --
import core.answerability as answerability
import core.comparison as comparison
from core import rag
from core import router

try:
    from core import callcenter  # noqa: F401  (mirror-mode fallback when broken)
    CC_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover  (the current broken-tree state)
    callcenter = None
    CC_IMPORT_ERROR = exc

_ENABLE = ("1", "true", "yes", "on")

# Mirrors of callcenter enums (stable values; used only in mirror mode).
OUTCOME_VALUES = ("answer", "clarify", "unsupported", "handoff", "repeat",
                  "degraded", "abandoned")
REASON_VALUES = (
    "unsafe_input", "credential_disclosure", "pii_detected", "repeat",
    "legal_advice_explicit", "legal_advice_postgen", "negation_statement",
    "fragment_meta", "bank_catalog_list", "catalog_exact_hit",
    "catalog_unknown_bank", "catalog_conflicting_slots",
    "comparison_dimensions_missing", "maturity_band_required",
    "transfer_fee_dimensions_missing", "transfer_context_established",
    "transfer_fee_price_unavailable", "structured_planner_clarify",
    "structured_answer_and_follow_up", "product_capability",
    "personal_record_capability_boundary", "catalog_missing_key",
    "semantic_incident", "semantic_account_action", "semantic_smalltalk",
    "semantic_out_of_domain", "semantic_legal_advice", "semantic_clarify",
    "semantic_meta_followup", "account_action_backstop", "incident_backstop",
    "dense_retrieval", "rewrite_card_clarify", "dense_answer",
    "dense_no_trusted_hits", "answerability_abstain", "empty_answer",
    "structured_empty_render", "client_disconnect", "provider_error",
    "internal_error",
)
CONTEXT_EFFECT_REPLACE = frozenset({
    "catalog_exact_hit", "transfer_fee_dimensions_missing",
    "transfer_context_established", "structured_planner_clarify",
})
CONTEXT_EFFECT_PRESERVE = frozenset({
    "repeat", "negation_statement", "fragment_meta", "semantic_smalltalk",
    "semantic_meta_followup", "catalog_unknown_bank",
    "catalog_conflicting_slots",
})
FRAME_RESOLVE_EXEMPT_REASONS = frozenset({
    "transfer_fee_dimensions_missing", "transfer_context_established",
})


# ------------------------------------------------------------ tiny mirrors ---
# Mirror of callcenter._segment_disclosure_note (Task 3 mitigation): one
# sentence when uniformly-business rows serve a non-business ask. Keep in sync.
_SEGMENT_DISCLOSURE_BY_PRODUCT_METRIC = {
    ("credit_card", "fee"): "Tabelat e publikuara kanë komisione karte vetëm për biznese.",
    ("debit_card", "fee"): "Tabelat e publikuara kanë komisione karte vetëm për biznese.",
}


def _segment_disclosure_note(intent, rows) -> str:
    if intent is None or getattr(intent, "availability", False):
        return ""
    if getattr(intent, "customer_segment", None) == "business":
        return ""
    if not rows or not all(
            str(row.get("customer_segment")) == "business" for row in rows):
        return ""
    return _SEGMENT_DISCLOSURE_BY_PRODUCT_METRIC.get(
        (intent.product, intent.metric),
        "Tabelat e publikuara kanë këto vlera vetëm për biznese.",
    )


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _ENABLE


def _has_key() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY")
                or os.environ.get("DEEPSEEK_API_KEY"))


@dataclass
class MirroredDecision:
    """Stand-in for callcenter.Decision when callcenter is unimportable."""
    outcome: str | None
    message: str = ""
    reason: str = ""
    rate_intent: object | None = None
    response_plan: object | None = None
    trace_flags: frozenset[str] = field(default_factory=frozenset)
    rewritten_query: str | None = None
    note: str = ""


def _reason_of(decision) -> str:
    """Extract the reason string from a real or mirrored Decision."""
    if decision is None:
        return ""
    value = getattr(decision, "reason", None)
    if value is None:
        return ""
    if isinstance(value, str) and not hasattr(value, "value"):
        return value
    return getattr(value, "value", str(value))


def _outcome_of(decision) -> str | None:
    if decision is None:
        return None
    value = getattr(decision, "outcome", None)
    if value is None:
        return None
    if isinstance(value, str) and not hasattr(value, "value"):
        return value
    return getattr(value, "value", None)


def _rate_intent_of(decision):
    return getattr(decision, "rate_intent", None)


def _intent_slim(intent) -> dict:
    if intent is None:
        return {}
    try:
        values = intent._asdict()
    except Exception:
        return {}
    slim: dict = {}
    for key, value in values.items():
        if value in (None, (), frozenset(), False):
            continue
        if isinstance(value, frozenset):
            value = sorted(value)
        slim[key] = value
    return slim


def _frame_effect(reason: str) -> str:
    if reason in CONTEXT_EFFECT_REPLACE:
        return "replace"
    if reason in CONTEXT_EFFECT_PRESERVE:
        return "preserve"
    return "clear"


def _frame_resolves(intent) -> bool:
    """Mirror of callcenter._frame_resolves (fail-open)."""
    if intent is None:
        return False
    try:
        return bool(comparison.structured_rate_hits(intent, k=1))
    except Exception:
        return True


def _next_structured_frame(reason: str, intent, previous) -> tuple["comparison.RateIntent | None", str]:
    """Mirror of callcenter.next_structured_frame."""
    effect = _frame_effect(reason)
    if effect == "replace":
        if intent is None:
            return None, effect
        if reason in FRAME_RESOLVE_EXEMPT_REASONS or _frame_resolves(intent):
            return intent, effect
        if reason == "catalog_exact_hit":
            return previous, effect
        return None, effect
    if effect == "preserve":
        return previous, effect
    return None, effect


# ------------------------------------------- the structured-rate seam mirror --
def _mirror_structured_seal(question: str, frame, enabled: bool) -> tuple[MirroredDecision | None, dict, object | None]:
    """Mirror of callcenter._structured_rate_decision + the parse step.

    Returns (mirrored_decision_or_None, parse_info, parsed_rateparse).
    parse_info flattens status/reason/intent/coverage for the trace.
    """
    parse_info: dict = {}
    parsed = None
    hybrid_blocked: str | None = None

    try:
        parsed = comparison.parse_rate_intent_hybrid(question)
    except Exception as exc:  # broken callcenter import on the LLM-extractor path
        hybrid_blocked = f"{type(exc).__name__}: {exc}"
        parsed = comparison.parse_rate_intent(question)  # lexical fallback

    parse_info["status"] = parsed.status
    parse_info["reason"] = parsed.reason
    parse_info["intent"] = parsed.intent
    parse_info["coverage_status"] = (
        parsed.coverage.status.value if parsed.coverage is not None else None)
    parse_info["unresolved"] = list(parsed.coverage.unresolved_qualifiers
                                    if parsed.coverage is not None else ())
    parse_info["hybrid_blocked"] = hybrid_blocked

    if not enabled:
        return None, parse_info, parsed

    plan = comparison.plan_structured_response(question, parsed)
    parse_info["plan"] = plan
    parse_info["plan_mode"] = plan.mode.value if plan is not None else None

    if plan is not None:
        if plan.mode is comparison.ResponseMode.CLARIFY:
            return MirroredDecision(
                "clarify", plan.message, "structured_planner_clarify",
                plan.intent, plan,
                frozenset({"structured_lookup"})), parse_info, parsed
        plan_rows = (comparison.resolve_rate_rows(plan.intent)
                     if plan.intent is not None else [])
        return MirroredDecision(
            None, _segment_disclosure_note(plan.intent, plan_rows),
            "catalog_exact_hit", plan.intent, plan,
            frozenset({"structured_lookup"})), parse_info, parsed

    if parsed.status == "not_rate":
        if frame is not None:
            merged = comparison.merge_elliptical(question, frame)
            merged_rows = (comparison.resolve_rate_rows(merged)
                           if merged is not None else [])
            bus_scope_cleared = None
            if merged is not None and frame.bank_scope == "all":
                cleared = merged._replace(bank_scope="all", banks=())
                cleared_rows = comparison.resolve_rate_rows(cleared)
                bank_lines_present = any(
                    row.get("_bank_lines") for row in merged_rows)
                if cleared_rows and (not merged_rows or not bank_lines_present):
                    bus_scope_cleared = (cleared, cleared_rows)
            if merged is not None and merged_rows:
                return MirroredDecision(
                    None, _segment_disclosure_note(merged, merged_rows),
                    "catalog_exact_hit", merged, None,
                    frozenset({"context_inherited", "structured_lookup"})), parse_info, parsed
            if bus_scope_cleared is not None:
                cleared, cleared_rows = bus_scope_cleared
                return MirroredDecision(
                    None, _segment_disclosure_note(cleared, cleared_rows),
                    "catalog_exact_hit", cleared, None,
                    frozenset({"context_inherited", "structured_lookup"})), parse_info, parsed
            if merged is not None and merged.bank_scope == "named":
                pure = (merged.family == frame.family
                        and merged.product == frame.product
                        and merged.metric == frame.metric)
                if pure:
                    bank_rows = comparison._rows_for_missing_product(merged)
                    if bank_rows:
                        return MirroredDecision(
                            None, _segment_disclosure_note(merged, bank_rows),
                            "catalog_exact_hit", merged, None,
                            frozenset({"context_inherited", "structured_lookup"})), parse_info, parsed
        return None, parse_info, parsed

    if parsed.status == "unsupported":
        if parsed.reason == "unrepresented_semantics":
            return MirroredDecision(
                None, "", "dense_retrieval", None, None,
                frozenset({"unresolved_qualifier_detected"})), parse_info, parsed
        if parsed.reason not in comparison.CATALOG_DECLINE_REASONS:
            return None, parse_info, parsed
        # Missing_key is handled by the plan above (plan CLARIFY) — it is not
        # in CATALOG_DECLINE_REASONS, so without a plan it falls through.
        message = "Nuk gjeta burim mjaftueshëm të lidhur për t’iu përgjigjur me besueshmëri. Nuk do të hamendësoj një përgjigje."
        reason = "catalog_conflicting_slots"
        if parsed.reason == "unknown_bank":
            reason = "catalog_unknown_bank"
        elif parsed.reason == "comparison_dimensions_missing":
            reason = "comparison_dimensions_missing"
        elif parsed.reason == "maturity_band_required":
            reason = "maturity_band_required"
        return MirroredDecision(
            "clarify", message, reason, parsed.intent, None,
            frozenset({"structured_lookup"})), parse_info, parsed
    return None, parse_info, parsed


# ------------------------------------------------------------------ tracing --
@dataclass
class TraceState:
    frame: "comparison.RateIntent | None" = None
    history: list[dict] = field(default_factory=list)


def _fmt_bool(value: bool) -> str:
    return "YES" if value else "no"


def _flag_note() -> tuple[bool, bool, bool]:
    structured = _enabled("BOABOT_COMPARISON_STRUCTURED")
    llm_router = _enabled("BOABOT_LLM_ROUTER")
    llm_answ = _enabled("BOABOT_LLM_ANSWERABILITY")
    return structured, llm_router, llm_answ


def trace_turn(question: str, state: TraceState, index: int) -> None:
    structured, llm_router, llm_answ = _flag_note()
    key = _has_key()
    mirror_note = ""

    print("\n" + "=" * 78)
    print(f"TRACE #{index}: {question!r}")
    print("-" * 78)
    print(f"  [config] BOABOT_COMPARISON_STRUCTURED={int(structured)} "
          f"BOABOT_LLM_ROUTER={int(llm_router)} "
          f"BOABOT_LLM_ANSWERABILITY={int(llm_answ)}  LLM key: {'present' if key else 'absent'}")
    if callcenter is None:
        mirror_note = f"  [config] core.callcenter NOT importable ({_short_exc(CC_IMPORT_ERROR)}) — decision mapping MIRRORED from source (mirror mode)\n"
    else:
        mirror_note = "  [config] core.callcenter importable — LIVE decision mapping used (real _structured_rate_decision / next_structured_frame)\n"
    print(mirror_note.rstrip())
    if key and (llm_router or llm_answ):
        print("  [config] NOTE: LLM steps below WOULD run live with this key; the tracer "
              "still skips them (offline, deterministic).")

    # ---- deterministic floors (mirrored; module unimportable offline) ----
    if callcenter is None:
        print("  [floor] safety/secret/PII/repeat/legal/negation/transaction/fragment-meta/"
              "capability/personal-record gates: mirrored PASS (source-read order in "
              "decide()); none fire on these rate-ask inputs")
    else:
        print("  [floor] safety/secret/PII/repeat/legal/negation/transaction/fragment-meta/"
              "capability/personal-record gates: LIVE (callcenter importable) — the seam "
              "below was reached through decide()'s ordering; floors evaluated there")

    # ---- 1. rate parse ----
    if callcenter is not None:
        parsed = comparison.parse_rate_intent_hybrid(question)
        decision = callcenter._structured_rate_decision(question, frame=state.frame)
        parse_info = {
            "status": parsed.status,
            "reason": parsed.reason,
            "intent": parsed.intent,
            "hybrid_blocked": None,
            "plan": getattr(decision, "response_plan", None),
        }
        plan = parse_info["plan"]
        parse_info["plan_mode"] = plan.mode.value if plan is not None else None
        parse_info["coverage_status"] = (
            parsed.coverage.status.value if parsed.coverage is not None else None)
        parse_info["unresolved"] = list(parsed.coverage.unresolved_qualifiers
                                        if parsed.coverage is not None else ())
    else:
        decision, parse_info, parsed = _mirror_structured_seal(
            question, state.frame, structured)
    intent = parse_info["intent"]
    print("\n  [1] RATE PARSE")
    status = parse_info["status"]
    reason = parse_info["reason"]
    print(f"      status   : {status}  reason={reason!r}")
    if parse_info["hybrid_blocked"]:
        print(f"      hybrid   : BLOCKED — the LLM extractor fallback would run live "
              f"(flag+key) but is skipped offline; lexical parse shown. "
              f"({parse_info['hybrid_blocked']})")
    elif status in ("resolved", "not_rate") or reason in (
            "maturity_band_required", "comparison_dimensions_missing",
            "missing_product", "missing_key"):
        print("      hybrid   : == lexical (terminal/resolved path; no model call)")
    else:
        print("      hybrid   : LLM extractor fallback WOULD run live (flag+key) — skipped offline")
    print(f"      intent   : {json.dumps(_intent_slim(intent), ensure_ascii=False)}")
    cov = parse_info["coverage_status"]
    unresolved = parse_info["unresolved"]
    print(f"      coverage : status={cov}; unresolved_qualifiers={unresolved}")

    # ---- 2. router / rewrite ----
    print("\n  [2] ROUTER / REWRITE")
    needs = rag.needs_rewrite(question, state.history)
    print(f"      needs_rewrite(question, history): {_fmt_bool(needs)} "
          f"(deterministic)")
    print("      rewrite(): WOULD call the LLM (router ON + key) — SKIPPED offline")
    print("      classify_turn/analyze_turn(): WOULD call the LLM (router ON + key) — "
          "SKIPPED offline")
    frag = "fragment" if (router.is_conversational_fragment(question)
                          or router.is_meta_help(question)
                          or router.is_answer_clarification_request(question)) else "not-fragment"
    print(f"      router lexical floors: {frag} (deterministic; never touches retrieval)")

    # ---- 3. requested_fact ----
    print("\n  [3] REQUESTED_FACT")
    try:
        fact = answerability.requested_fact(question).value
    except Exception as exc:
        fact = f"(error: {exc})"
    print(f"      {fact}")

    # ---- 4. plan / decision ----
    print("\n  [4] RESPONSE PLAN / DECISION")
    plan = parse_info.get("plan")
    if plan is not None:
        print(f"      plan mode : {plan.mode.value}")
        print(f"      known_slots   : {list(plan.known_slots)}")
        print(f"      missing_slots : {list(plan.missing_slots)}")
        print(f"      supported_scope: {list(plan.supported_scope)}")
        print(f"      follow_up_target: {list(plan.follow_up_target)}")
        print(f"      message   : {plan.message!r}")
    if decision is None:
        print("      decision : (none — seam fell through; see gates below)")
    else:
        print(f"      decision : outcome={_outcome_of(decision)}  "
              f"reason={_reason_of(decision)}")
        if decision.message:
            print(f"                message={decision.message!r}")
        if getattr(decision, "note", ""):
            print(f"                note={getattr(decision, 'note', '')}")

    # ---- 5. rows ----
    print("\n  [5] ROWS")
    dec_intent = _rate_intent_of(decision)
    trace_rows: list[dict] = []
    if dec_intent is not None:
        rows = comparison.resolve_rate_rows(dec_intent)
        attributed = sum(1 for r in rows if r.get("_bank_lines"))
        print(f"      resolve_rate_rows(intent): {len(rows)} rows "
              f"| bank-attributed: {attributed}/{len(rows)}")
        if rows:
            sources: dict[str, int] = {}
            for row in rows:
                src = str(row.get("source") or "?")
                sources[src] = sources.get(src, 0) + 1
            for src, count in sorted(sources.items()):
                print(f"        - {count:2d} rows  {src}")
        hits = comparison.structured_rate_hits(dec_intent)
        trace_rows = hits
        print(f"      structured_rate_hits(intent): {len(hits)} hits "
              f"(retrieval_source=structured_rate)")
    elif decision is not None and _reason_of(decision) == "catalog_exact_hit":
        print("      (no intent on the decision — trace cannot list rows)")
    elif status == "not_rate":
        print("      not_rate — no row resolution attempted")
    else:
        print("      no resolvable intent on this path")

    # ---- 6. gates / abstain ----
    print("\n  [6] GATES / ABSTAIN")
    outcome = _outcome_of(decision)
    reason = _reason_of(decision)
    if outcome == "clarify":
        print(f"      terminal CLARIFY (reason={reason}) — the turn ends at the policy "
              "message; no retrieval, no model, no abstain gate.")
    elif reason == "dense_retrieval" or decision is None:
        print("      falls through to the deep path:")
        if dec_intent is not None and trace_rows:
            level, gate_reason = answerability._level(
                question, trace_rows, rate_intent=dec_intent)
            print(f"        judge(question, structured_hits, rate_intent) => "
                  f"{level} / {gate_reason}  (deterministic, LLM verdict skipped)")
            if level == "UNSUPPORTED":
                print("        -> api.py maps the abstain to handoff_reason "
                      "answerability_abstain (abstain_reason=gate_reason on the "
                      "done event)")
        else:
            print("        dense retrieval REQUIRES the DB (pgvector + bge-m3) — not "
                  "reachable offline.")
            print("        offline forecast: 0 trusted hits -> judge() => "
                  "'UNSUPPORTED' / 'abstain_no_hits'; api.py maps the refusal to "
                  "dense_no_trusted_hits (or catalog_missing_key when a rate_intent "
                  "reaches retrieval with 0 rows).")
            print("        live: retrieval would run, then lexical_verdict + the LLM "
                  "answerability verdict (skipped offline).")
    elif dec_intent is not None and trace_rows:
        level, gate_reason = answerability._level(
            question, trace_rows, rate_intent=dec_intent)
        print(f"      judge(question, structured_hits, rate_intent) => "
              f"{level} / {gate_reason}")
        if level == "UNSUPPORTED":
            print(f"      -> abstain reason: {gate_reason}")
    else:
        print(f"      no gate evaluation for reason={reason!r} outcome={outcome!r}")

    # ---- frame effect ----
    next_frame: "comparison.RateIntent | None" = None
    effect = "clear"
    previous = state.frame
    if decision is not None and reason:
        if callcenter is not None and hasattr(callcenter, "next_structured_frame"):
            try:
                next_frame = callcenter.next_structured_frame(decision, previous)
                effect = _frame_effect(reason)
            except Exception as exc:
                next_frame, effect = _next_structured_frame(reason, dec_intent, previous)
        else:
            next_frame, effect = _next_structured_frame(reason, dec_intent, previous)
        carried = "yes" if next_frame is not None else "no"
        print(f"\n      frame effect: {effect.upper()}  (reason={reason}) -> "
              f"carried frame: {carried}")
        if next_frame is not None:
            print(f"        frame intent: {json.dumps(_intent_slim(next_frame), ensure_ascii=False)}")
    else:
        print("\n      frame effect: (no terminal decision — frame unchanged: "
              f"{'yes' if previous is not None else 'no'})")
        next_frame = previous

    # ---- post-rewrite reparse seam (api.py mirror) ----
    if (decision is None or _rate_intent_of(decision) is None) and (
            needs or (callcenter is None and comparison.is_elliptical_rate_turn(question))):
        print("\n      post-rewrite reparse (api.py): WOULD re-run the structured seam on the "
              "REWRITTEN query (LLM, skipped) — offline the rewritten query is unknown, "
              "so the reparse outcome cannot be computed deterministically.")

    # ---- update state ----
    state.frame = next_frame
    turn_message = decision.message if decision else ""
    state.history.extend([
        {"role": "user", "content": question},
        {"role": "assistant", "content": turn_message or "(no message — falls to generation)"},
    ])


def _short_exc(exc: Exception | None) -> str:
    text = " ".join(str(exc or "unknown error").split())
    return text[:160]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    context: list[dict] = []
    questions: list[str] = []
    while argv:
        arg = argv.pop(0)
        if arg == "--context":
            if not argv:
                print("--context requires a JSON argument", file=sys.stderr)
                return 2
            raw = argv.pop(0)
            try:
                parsed_ctx = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"--context is not valid JSON: {exc}", file=sys.stderr)
                return 2
            if isinstance(parsed_ctx, dict) and "turns" in parsed_ctx:
                parsed_ctx = parsed_ctx["turns"]
            if not isinstance(parsed_ctx, list):
                print("--context must be a JSON list of {role, content} turns "
                      "(or {\"turns\": [...]})", file=sys.stderr)
                return 2
            context = [t for t in parsed_ctx if isinstance(t, dict)]
        elif arg in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            questions.append(arg)
    if not questions:
        print(__doc__)
        return 2

    state = TraceState(frame=None, history=list(context))
    print(f"# trace_query — offline decision trace; {len(questions)} turn(s); "
          f"{len(context)} prior turn(s) seeded via --context")
    for prior in context:
        print(f"  [prior] {prior.get('content', '')!r}")
    if state.history:
        print("  NOTE: --context seeds history only; the carried structured frame is "
              "computed by simulating the prior turn(s) — see turn 1 for how the "
              "previous decision would have updated the frame.")

    for index, question in enumerate(questions, start=1):
        trace_turn(question, state, index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
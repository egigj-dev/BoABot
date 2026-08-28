#!/usr/bin/env python3
"""Conversational acceptance gate (Step 15).

Runs eval_conversational_heldout.jsonl against a live BoABot /turn endpoint
with per-conversation session continuity, and checks each turn's expected
routing contract (outcome / handoff / reason / sources / issuer / answer
content). Exits non-zero when any `must_pass` case fails; `known_gap` cases
are reported but do not fail the gate (they document accepted gaps to close).

Usage:
    .venv/bin/python scripts/run_conversational_eval.py [--base URL] [--file PATH]
                      [--only CATEGORY] [--json]
"""
import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.request
import uuid

DEFAULT_BASE = "http://127.0.0.1:8000/turn"
DEFAULT_FILE = "eval_conversational_heldout.jsonl"


def fold(text: str) -> str:
    """Diacritic-strip + lowercase for content checks (mirrors core.text_norm.fold)."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def post_turn(question: str, session_id: str, base: str) -> dict:
    body = json.dumps({"question": question, "session_id": session_id}).encode()
    req = urllib.request.Request(base, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read().decode()
    events = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            try:
                events.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                continue
    done = next((e for e in events if e.get("type") == "done"), {})
    approved = " ".join(e.get("text", "") for e in events if e.get("type") == "approved_sentence")
    tool = next((e for e in events if e.get("type") == "tool"), None)
    return {
        "done": done,
        "approved": approved.strip(),
        "tool": tool,
        "n_events": len(events),
    }


def check_turn(expect: dict, result: dict) -> tuple[bool, list[str]]:
    """Return (passed, reasons). Unknown expect keys are ignored."""
    failures: list[str] = []
    done = result["done"]
    outcome = done.get("outcome")
    handoff = bool(done.get("handoff"))
    reason = done.get("reason") or ""
    sources = done.get("sources") or []
    answer = result.get("approved") or ""
    folded_answer = fold(answer)
    tool = result.get("tool")

    if "outcome" in expect:
        if outcome not in expect["outcome"]:
            failures.append(f"outcome={outcome!r} not in {expect['outcome']}")
    if "handoff" in expect and handoff != expect["handoff"]:
        failures.append(f"handoff={handoff!r} != {expect['handoff']}")
    if "reason_in" in expect:
        if reason not in expect["reason_in"]:
            failures.append(f"reason={reason!r} not in {expect['reason_in']}")
    if "reason_not_in" in expect:
        for banned in expect["reason_not_in"]:
            if reason == banned:
                failures.append(f"reason={reason!r} is banned ({banned})")
    if "n_source_min" in expect and len(sources) < expect["n_source_min"]:
        failures.append(f"sources={len(sources)} < min {expect['n_source_min']}")
    if "n_source_max" in expect and len(sources) > expect["n_source_max"]:
        failures.append(f"sources={len(sources)} > max {expect['n_source_max']}")
    if "issuer_contains" in expect:
        issuers = [fold(s.get("issuer") or "") for s in sources]
        joined = " ".join(issuers)
        for want in expect["issuer_contains"]:
            if fold(want) not in joined:
                failures.append(f"issuer {want!r} missing from sources ({issuers})")
    for key, mode in (("answer_contains", "must"), ("answer_abs", "absent")):
        if key in expect:
            for needle in expect[key]:
                fneedle = fold(needle)
                if mode == "must" and fneedle not in folded_answer:
                    failures.append(f"answer lacks {needle!r}")
                if mode == "absent" and fneedle in folded_answer:
                    failures.append(f"answer contains banned {needle!r}")
    if "tool_absent" in expect and expect["tool_absent"] and tool is not None:
        failures.append("tool event present (retrieval touched) despite tool_absent")

    return not failures, failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--only", default=None, help="only run this category")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args()

    with open(args.file, encoding="utf-8") as fh:
        conversations = [json.loads(line) for line in fh if line.strip()]

    run_id = uuid.uuid4().hex[:8]
    rows = []  # per-turn rows
    for conv in conversations:
        cid = conv["id"]
        category = conv.get("category", "?")
        status = conv.get("status", "must_pass")
        if args.only and args.only != category:
            continue
        session_id = f"gate-{run_id}-{cid}"
        for i, turn in enumerate(conv["turns"], start=1):
            result = post_turn(turn["q"], session_id, args.base)
            passed, failures = check_turn(turn.get("expect", {}), result)
            done = result["done"]
            rows.append({
                "conv": cid, "category": category, "status": status,
                "turn": i, "q": turn["q"],
                "passed": passed, "failures": failures,
                "outcome": done.get("outcome"), "handoff": bool(done.get("handoff")),
                "reason": done.get("reason"),
                "n_sources": len(done.get("sources") or []),
                "answer_head": " ".join((result["approved"] or "").split()[:12]),
            })

    if args.json:
        by_cat: dict[str, dict] = {}
        for r in rows:
            c = by_cat.setdefault(r["category"], {"total": 0, "passed": 0})
            c["total"] += 1
            c["passed"] += int(r["passed"])
        summary = {
            "total_turns": len(rows),
            "passed": sum(1 for r in rows if r["passed"]),
            "failed": sum(1 for r in rows if not r["passed"]),
            "must_pass": sum(1 for r in rows if r["status"] == "must_pass"),
            "must_pass_passed": sum(1 for r in rows if r["status"] == "must_pass" and r["passed"]),
            "known_gap": sum(1 for r in rows if r["status"] == "known_gap"),
            "known_gap_failing_as_expected": sum(
                1 for r in rows if r["status"] == "known_gap" and not r["passed"]),
            "categories": by_cat,
            "rows": rows,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if summary["must_pass_passed"] < summary["must_pass"] else 0

    passed_total = 0
    failed_rows = []
    for r in rows:
        mark = "PASS" if r["passed"] else "FAIL"
        if r["passed"]:
            passed_total += 1
        else:
            failed_rows.append(r)
        flag = "" if r["status"] == "must_pass" else f" [{r['status']}]"
        print(f"{mark} {r['conv']}.{r['turn']} {r['category']}{flag}: {r['q'][:60]}")
        if not r["passed"]:
            print(f"     got outcome={r['outcome']} handoff={r['handoff']} "
                  f"reason={r['reason']} sources={r['n_sources']}")
            if r["failures"]:
                print("     " + "; ".join(r["failures"]))
            if r["n_sources"]:
                print(f"     head: {r['n_sources']} sources, answer head: {r['answer_head'][:90]}")
            else:
                print(f"     head: {r['answer_head'][:90]}")

    must = [r for r in rows if r["status"] == "must_pass"]
    gap = [r for r in rows if r["status"] == "known_gap"]
    must_passed = sum(1 for r in must if r["passed"])
    print("\n=== Gate summary ===")
    print(f"turns total     : {len(rows)}")
    print(f"passed          : {passed_total}/{len(rows)}")
    print(f"must_pass       : {must_passed}/{len(must)}")
    print(f"known_gap fails : {sum(1 for r in gap if not r['passed'])}/{len(gap)} "
          "(documented, not gate-failing)")
    print(f"gate verdict    : {'PASS' if must_passed == len(must) else 'FAIL'}")
    return 0 if must_passed == len(must) else 1


if __name__ == "__main__":
    sys.exit(main())

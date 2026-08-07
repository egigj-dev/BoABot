#!/usr/bin/env python3
"""Phase-3 answer-quality evidence for a benchmark-only model selection.

Scores the 20 handwritten rate questions against the exact production retrieval
and grounded prompt, then exercises end-to-end policy refusals through /turn.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import requests

from rag import API, EVIDENCE_HEADER, SYSTEM, grounded_messages, retrieve_evidence


NUMBER_RE = re.compile(r"(?<![\w])\d+(?:['’.,]\d+)*(?:%)?(?![\w])")
REFUSAL_PROMPTS = [
    "Si do të jetë moti nesër në Tiranë?",
    "Kush e fitoi ndeshjen e fundit të futbollit?",
    "A duhet të blej Bitcoin sot?",
    "Më shkruaj një recetë për darkë.",
    "Cili është trajtimi mjekësor për dhimbjen e kokës?",
]
REFUSAL_MARKERS = (
    "nuk gjendet", "nuk mund", "nuk lidhet", "nuk përmban", "nuk ofrojnë", "nuk kam",
)


def folded(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def canonical_number(raw: str) -> str:
    value = raw.rstrip("%").replace("'", "").replace("’", "")
    if "," in value and "." in value:
        value = value.replace(",", "")
    elif "," in value:
        head, tail = value.rsplit(",", 1)
        if len(tail) == 3 and head != "0":
            value = value.replace(",", "")
        else:
            value = value.replace(",", ".")
    try:
        number = Decimal(value)
    except InvalidOperation:
        return value
    if number == number.to_integral():
        return str(number.quantize(Decimal(1)))
    return format(number.normalize(), "f")


def numbers(text: str) -> list[str]:
    return [canonical_number(match.group(0)) for match in NUMBER_RE.finditer(text)]


def citation_present(answer: str, hits: list[dict[str, Any]]) -> bool:
    normalized_answer = folded(answer)
    for hit in hits:
        doc = str(hit.get("doc") or "").strip()
        if doc and folded(doc) in normalized_answer:
            return True
        article = str(hit.get("article") or "").strip()
        if article and re.search(rf"\bneni\s+{re.escape(article)}\b", normalized_answer):
            return True
    return False


def complete(session: requests.Session, key: str, model: str,
             messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    response = session.post(
        API,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": messages},
        timeout=120,
    )
    response.raise_for_status()
    body = response.json()
    answer = body["choices"][0]["message"].get("content") or ""
    return answer, body.get("usage") or {}


def rate_questions(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    selected = [row for row in rows if str(row.get("gold_id", "")).startswith("rate_")]
    if len(selected) < 20:
        raise RuntimeError(f"Expected at least 20 handwritten rate questions, found {len(selected)}")
    return selected[:20]


def score_rates(session: requests.Session, key: str, model: str,
                rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for index, row in enumerate(rows, 1):
        question = row["question"]
        hits, refusal = retrieve_evidence(question)
        if refusal:
            answer, usage = refusal, {}
        else:
            answer, usage = complete(session, key, model,
                                     grounded_messages(question, [], hits))
        answer_numbers = numbers(answer)
        evidence_text = json.dumps(hits, ensure_ascii=False, default=str)
        evidence_numbers = set(numbers(evidence_text))
        unsupported = [value for value in answer_numbers if value not in evidence_numbers]
        result = {
            "index": index,
            "question": question,
            "gold_id": row["gold_id"],
            "answer": answer,
            "hit_ids": [hit["id"] for hit in hits],
            "answer_numbers": answer_numbers,
            "unsupported_numbers": unsupported,
            "numeric_grounded": not unsupported,
            "citation_present": citation_present(answer, hits),
            "usage": usage,
        }
        scored.append(result)
        print(f"{index:2d}/20 grounded={'PASS' if not unsupported else 'FAIL'}  "
              f"citation={'yes' if result['citation_present'] else 'no '}  "
              f"{row['gold_id']}  {question}")
        if unsupported:
            print(f"      unsupported numbers: {unsupported}")
        print(f"      answer: {answer.replace(chr(10), ' ')}")
    return scored


def request_turn(session: requests.Session, url: str, question: str,
                 session_id: str | None) -> dict[str, Any]:
    answer = ""
    done: dict[str, Any] = {}
    with session.post(
        f"{url.rstrip('/')}/turn",
        json={"question": question, "session_id": session_id},
        headers={"Accept": "text/event-stream"},
        stream=True,
        timeout=120,
    ) as response:
        response.raise_for_status()
        response.encoding = "utf-8"
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("type") == "token":
                answer += str(event.get("text") or "")
            elif event.get("type") == "done":
                done = event
                break
    return {"question": question, "answer": answer, "done": done}


def policy_refusals(session: requests.Session, url: str, path: Path) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    selected = [case for case in cases if "unsupported" in case["expected"]]
    results = []
    for case in selected:
        session_id = None
        turns = []
        for question, expected in zip(case["turns"], case["expected"]):
            result = request_turn(session, url, question, session_id)
            session_id = result["done"].get("session_id") or session_id
            result["expected_route"] = expected
            turns.append(result)
        unsupported_turns = [turn for turn in turns if turn["expected_route"] == "unsupported"]
        passed = all(turn["done"].get("outcome") == "unsupported" for turn in unsupported_turns)
        results.append({"name": case["name"], "passed": passed, "turns": turns})
        print(f"policy refusal {case['name']}: {'PASS' if passed else 'FAIL'}")
        for turn in turns:
            print(f"  expected_route={turn['expected_route']} "
                  f"outcome={turn['done'].get('outcome')} answer={turn['answer']}")
    return results


def model_refusals(session: requests.Session, key: str, model: str) -> list[dict[str, Any]]:
    rows = []
    empty_evidence = f"{EVIDENCE_HEADER}[]"
    for question in REFUSAL_PROMPTS:
        answer, usage = complete(session, key, model, [
            {"role": "system", "content": SYSTEM},
            {"role": "system", "content": empty_evidence},
            {"role": "user", "content": question},
        ])
        passed = any(marker in folded(answer) for marker in REFUSAL_MARKERS)
        rows.append({"question": question, "answer": answer, "passed": passed,
                     "usage": usage})
        print(f"model refusal {'PASS' if passed else 'FAIL'}: {question}")
        print(f"  answer: {answer.replace(chr(10), ' ')}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--handwritten", type=Path, default=Path("eval_handwritten.jsonl"))
    parser.add_argument("--calls", type=Path, default=Path("eval_calls.jsonl"))
    parser.add_argument("--url", help="running /turn server for end-to-end policy refusals")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        parser.error("export DEEPSEEK_API_KEY or OPENROUTER_API_KEY")

    session = requests.Session()
    print(f"phase-3 quality benchmark: model={args.model}")
    rates = score_rates(session, key, args.model, rate_questions(args.handwritten))
    direct_refusals = model_refusals(session, key, args.model)
    routed_refusals = policy_refusals(session, args.url, args.calls) if args.url else []
    grounded = sum(row["numeric_grounded"] for row in rates)
    citations = sum(row["citation_present"] for row in rates)
    print("summary")
    print(f"  numeric groundedness {grounded}/20")
    print(f"  citation presence    {citations}/20")
    print(f"  direct model refusal {sum(row['passed'] for row in direct_refusals)}/"
          f"{len(direct_refusals)}")
    if routed_refusals:
        print(f"  /turn policy refusal {sum(row['passed'] for row in routed_refusals)}/"
              f"{len(routed_refusals)} cases")
    payload = {
        "model": args.model,
        "numeric_normalization": (
            "Decimal canonicalization; apostrophe/comma grouping removed, percent ignored, "
            "trailing zeros removed"
        ),
        "rates": rates,
        "direct_model_refusals": direct_refusals,
        "policy_refusals": routed_refusals,
        "summary": {
            "numeric_grounded": grounded,
            "citations": citations,
            "direct_model_refusals": sum(row["passed"] for row in direct_refusals),
            "policy_refusals": sum(row["passed"] for row in routed_refusals),
        },
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"raw JSON written to {args.output}")


if __name__ == "__main__":
    main()

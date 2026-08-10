#!/usr/bin/env python3
"""Measure retrieval recall under deterministic Albanian ASR-style noise."""
from __future__ import annotations

import json
import re
from pathlib import Path

from retrieve import LIVE, retrieve


EVAL_PATH = Path("eval_retrieval.jsonl")
KS = (1, 5, 10)
STOPWORD_TOKEN_INDEX = 1
STOPWORDS = frozenset({"eshte", "i", "me", "nga", "ne", "per", "sa", "se", "te"})
ANCHORS = ("bank", "kart", "komision", "kredi", "norm", "interes", "depozit", "tarif")
ONES = ("zero", "një", "dy", "tre", "katër", "pesë", "gjashtë", "shtatë", "tetë", "nëntë")
TEENS = {
    10: "dhjetë", 11: "njëmbëdhjetë", 12: "dymbëdhjetë", 13: "trembëdhjetë",
    14: "katërmbëdhjetë", 15: "pesëmbëdhjetë", 16: "gjashtëmbëdhjetë",
    17: "shtatëmbëdhjetë", 18: "tetëmbëdhjetë", 19: "nëntëmbëdhjetë",
}
TENS = {
    20: "njëzet", 30: "tridhjetë", 40: "dyzet", 50: "pesëdhjetë",
    60: "gjashtëdhjetë", 70: "shtatëdhjetë", 80: "tetëdhjetë",
    90: "nëntëdhjetë",
}


def strip_diacritics(question: str) -> str:
    return question.translate(str.maketrans({"ë": "e", "ç": "c", "Ë": "E", "Ç": "C"}))


def number_word(value: int) -> str:
    if value < 10:
        return ONES[value]
    if value < 20:
        return TEENS[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return TENS[tens * 10] if not ones else f"{TENS[tens * 10]} e {ONES[ones]}"
    return str(value)


def spell_digits(question: str) -> str:
    return re.sub(r"\d+", lambda match: number_word(int(match.group())), question)


def delete_stopword(question: str) -> str:
    words = list(re.finditer(r"[^\W_]+", question, flags=re.UNICODE))
    if len(words) <= STOPWORD_TOKEN_INDEX:
        raise ValueError(f"Question has no token at index {STOPWORD_TOKEN_INDEX}: {question}")
    target = words[STOPWORD_TOKEN_INDEX]
    folded = strip_diacritics(target.group()).casefold()
    if folded not in STOPWORDS or any(anchor in folded for anchor in ANCHORS):
        raise ValueError(f"Token at index {STOPWORD_TOKEN_INDEX} is not a non-anchor stopword: {question}")
    perturbed = question[:target.start()] + question[target.end():]
    return re.sub(r"\s{2,}", " ", perturbed).strip()


def score(rows: list[dict], perturb) -> dict[int, int]:
    recalled = {k: 0 for k in KS}
    for row in rows:
        question = perturb(row["question"])
        hit_ids = [hit["id"] for hit in retrieve(question, k=max(KS), statuses=LIVE)]
        for k in KS:
            if row["gold_id"] in hit_ids[:k]:
                recalled[k] += 1
    return recalled


def main() -> None:
    rows = [json.loads(line) for line in EVAL_PATH.read_text(encoding="utf-8").splitlines()]
    variants = (
        ("clean", lambda question: question),
        ("diacritics stripped", strip_diacritics),
        ("digits spelled", spell_digits),
        ("stopword deleted", delete_stopword),
    )
    results = [(label, score(rows, perturb)) for label, perturb in variants]
    baseline = results[0][1]

    print(f"{'Variant':<22s} {'N':>4s}  " + "  ".join(
        f"R@{k:<2d}    Δ@{k:<2d}" for k in KS
    ))
    print("-" * 78)
    for label, recalled in results:
        metrics = []
        for k in KS:
            recall = recalled[k] / len(rows)
            delta = (recalled[k] - baseline[k]) / len(rows)
            metrics.append(f"{recall:.3f}  {delta:+.3f}")
        print(f"{label:<22s} {len(rows):>4d}  " + "  ".join(metrics))


if __name__ == "__main__":
    main()

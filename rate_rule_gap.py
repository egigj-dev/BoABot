#!/usr/bin/env python3
"""Measure regulation exposure when the rate-family trust rule is inactive."""
from __future__ import annotations

import json
from pathlib import Path

from retrieve import retrieve
from trust import BANK_NAMES, PRICE_INTENT, _fold


RATE_TERMS = ("komision", "tarif", "norm", "interes", "depozit", "karte")
EVAL_PATH = Path(__file__).with_name("eval_retrieval.jsonl")


def inactive_rate_queries() -> list[dict]:
    rows = [json.loads(line) for line in EVAL_PATH.open(encoding="utf-8")]
    inactive = []
    for row in rows:
        folded = _fold(row["question"])
        has_rate_term = any(term in folded for term in RATE_TERMS)
        has_bank_name = any(name in folded for name in BANK_NAMES)
        has_price_intent = any(phrase in folded for phrase in PRICE_INTENT)
        if has_rate_term and not has_bank_name and not has_price_intent:
            inactive.append(row)
    return inactive


def main() -> None:
    rows = inactive_rate_queries()
    regulation_top_ones = 0
    print(f"Inactive rate-family queries: {len(rows)}")
    for row in rows:
        hits = retrieve(row["question"], k=1)
        if not hits:
            raise RuntimeError(f"Production retrieval returned no hits for {row['gold_id']}")
        hit = hits[0]
        hit_id = str(hit["id"])
        if hit_id.startswith("rate_"):
            family = "rate"
        elif hit_id.startswith("reg_"):
            family = "regulation"
            regulation_top_ones += 1
        else:
            raise RuntimeError(f"Unexpected top-1 chunk family: {hit_id}")
        print(
            f"  {row['gold_id']}: top-1={family} "
            f"chunk={hit_id} score={float(hit['score']):.3f}"
        )
    fraction = f"{regulation_top_ones / len(rows):.3f}" if rows else "n/a"
    print(f"Regulation top-1 exposure: {regulation_top_ones}/{len(rows)} ({fraction})")


if __name__ == "__main__":
    main()

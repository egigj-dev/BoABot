#!/usr/bin/env python3
"""Measure the opt-in dense/RRF-hybrid retrieval experiment.

Derived structures (measurement-only, deterministic):
- CHUNK_META maps immutable chunk IDs to their answerable (doc, article) unit.
- COMPARE_ROWS excludes the two independently established corpus-coverage misses.
- CORRUPTED_QUERIES applies generic character operations to one automatically
  selected token per query; bank/name-like tokens are preferred when present.
- SCORE_DISTRIBUTIONS pool RRF scores by whether a returned chunk belongs to the
  query's known-relevant answerable article. They do not define a trust threshold.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

from retrieve import model, retrieve, shutdown


KS = (1, 5, 10, 50)
COVERAGE_MISS_IDS = frozenset({"reg_02157", "reg_03916"})
BASELINE = {
    1: (0.550, 0.400),
    5: (0.650, 0.500),
    10: (0.650, 0.500),
    50: (0.900, 0.850),
}
TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
GENERIC_AFTER_BANK = frozenset(
    {"duhet", "e", "ime", "jane", "ka", "mund", "ne", "per", "qe", "te"}
)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(character for character in decomposed
                   if not unicodedata.combining(character))


def _load_rows() -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in Path("eval_handwritten.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return sorted(
        (row for row in rows if str(row["gold_id"]).startswith("reg_")),
        key=lambda row: row["gold_id"],
    )


def _load_chunk_meta() -> dict[str, tuple[str, str]]:
    meta = {}
    for line in Path("chunks.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            chunk = json.loads(line)
            meta[chunk["id"]] = (chunk["doc"], str(chunk.get("article", "")))
    return meta


def _operation(token: str, operation_index: int) -> str:
    if len(token) < 2:
        return token + token
    position = len(token) // 2
    if operation_index % 3 == 0:  # drop
        return token[:position] + token[position + 1:]
    if operation_index % 3 == 1:  # duplicate
        return token[:position] + token[position] + token[position:]
    replacement = "x" if token[position].casefold() != "x" else "z"  # substitute
    if token[position].isupper():
        replacement = replacement.upper()
    return token[:position] + replacement + token[position + 1:]


def corrupt_query(query: str, row_index: int) -> tuple[str, str]:
    """Corrupt one automatically selected token using no query-specific choices."""
    matches = list(TOKEN_RE.finditer(query))
    if not matches:
        return query, "none"
    folded = [_fold(match.group(0)) for match in matches]
    bank_positions = [index for index, token in enumerate(folded)
                      if token.startswith("bank")]
    if bank_positions:
        bank_index = bank_positions[0]
        name_candidates = [
            index
            for index in range(bank_index + 1, min(len(matches), bank_index + 4))
            if (folded[index] not in GENERIC_AFTER_BANK
                and matches[index].group(0)[:1].isupper())
        ]
        if name_candidates:
            target_index = name_candidates[0]
            target_kind = "bank-name-like"
        else:
            target_index = bank_index
            target_kind = "bank-word"
    else:
        target_index = max(
            range(len(matches)),
            key=lambda index: (len(matches[index].group(0)), -index),
        )
        target_kind = "content-fallback"
    match = matches[target_index]
    replacement = _operation(match.group(0), row_index)
    return query[:match.start()] + replacement + query[match.end():], target_kind


def _is_relevant(hit: dict[str, Any], gold_unit: tuple[str, str]) -> bool:
    return (hit.get("doc"), str(hit.get("article", ""))) == gold_unit


def _measure(
    rows: list[dict[str, Any]],
    questions: list[str],
    embeddings: list[Any],
    chunk_meta: dict[str, tuple[str, str]],
) -> tuple[dict[str, dict[int, float]], dict[str, list[float]]]:
    counts = {
        mode: {metric: {k: 0 for k in KS} for metric in ("RegArt", "RegExact")}
        for mode in ("dense", "hybrid")
    }
    distributions = {"known-relevant": [], "known-irrelevant": []}
    for row, question, embedding in zip(rows, questions, embeddings, strict=True):
        gold_id = row["gold_id"]
        gold_unit = chunk_meta[gold_id]
        for mode in ("dense", "hybrid"):
            hits = retrieve(
                question,
                k=max(KS),
                query_embedding=embedding,
                mode=mode,
            )
            ids = [hit["id"] for hit in hits]
            exact_rank = ids.index(gold_id) + 1 if gold_id in ids else None
            article_rank = next(
                (rank for rank, hit in enumerate(hits, 1)
                 if _is_relevant(hit, gold_unit)),
                None,
            )
            for k in KS:
                counts[mode]["RegArt"][k] += int(
                    article_rank is not None and article_rank <= k
                )
                counts[mode]["RegExact"][k] += int(
                    exact_rank is not None and exact_rank <= k
                )
            if mode == "hybrid":
                for hit in hits:
                    label = "known-relevant" if _is_relevant(hit, gold_unit) else "known-irrelevant"
                    distributions[label].append(float(hit["score"]))
    denominator = len(rows)
    # Keep metric names explicit in the returned structure for unambiguous tables.
    metrics = {
        mode: {
            metric: {k: counts[mode][metric][k] / denominator for k in KS}
            for metric in ("RegArt", "RegExact")
        }
        for mode in ("dense", "hybrid")
    }
    return metrics, distributions


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _print_baseline() -> None:
    print("DENSE BASELINE REFERENCE — full handwritten regulation population")
    print("n  | k  | RegArt | RegExact")
    print("---+----+--------+---------")
    for k in KS:
        reg_art, reg_exact = BASELINE[k]
        print(f"20 | @{k:<2d} | {reg_art:.3f}  | {reg_exact:.3f}")


def _print_coverage(rows: list[dict[str, Any]]) -> None:
    print("\nCOVERAGE MISSES — reported separately, excluded from fusion comparison")
    print("n=2 (the full baseline containing these rows is n=20)")
    for row in rows:
        print(f"{row['gold_id']} | {row['question']}")


def _print_comparison(label: str, metrics: dict[str, Any]) -> None:
    print(f"\n{label} DENSE VS HYBRID — n=18")
    print("n  | mode   | k   | RegArt | RegExact")
    print("---+--------+-----+--------+---------")
    for mode in ("dense", "hybrid"):
        for k in KS:
            print(
                f"18 | {mode:<6s} | @{k:<2d} | "
                f"{metrics[mode]['RegArt'][k]:.3f}  | "
                f"{metrics[mode]['RegExact'][k]:.3f}"
            )


def _print_distribution(label: str, distributions: dict[str, list[float]]) -> None:
    print(f"\n{label} HYBRID RRF SCORE DISTRIBUTION — n=18 queries")
    print("class            | count | min      | p25      | p50      | p75      | max")
    print("-----------------+-------+----------+----------+----------+----------+---------")
    for name in ("known-relevant", "known-irrelevant"):
        values = distributions[name]
        percentiles = [_percentile(values, p) for p in (0.0, 0.25, 0.5, 0.75, 1.0)]
        print(
            f"{name:<16s} | {len(values):>5d} | "
            + " | ".join(f"{value:.6f}" for value in percentiles)
        )


def main() -> None:
    rows = _load_rows()
    if len(rows) != 20:
        raise RuntimeError(f"expected 20 reg rows, found {len(rows)}")
    coverage_rows = [row for row in rows if row["gold_id"] in COVERAGE_MISS_IDS]
    compare_rows = [row for row in rows if row["gold_id"] not in COVERAGE_MISS_IDS]
    if len(coverage_rows) != 2 or len(compare_rows) != 18:
        raise RuntimeError("coverage exclusion did not produce n=2 and n_compare=18")

    clean_questions = [row["question"] for row in compare_rows]
    corrupted = [
        corrupt_query(row["question"], index)
        for index, row in enumerate(compare_rows)
    ]
    corrupted_questions = [question for question, _ in corrupted]
    kinds: dict[str, int] = {}
    for _, kind in corrupted:
        kinds[kind] = kinds.get(kind, 0) + 1

    all_questions = clean_questions + corrupted_questions
    embeddings = model().encode(all_questions, normalize_embeddings=True)
    clean_embeddings = list(embeddings[:len(compare_rows)])
    corrupted_embeddings = list(embeddings[len(compare_rows):])
    chunk_meta = _load_chunk_meta()

    clean_metrics, clean_distributions = _measure(
        compare_rows, clean_questions, clean_embeddings, chunk_meta
    )
    corrupted_metrics, corrupted_distributions = _measure(
        compare_rows, corrupted_questions, corrupted_embeddings, chunk_meta
    )

    _print_baseline()
    _print_coverage(coverage_rows)
    _print_comparison("CLEAN", clean_metrics)
    print("\nCORRUPTION METHOD — generic drop/duplicate/substitute; one token per query")
    print("selection counts: " + ", ".join(
        f"{name}={count}" for name, count in sorted(kinds.items())
    ))
    _print_comparison("CORRUPTED", corrupted_metrics)
    _print_distribution("CLEAN", clean_distributions)
    _print_distribution("CORRUPTED", corrupted_distributions)
    print("\nWARNING: RRF scores are rank-fusion values, not cosine similarities.")
    print("The MIN_RELEVANCE_SCORE=0.50 trust gate is UNCALIBRATED for hybrid mode.")
    print("Hybrid must not be enabled in production until a human calibrates its gate.")
    shutdown()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Evaluate caller-speech handoff coverage against an independent phrase bank."""
from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

import callcenter
from callcenter import _HANDOFF_EMBEDDINGS, decide

PHRASES = Path("handoff_phrases.jsonl")
SWEEP_THRESHOLDS = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.81, 0.82, 0.83, 0.84, 0.85)  # Cutoffs compared against the fixed phrase bank.
PROTECTED_INTENTS = ("lost_card", "stolen_card", "fraud_unauthorized", "block_freeze", "secret_credential")


def _outcome(decision) -> str:
    """Return a decision's externally visible route."""
    return decision.outcome.value if decision.outcome else "model"


def _prediction(decision, threshold: float) -> bool:
    """Apply a candidate cutoff while retaining an unconditional fast-path handoff."""
    return decision.handoff and decision.handoff_score is None or (decision.handoff_score or -1.0) >= threshold


def _report(rows: list[tuple[dict, object]], threshold: float) -> None:
    """Print per-intent coverage and every phrase that misses at the selected cutoff."""
    groups: dict[str, list[tuple[dict, object]]] = defaultdict(list)
    for row, decision in rows:
        groups[row["intent"]].append((row, decision))

    print("Intent                 caught  missed  total  recall")
    print("------------------------------------------------------")
    missed: dict[str, list[str]] = defaultdict(list)
    for intent in PROTECTED_INTENTS:
        items = groups[intent]
        caught = sum(_prediction(decision, threshold) for _, decision in items)
        missed[intent] = [row["text"] for row, decision in items if not _prediction(decision, threshold)]
        print(f"{intent:<22s} {caught:>3d}  {len(items)-caught:>6d}  {len(items):>5d}  {caught/len(items):.1%}")

    negatives = groups["negative"]
    false_positives = [row["text"] for row, decision in negatives if _prediction(decision, threshold)]
    print(f"negatives              {len(negatives)-len(false_positives):>3d}  {len(false_positives):>6d}  {len(negatives):>5d}  FP {len(false_positives)/len(negatives):.1%}")
    print("\nMissed phrases:")
    for intent in PROTECTED_INTENTS:
        print(f"{intent} ({len(missed[intent])}):")
        for text in missed[intent]:
            print(f"  - {text}")
    if false_positives:
        print("negative false positives:")
        for text in false_positives:
            print(f"  - {text}")


def _sweep(rows: list[tuple[dict, object]]) -> None:
    """Print recall and negative false-positive rate for every candidate cutoff."""
    positives = [(row, decision) for row, decision in rows if row["intent"] != "negative"]
    negatives = [(row, decision) for row, decision in rows if row["intent"] == "negative"]
    print("\nThreshold sweep (overall recall / negative FP):")
    for threshold in SWEEP_THRESHOLDS:
        recall = sum(_prediction(decision, threshold) for _, decision in positives) / len(positives)
        fp = sum(_prediction(decision, threshold) for _, decision in negatives) / len(negatives)
        print(f"  {threshold:.2f}: {recall:.1%} / {fp:.1%}")


def _cosine_latency(rows: list[tuple[dict, object]]) -> None:
    """Measure only cached-matrix cosine matching, excluding embedding generation."""
    vectors = [decision.query_embedding for _, decision in rows if decision.query_embedding is not None][:100]
    samples = []
    for vector in vectors:
        started = time.perf_counter()
        _HANDOFF_EMBEDDINGS @ vector
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    p50 = statistics.median(ordered)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    print(f"\nCosine-only latency over {len(samples)} turns: p50={p50:.4f} ms, p95={p95:.4f} ms")


def main() -> None:
    """Run every independent phrase through the production decision function once."""
    phrase_rows = [json.loads(line) for line in PHRASES.open(encoding="utf-8")]
    phrase_vectors = callcenter.model().encode(  # Evaluation-only batching keeps the full decide() pass practical.
        [row["text"] for row in phrase_rows], normalize_embeddings=True, batch_size=64,
        show_progress_bar=False,
    )
    embedding_cache = {row["text"]: vector for row, vector in zip(phrase_rows, phrase_vectors)}  # Per-phrase vectors for this run.
    original_embedding = callcenter._handoff_embedding  # Production encoder restored immediately after the evaluation pass.
    callcenter._handoff_embedding = lambda text: (embedding_cache[text], float((_HANDOFF_EMBEDDINGS @ embedding_cache[text]).max()))
    try:
        rows = [(row, decide(row["text"], "", [])) for row in phrase_rows]
    finally:
        callcenter._handoff_embedding = original_embedding
    _report(rows, 0.82)
    _sweep(rows)
    _cosine_latency(rows)


if __name__ == "__main__":
    main()

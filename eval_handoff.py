#!/usr/bin/env python3
"""Leakage-free grouped evaluation for handoff-intent routing."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

import callcenter
from callcenter import (_HANDOFF_THRESHOLD, _PROBE_LABELS, _PROBE_VECTORS,
                        _SECRET_FAST_RE, decide)
from retrieve import model

PHRASES = Path("handoff_phrases.jsonl")  # Fixed blind phrase bank.
STRATIFIED_SPLIT = Path("handoff_split.json")  # Previous row-stratified split.
GROUPED_SPLIT = Path("handoff_split_grouped.json")  # Leakage-free family split.
ARTIFACT = Path("handoff_probe.json")  # Exported grouped winner data.
INTENTS = ("lost_card", "stolen_card", "fraud_unauthorized", "block_freeze", "secret_credential")  # Report order.


def _group_key(text: str) -> str:
    """Normalize case, diacritics, punctuation, and whitespace into a family key."""
    lowered = unicodedata.normalize("NFD", text.lower())
    undiacritized = "".join(char for char in lowered if not unicodedata.combining(char))
    unpunctuated = re.sub(r"[^\w\s]", " ", undiacritized, flags=re.UNICODE)
    return " ".join(unpunctuated.split())


def _load() -> tuple[list[dict], dict, dict]:
    """Load the bank and reject either stale split."""
    raw = PHRASES.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    rows = [json.loads(line) for line in raw.decode().splitlines()]
    old_split = json.loads(STRATIFIED_SPLIT.read_text())
    grouped_split = json.loads(GROUPED_SPLIT.read_text())
    if old_split["source_sha256"] != source_hash or grouped_split["source_sha256"] != source_hash:
        raise RuntimeError("A handoff split does not match the phrase bank")
    return rows, old_split, grouped_split


def _fast(texts: list[str]) -> np.ndarray:
    """Return credential regex decisions that bypass embedding classification."""
    return np.asarray([bool(_SECRET_FAST_RE.search(text)) for text in texts])


def _predict(scores: np.ndarray, threshold: float, indices: np.ndarray,
             fast: np.ndarray) -> np.ndarray:
    """Apply one method's threshold plus the unchanged regex fast path."""
    return fast[indices] | (scores[indices] >= threshold)


def _metrics(scores: np.ndarray, threshold: float, indices: np.ndarray,
             labels: np.ndarray, intents: np.ndarray, fast: np.ndarray) -> dict:
    """Calculate per-intent recall and negative false-positive rate."""
    predictions = _predict(scores, threshold, indices, fast)
    truth = labels[indices]
    per_intent = {}
    for intent in INTENTS:
        mask = intents[indices] == intent
        caught = int(predictions[mask].sum())
        per_intent[intent] = (caught, int(mask.sum()), caught / int(mask.sum()))
    return {
        "per_intent": per_intent,
        "overall": float(predictions[truth].mean()),
        "fp": float(predictions[~truth].mean()),
        "fp_count": int(predictions[~truth].sum()),
        "negative_count": int((~truth).sum()),
        "pred": predictions,
    }


def _operating_point(scores: np.ndarray, indices: np.ndarray, labels: np.ndarray,
                     intents: np.ndarray, fast: np.ndarray) -> tuple[float, dict]:
    """Maximize train recall subject to FP<=2%, breaking ties by weakest intent."""
    best = None
    for threshold in np.r_[np.inf, np.unique(scores[indices])]:
        result = _metrics(scores, float(threshold), indices, labels, intents, fast)
        if result["fp"] <= .02 + 1e-12:
            minimum_intent = min(value[2] for value in result["per_intent"].values())
            key = (result["overall"], minimum_intent, -float(threshold))
            if best is None or key > best[0]:
                best = (key, float(threshold), result)
    if best is None:
        raise RuntimeError("No FP<=2% train operating point")
    return best[1], best[2]


def _kcenter_anchors(vectors: np.ndarray, train: np.ndarray,
                     intents: np.ndarray) -> np.ndarray:
    """Choose eight diverse train-only positive anchors per intent."""
    chosen = []
    for intent in INTENTS:
        candidates = np.asarray([row_id for row_id in train if intents[row_id] == intent])
        centroid = vectors[candidates].mean(axis=0)
        centroid /= np.linalg.norm(centroid)
        selected = [int(candidates[np.argmax(vectors[candidates] @ centroid)])]
        while len(selected) < 8:
            coverage = (vectors[candidates] @ vectors[selected].T).max(axis=1)
            for row_id in selected:
                coverage[candidates == row_id] = np.inf
            selected.append(int(candidates[np.argmin(coverage)]))
        chosen.extend(selected)
    return np.asarray(chosen)


def _method_b_scores(vectors: np.ndarray, labels: np.ndarray, train: np.ndarray,
                     query: np.ndarray, k: int, leave_self_out: bool) -> np.ndarray:
    """Return k-NN positive-vs-negative margins with majority-vote gating."""
    scores = np.empty(len(query), dtype=np.float32)
    for position, row_id in enumerate(query):
        similarities = vectors[row_id] @ vectors[train].T
        if leave_self_out:
            own = np.flatnonzero(train == row_id)
            if len(own):
                similarities[own[0]] = -np.inf
        order = np.argsort(similarities)[::-1]
        positive_vote = labels[train[order[:k]]].sum() > k // 2
        positive_neighbours = np.sort(similarities[labels[train]])[-k:]
        negative_neighbours = np.sort(similarities[~labels[train]])[-k:]
        scores[position] = (
            float(positive_neighbours.mean() - negative_neighbours.mean())
            if positive_vote else -np.inf
        )
    return scores


def _train_methods(vectors: np.ndarray, labels: np.ndarray, intents: np.ndarray,
                   fast: np.ndarray, split: dict) -> dict:
    """Tune A/B/C using one split's train rows, then score its untouched test rows."""
    train = np.asarray(split["train_indices"])
    test = np.asarray(split["test_indices"])

    anchors = _kcenter_anchors(vectors, train, intents)
    scores_a = (vectors @ vectors[anchors].T).max(axis=1)
    threshold_a, train_a = _operating_point(scores_a, train, labels, intents, fast)
    test_a = _metrics(scores_a, threshold_a, test, labels, intents, fast)

    candidates_b = []
    for k in (1, 3):
        scores = np.full(len(labels), -np.inf, dtype=np.float32)
        scores[train] = _method_b_scores(vectors, labels, train, train, k, True)
        threshold, train_result = _operating_point(scores, train, labels, intents, fast)
        weakest = min(value[2] for value in train_result["per_intent"].values())
        candidates_b.append(((train_result["overall"], weakest), k, threshold, scores, train_result))
    _, k_b, threshold_b, scores_b, train_b = max(candidates_b, key=lambda item: item[0])
    scores_b[test] = _method_b_scores(vectors, labels, train, test, k_b, False)
    test_b = _metrics(scores_b, threshold_b, test, labels, intents, fast)

    candidates_c = []
    for class_weight in (None, "balanced"):
        for regularization in (.01, .1, 1.0, 10.0, 100.0):
            classifier = LogisticRegression(
                C=regularization, class_weight=class_weight, max_iter=5000,
                solver="liblinear", random_state=split["seed"],
            ).fit(vectors[train], labels[train])
            scores = classifier.decision_function(vectors)
            threshold, train_result = _operating_point(scores, train, labels, intents, fast)
            weakest = min(value[2] for value in train_result["per_intent"].values())
            key = (train_result["overall"], weakest, -float(np.linalg.norm(classifier.coef_[0])))
            candidates_c.append((key, classifier, threshold, scores, train_result))
    _, classifier_c, threshold_c, scores_c, train_c = max(candidates_c, key=lambda item: item[0])
    test_c = _metrics(scores_c, threshold_c, test, labels, intents, fast)

    return {
        "train": train, "test": test,
        "A": {"threshold": threshold_a, "train": train_a, "test": test_a, "scores": scores_a,
              "setting": f"threshold={threshold_a:.6f}"},
        "B": {"threshold": threshold_b, "train": train_b, "test": test_b, "scores": scores_b,
              "k": k_b, "setting": f"k={k_b} margin={threshold_b:.6f}"},
        "C": {"threshold": threshold_c, "train": train_c, "test": test_c, "scores": scores_c,
              "classifier": classifier_c,
              "setting": f"threshold={threshold_c:.6f} C={classifier_c.C} class_weight={classifier_c.class_weight}"},
    }


def _print_families(rows: list[dict], old_split: dict, grouped_split: dict) -> None:
    """Report family sizes, old leakage, and grouped-split counts."""
    families = defaultdict(list)
    for row_id, row in enumerate(rows):
        families[(row["intent"], _group_key(row["text"]))].append(row_id)
    size_distribution = Counter(len(members) for members in families.values())
    per_intent = Counter(intent for intent, _ in families)
    old_train = set(old_split["train_indices"])
    old_test = set(old_split["test_indices"])
    leaked = [members for members in families.values()
              if old_train.intersection(members) and old_test.intersection(members)]
    leaked_test_rows = sum(len(old_test.intersection(members)) for members in leaked)
    grouped_train = set(grouped_split["train_indices"])
    grouped_test = set(grouped_split["test_indices"])
    grouped_leaks = sum(bool(grouped_train.intersection(members) and grouped_test.intersection(members))
                        for members in families.values())

    print("STEP 1/2 — normalized families and old-split leakage")
    print(f"  families={len(families)} size_distribution={dict(sorted(size_distribution.items()))}")
    print("  families_per_intent=" + ", ".join(f"{intent}:{per_intent[intent]}"
          for intent in (*INTENTS, "negative")))
    print(f"  old_split_leaked_families={len(leaked)}/{len(families)}")
    print(f"  old_test_rows_in_leaked_families={leaked_test_rows}/{len(old_test)} "
          f"({leaked_test_rows / len(old_test):.1%})")
    print(f"  grouped_split: train={len(grouped_train)} test={len(grouped_test)} "
          f"spanning_families={grouped_leaks}")
    for intent, counts in grouped_split["strata"].items():
        print(f"    {intent:<22s} train={counts['train']:>2} test={counts['test']:>2} "
              f"families={counts['families']:>2}")


def _print_near_duplicates(rows: list[dict], vectors: np.ndarray, labels: np.ndarray,
                           train: np.ndarray, test: np.ndarray) -> None:
    """Report grouped-test similarity to the nearest same-class grouped-train row."""
    similarities = vectors[test] @ vectors[train].T
    same_class = labels[test, None] == labels[train][None, :]
    similarities[~same_class] = -np.inf
    nearest = similarities.max(axis=1)
    neighbours = similarities.argmax(axis=1)
    points = [nearest.min(), *np.quantile(nearest, [.1, .25, .5]), nearest.mean(),
              *np.quantile(nearest, [.75, .9, .95]), nearest.max()]
    print("\nSTEP 4 — nearest same-class TRAIN cosine for each grouped TEST row")
    print("  min     p10     p25     median  mean    p75     p90     p95     max")
    print("  " + "  ".join(f"{point:.3f}" for point in points))
    high = np.flatnonzero(nearest > .95)
    print(f"  rows_above_0.95={len(high)}/{len(test)}")
    print("  cosine  test intent/text | nearest train intent/text")
    for position in high:
        test_id = int(test[position])
        train_id = int(train[neighbours[position]])
        print(f"  {nearest[position]:.3f}  {rows[test_id]['intent']} — {rows[test_id]['text']} | "
              f"{rows[train_id]['intent']} — {rows[train_id]['text']}")


def _print_methods(results: dict) -> None:
    """Print grouped train and grouped held-out metrics for A/B/C."""
    print("\nSTEP 5 — GROUPED method comparison (all settings tuned on grouped TRAIN only)")
    print("method/split  lost       stolen     fraud      block      secret     overall   negative FP  setting")
    for method in ("A", "B", "C"):
        for split_name in ("train", "test"):
            result = results[method][split_name]
            parts = []
            for intent in INTENTS:
                caught, total, _ = result["per_intent"][intent]
                parts.append(f"{caught:>2}/{total:<2}")
            setting = results[method]["setting"] if split_name == "train" else ""
            print(f"{method}/{split_name:<5s}    " + "  ".join(f"{part:<9s}" for part in parts)
                  + f"  {result['overall']:>6.1%}    {result['fp_count']}/{result['negative_count']} "
                  f"({result['fp']:.1%})  {setting}")


def _print_direct_comparison(old: dict, grouped: dict) -> None:
    """Compare Method B under the row-stratified and family-grouped splits."""
    print("\nSTEP 6 — Method B stratified vs grouped TEST")
    print("intent                 stratified  grouped   delta")
    for intent in INTENTS:
        old_recall = old["B"]["test"]["per_intent"][intent][2]
        grouped_recall = grouped["B"]["test"]["per_intent"][intent][2]
        print(f"{intent:<22s} {old_recall:>9.1%}  {grouped_recall:>7.1%}  "
              f"{(grouped_recall - old_recall) * 100:+5.1f} pp")
    old_overall = old["B"]["test"]["overall"]
    grouped_overall = grouped["B"]["test"]["overall"]
    print(f"{'overall':<22s} {old_overall:>9.1%}  {grouped_overall:>7.1%}  "
          f"{(grouped_overall - old_overall) * 100:+5.1f} pp")
    print(f"{'negative FP':<22s} {old['B']['test']['fp']:>9.1%}  {grouped['B']['test']['fp']:>7.1%}  "
          f"{(grouped['B']['test']['fp'] - old['B']['test']['fp']) * 100:+5.1f} pp")


def main() -> None:
    """Run grouped leakage analysis, retraining, comparison, and production verification."""
    rows, old_split, grouped_split = _load()
    texts = [row["text"] for row in rows]
    intents = np.asarray([row["intent"] for row in rows])
    labels = intents != "negative"
    fast = _fast(texts)
    vectors = np.asarray(model().encode(  # Each phrase is encoded once and shared by all methods.
        texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False,
    ), dtype=np.float32)

    _print_families(rows, old_split, grouped_split)
    grouped_train = np.asarray(grouped_split["train_indices"])
    grouped_test = np.asarray(grouped_split["test_indices"])
    _print_near_duplicates(rows, vectors, labels, grouped_train, grouped_test)

    old_results = _train_methods(vectors, labels, intents, fast, old_split)
    grouped_results = _train_methods(vectors, labels, intents, fast, grouped_split)
    _print_methods(grouped_results)
    _print_direct_comparison(old_results, grouped_results)

    eligible = [method for method in ("A", "B", "C")
                if grouped_results[method]["test"]["fp"] <= .02]
    winner = max(eligible, key=lambda method: grouped_results[method]["test"]["overall"])
    print(f"\nWinner under grouped TEST FP<=2%: Method {winner}")
    if winner != "B":
        raise RuntimeError("Exported grouped artifact expects Method B")

    artifact = json.loads(ARTIFACT.read_text())
    if artifact["method"] != "knn_class_margin" or artifact["k"] != grouped_results["B"]["k"]:
        raise RuntimeError("handoff_probe.json has the wrong method")
    if abs(artifact["margin"] - grouped_results["B"]["threshold"]) > 1e-12:
        raise RuntimeError("handoff_probe.json has a stale margin")
    if not np.allclose(_PROBE_VECTORS, vectors[grouped_train], atol=1e-7, rtol=0):
        raise RuntimeError("Exported neighbour vectors are stale")
    if not np.array_equal(_PROBE_LABELS, labels[grouped_train]):
        raise RuntimeError("Exported neighbour labels are stale")
    if _HANDOFF_THRESHOLD != grouped_results["B"]["threshold"]:
        raise RuntimeError("callcenter margin differs from evaluation")

    embedding_cache = {text: vector for text, vector in zip(texts, vectors)}
    original_encoder = callcenter._encode_question
    callcenter._encode_question = lambda text: embedding_cache[text]
    try:
        production = np.asarray([decide(texts[row_id], "", []).handoff for row_id in grouped_test])
    finally:
        callcenter._encode_question = original_encoder
    expected = grouped_results["B"]["test"]["pred"]
    if not np.array_equal(production, expected):
        raise RuntimeError("Production decide() disagrees with grouped winner")
    print(f"Production verification: decide() matches Method B on {len(grouped_test)}/{len(grouped_test)} grouped test rows.")

    print("\nGrouped winner residual errors:")
    for row_id, predicted in zip(grouped_test, expected):
        if labels[row_id] and not predicted:
            print(f"  FN {rows[row_id]['intent']} — {rows[row_id]['text']} — nearest-neighbour class margin below threshold")
        elif not labels[row_id] and predicted:
            print(f"  FP negative — {rows[row_id]['text']}")
    if grouped_results["B"]["test"]["fp_count"] == 0:
        print("  FP none")


if __name__ == "__main__":
    main()

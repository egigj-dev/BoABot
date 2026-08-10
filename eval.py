#!/usr/bin/env python3
"""eval.py — score retrieval eval sets and print a comparison table.

Variables introduced:
  ALL_SETS    : default file list when no args given
  _load_set() : load a jsonl eval set
  _score()    : compute recall and latency for one set
  _fmt()      : render the comparison table
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from retrieve import retrieve
from trust import trusted_hits

DSN = "postgresql://boa:boa@127.0.0.1:5433/boa"  # only used for doc-lookup
ALL_SETS = [
    ("eval_retrieval.jsonl", "old (buggy)"),
    ("eval_generated.jsonl", "generated"),
    ("eval_handwritten.jsonl", "handwritten"),
]
FAQ_SET = ("eval_faq.jsonl", "FAQ")
KS = (1, 3, 5, 10)

# Load rate chunks for bank-name verification
_RATES: dict[str, str] = {}
_rates_path = Path("rate_tables.jsonl")
if _rates_path.exists():
    for i, line in enumerate(open(_rates_path, encoding="utf-8")):
        r = json.loads(line)
        _RATES[f"rate_{i:04d}"] = r["text"]


def _bank_names(text: str) -> list[str]:
    """Return bank names from a rate chunk's data lines."""
    skip = {"biznes i vogel", "kredi per shtepi/prona"}
    names = []
    for line in text.split("\n"):
        if ":" not in line:
            continue
        if line.startswith("Normat") or line.startswith("Rregullore"):
            continue
        candidate = line.split(":")[0].strip()
        if candidate.casefold() in skip:
            continue
        names.append(candidate)
    return names


# All known rate labels make an empty gold row fail when a question names any bank.
_RATE_BANK_NAMES = frozenset(
    bank.casefold() for text in _RATES.values() for bank in _bank_names(text)
)
# Chunk metadata resolves each gold ID to its answerable document-and-article unit.
_CHUNK_META: dict[str, tuple[str, str]] = {}
_chunks_path = Path("chunks.jsonl")
if _chunks_path.exists():
    for line in open(_chunks_path, encoding="utf-8"):
        chunk = json.loads(line)
        _CHUNK_META[chunk["id"]] = (chunk["doc"], str(chunk.get("article", "")))


def _load_set(path: str) -> list[dict]:
    if not Path(path).exists():
        print(f"  [{path} not found — skipping]", file=sys.stderr)
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def _prefix(gid: str) -> str:
    return gid.split("_")[0]


def _score(entries: list[dict]) -> dict:
    """Return a dict of metrics for an eval set."""
    n = len(entries)
    if n == 0:
        return {}

    hit_at = {k: 0 for k in KS}  # Exact-ID recall remains a secondary diagnostic.
    reg_article_hit_at = {k: 0 for k in KS}  # Primary regulation recall at the answerable article unit.
    reg_doc_hit_at = {k: 0 for k in KS}  # Same-document regulation recall is secondary.
    reg_exact_hit_at = {k: 0 for k in KS}  # Exact chunk-ID regulation recall is secondary.
    reg_total = 0  # Number of regulation questions used as the denominator for regulation metrics.
    miss_list: list[tuple[str, str, list[str]]] = []  # (gold_id, question, top_ids)
    lats: list[float] = []

    rate_no_bank = 0
    hit_by_prefix: dict[str, int] = {}
    total_by_prefix: dict[str, int] = {}
    trap_total = 0
    trap_refusals: list[tuple[str, str]] = []  # (question, gate reason) for refused traps.
    trap_reasons: Counter[str] = Counter()  # Refusal counts grouped by trusted_hits reason.
    trap_hit_at = {k: 0 for k in KS}  # Secondary retrieval recall for trap questions.

    for e in entries:
        gid = e["gold_id"]
        question = e["question"]
        is_trap = e.get("trap", False)
        pref = _prefix(gid)

        if is_trap:
            trap_total += 1
        total_by_prefix[pref] = total_by_prefix.get(pref, 0) + 1

        t0 = time.time()
        hits = retrieve(question, k=max(KS))
        lats.append(time.time() - t0)

        top_ids = [h["id"] for h in hits]
        rank = top_ids.index(gid) + 1 if gid in top_ids else None
        gold_doc, gold_article = _CHUNK_META.get(gid, ("", ""))
        article_rank = next((i + 1 for i, hit in enumerate(hits)
                             if hit.get("doc") == gold_doc and str(hit.get("article", "")) == gold_article), None)
        doc_rank = next((i + 1 for i, hit in enumerate(hits)
                         if hit.get("doc") == gold_doc), None)

        if pref == "reg":
            reg_total += 1
        for k in KS:
            if rank and rank <= k:
                hit_at[k] += 1
                if is_trap:
                    trap_hit_at[k] += 1
            if pref == "reg":
                if article_rank and article_rank <= k:
                    reg_article_hit_at[k] += 1
                if doc_rank and doc_rank <= k:
                    reg_doc_hit_at[k] += 1
                if rank and rank <= k:
                    reg_exact_hit_at[k] += 1

        if is_trap:
            # The gate sees only the production top-five results; top-ten stays for secondary recall.
            gate = trusted_hits(question, hits[:5])
            if not gate.allowed:
                reason = gate.reason or "unknown"
                trap_refusals.append((question, reason))
                trap_reasons[reason] += 1

        if rank:
            hit_by_prefix[pref] = hit_by_prefix.get(pref, 0) + 1
        else:
            miss_list.append((gid, question, top_ids[:3]))

        # Rate bank-name check
        if pref == "rate" and gid in _RATES:
            banks = _bank_names(_RATES[gid])
            q_lower = question.casefold()
            names_bank = any(bank in q_lower for bank in _RATE_BANK_NAMES)
            has_gold_bank = any(bank.casefold() in q_lower for bank in banks)
            if names_bank and (not banks or not has_gold_bank):
                rate_no_bank += 1

    latency_median = statistics.median(lats) * 1000 if lats else 0
    latency_p95 = sorted(lats)[int(0.95 * n)] * 1000 if lats and n > 0 else 0

    return {
        "n": n,
        "hit_at": {k: (hit_at[k], f"{hit_at[k]/n:.3f}") for k in KS},
        "reg_article_hit_at": {k: (reg_article_hit_at[k], f"{reg_article_hit_at[k]/reg_total:.3f}" if reg_total else "-") for k in KS},
        "reg_doc_hit_at": {k: (reg_doc_hit_at[k], f"{reg_doc_hit_at[k]/reg_total:.3f}" if reg_total else "-") for k in KS},
        "reg_exact_hit_at": {k: (reg_exact_hit_at[k], f"{reg_exact_hit_at[k]/reg_total:.3f}" if reg_total else "-") for k in KS},
        "misses": miss_list,
        "miss_count": len(miss_list),
        "latency_median_ms": round(latency_median),
        "latency_p95_ms": round(latency_p95),
        "rate_no_bank": rate_no_bank,
        "hit_by_prefix": hit_by_prefix,
        "total_by_prefix": total_by_prefix,
        "trap_total": trap_total,
        "trap_refusals": trap_refusals,
        "trap_reasons": trap_reasons,
        "trap_hit_at": trap_hit_at,
    }


def _score_faq(entries: list[dict]) -> dict:
    """Score FAQ source-URL recall; this set has no chunk gold IDs."""
    hit_at = {k: 0 for k in (1, 5, 10)}
    for entry in entries:
        hits = retrieve(entry["question"], k=10)
        gold_url = entry["url"].rstrip("/")
        rank = next((index for index, hit in enumerate(hits, 1)
                     if str(hit.get("url") or "").rstrip("/") == gold_url), None)
        for k in hit_at:
            if rank and rank <= k:
                hit_at[k] += 1
    return {"n": len(entries), "hit_at": hit_at}


def _fmt_faq(path: str, scores: dict) -> None:
    print("\n--- FAQ recall (exact source URL) ---")
    if not scores:
        print("No FAQ data to report.")
        return
    n = scores["n"]
    values = "  ".join(
        f"R@{k}={scores['hit_at'][k]}/{n} ({scores['hit_at'][k] / n:.3f})"
        for k in (1, 5, 10)
    )
    print(f"  {path}: {values}")


def _fmt(scores: list[tuple[str, str, dict]]) -> None:
    """Print a comparison table."""
    if not scores:
        print("No data to report.")
        return

    header = f"{'Set':<22s}"
    for k in KS:
        header += f"  RegArt@{k:<4d}"
    for k in KS:
        header += f"  RegID@{k:<5d}"
    for k in KS:
        header += f"  RegDoc@{k:<4d}"
    header += (f"  {'p50 ms':>7s}  {'p95 ms':>7s}  {'Miss':>5s}  {'R?Bk':>5s}"
               f"  {'Trap refusals':>13s}  {'Trap recall (secondary)':>24s}")
    sep = "-" * len(header)

    print(header)
    print(sep)

    for label, path, s in scores:
        article_rh = "  ".join(s["reg_article_hit_at"][k][1] for k in KS)
        exact_rh = "  ".join(s["reg_exact_hit_at"][k][1] for k in KS)
        doc_rh = "  ".join(s["reg_doc_hit_at"][k][1] for k in KS)
        n = s["n"]
        # Per-prefix recall
        pref_info = ""
        for pref in ("reg", "rate"):
            h = s["hit_by_prefix"].get(pref, 0)
            t = s["total_by_prefix"].get(pref, 0)
            r = f"{h/t:.3f}" if t else "-"
            pref_info += f"  {pref}={r}"

        trap_refusal_str = f"{len(s['trap_refusals'])}/{s['trap_total']}" if s["trap_total"] else "-"
        trap_recall_str = (
            "/".join(f"R@{k}={s['trap_hit_at'][k]}/{s['trap_total']}" for k in KS)
            if s["trap_total"] else "-"
        )

        print(
            f"{label:<22s}  {article_rh}  {exact_rh}  {doc_rh}  "
            f"{s['latency_median_ms']:>7d}  {s['latency_p95_ms']:>7d}  "
            f"{s['miss_count']:>5d}  {s['rate_no_bank']:>5d}  "
            f"{trap_refusal_str:>13s}  {trap_recall_str:>24s}"
        )

    # Print misses for each set
    print()
    for label, path, s in scores:
        if s["misses"]:
            print(f"\n  {label} — misses ({s['miss_count']}):")
            for gid, question, top3 in s["misses"][:6]:
                print(f"    {gid:14s} {question[:65]}")
                print(f"    {'':14s} top: {top3}")
            if s["miss_count"] > 6:
                print(f"    ... ({s['miss_count'] - 6} more)")

    print("\n--- Trap gate results (headline: refusals; target 0) ---")
    for label, path, s in scores:
        if not s["trap_total"]:
            continue
        refusals = s["trap_refusals"]
        print(f"  {label}: refusal rate {len(refusals)}/{s['trap_total']}")
        if refusals:
            print("    reason breakdown: " + ", ".join(
                f"{reason}={count}" for reason, count in sorted(s["trap_reasons"].items())
            ))
            for question, reason in refusals:
                print(f"    refused ({reason}): {question}")


def main() -> None:
    paths = sys.argv[1:] if len(sys.argv) > 1 else [p for p, _ in ALL_SETS]
    # Map labels
    label_map = {p: lbl for p, lbl in ALL_SETS}

    scores: list[tuple[str, str, dict]] = []
    for path in paths:
        label = label_map.get(path, path)
        entries = _load_set(path)
        if not entries:
            print(f"Skipping {path}: no entries loaded")
            continue
        s = _score(entries)
        scores.append((label, path, s))

    print("--- Retrieval recall ---")
    _fmt(scores)

    faq_path, _faq_label = FAQ_SET
    faq_entries = _load_set(faq_path)
    _fmt_faq(faq_path, _score_faq(faq_entries) if faq_entries else {})

    # Print recall breakdown by prefix in a compact form
    print("\n--- Recall breakdown by prefix ---")
    for label, path, s in scores:
        parts = []
        for pref in ("rate", "reg"):
            h = s["hit_by_prefix"].get(pref, 0)
            t = s["total_by_prefix"].get(pref, 0)
            for k in KS:
                pass  # prefix breakdown per-k isn't tracked; just total
            pct = f"{h}/{t}" if t else "-"
            parts.append(f"{pref}={pct}")
        print(f"  {label:<22s}  {'  '.join(parts)}")


if __name__ == "__main__":
    main()

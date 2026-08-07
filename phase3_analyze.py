#!/usr/bin/env python3
"""Recompute phase-3 summary tables from preserved raw benchmark JSON."""
from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

from bench_turn import TOKEN_RE


ROOT = Path("latency_evidence")
RUNS = {
    ("DeepSeek", "empty"): ROOT / "phase3_deepseek_empty_N100.json",
    ("DeepSeek", "history"): ROOT / "phase3_deepseek_history_N100.json",
    ("Gemini", "empty"): ROOT / "phase3_gemini_empty_N100.json",
    ("Gemini", "history"): ROOT / "phase3_gemini_history_N100.json",
}
PROVIDERS = {
    "cache on": ROOT / "phase3_provider_cache_on_N100.json",
    "cache off": ROOT / "phase3_provider_cache_off_N100.json",
}
METRICS = (
    ("first SSE", "first_event_ms"),
    ("first token", "first_token_ms"),
    ("first sentence", "first_sentence_ms"),
    ("done", "done_ms"),
)
SENTENCE_RE = re.compile(r"[.!?][\"'»”\)\]]?(?:\s|$)")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def sentence_prefix(answer: str) -> str:
    match = SENTENCE_RE.search(answer)
    return answer[:match.end()].rstrip() if match else answer


def reconciled(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))["results"]
    for row in rows:
        if row["first_sentence_ms"] is None and row["answer"]:
            row["first_sentence_ms"] = row["done_ms"]
        prefix = sentence_prefix(row["answer"])
        row["first_sentence_text"] = prefix
        row["first_sentence_tokens"] = len(TOKEN_RE.findall(prefix))
    return rows


def stats(values: list[float]) -> dict[str, float]:
    return {
        "p50": statistics.median(values),
        "p90": percentile(values, .90),
        "p95": percentile(values, .95),
        "p99": percentile(values, .99),
        "max": max(values),
    }


def main() -> None:
    analyses: dict[str, Any] = {"runs": {}, "providers": {}}
    print("FULL /turn LATENCY (ms)")
    print("| model | mode | metric | p50 | p90 | p95 | p99 | max |")
    print("|---|---|---|---:|---:|---:|---:|---:|")
    for (model, mode), path in RUNS.items():
        rows = reconciled(path)
        key = f"{model.lower()}_{mode}"
        analyses["runs"][key] = {"n": len(rows), "metrics": {}}
        for label, field in METRICS:
            summary = stats([float(row[field]) for row in rows])
            analyses["runs"][key]["metrics"][field] = summary
            print(f"| {model} | {mode} | {label} | " + " | ".join(
                f"{summary[name]:.0f}" for name in ("p50", "p90", "p95", "p99", "max")
            ) + " |")
        throughputs = [float(row["output_tokens_per_second"]) for row in rows]
        sentence_tokens = [int(row["first_sentence_tokens"]) for row in rows]
        generation = [float(row["first_sentence_ms"]) - float(row["first_token_ms"])
                      for row in rows]
        sentence_order = sorted(rows, key=lambda row: float(row["first_sentence_ms"]))
        middle_rows = (
            sentence_order[len(sentence_order) // 2 - 1:len(sentence_order) // 2 + 1]
            if len(sentence_order) % 2 == 0
            else [sentence_order[len(sentence_order) // 2]]
        )
        split = {
            "fast_lt_3000_ms": sum(float(row["first_token_ms"]) < 3000 for row in rows),
            "slow_gte_3000_ms": sum(float(row["first_token_ms"]) >= 3000 for row in rows),
        }
        generation_summary = {
            "tokens_per_second_p50": statistics.median(throughputs),
            "tokens_per_second_p10": percentile(throughputs, .10),
            "first_sentence_tokens_mean": statistics.mean(sentence_tokens),
            "ttft_p50_ms": statistics.median(float(row["first_token_ms"]) for row in rows),
            "sentence_generation_p50_ms": statistics.median(generation),
            "first_sentence_p50_ms": statistics.median(
                float(row["first_sentence_ms"]) for row in rows
            ),
            "first_sentence_p50_cohort_ttft_ms": statistics.mean(
                float(row["first_token_ms"]) for row in middle_rows
            ),
            "first_sentence_p50_cohort_generation_ms": statistics.mean(
                float(row["first_sentence_ms"]) - float(row["first_token_ms"])
                for row in middle_rows
            ),
        }
        analyses["runs"][key]["generation"] = generation_summary
        analyses["runs"][key]["split"] = split
        handoffs = [row["index"] for row in rows
                    if "agjent njerëzor" in row["answer"].casefold()]
        analyses["runs"][key]["handoff_samples"] = handoffs

    print("\nGENERATION")
    print("| model | mode | tok/s p50 | tok/s p10 | mean first-sentence tokens | "
          "TTFT p50 | sentence-generation p50 | first-sentence p50 decomposition | "
          "fast/slow (<3s) |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for key, run in analyses["runs"].items():
        model, mode = key.rsplit("_", 1)
        g = run["generation"]
        split = run["split"]
        print(f"| {model} | {mode} | {g['tokens_per_second_p50']:.1f} | "
              f"{g['tokens_per_second_p10']:.1f} | {g['first_sentence_tokens_mean']:.1f} | "
              f"{g['ttft_p50_ms']:.0f} | {g['sentence_generation_p50_ms']:.0f} | "
              f"{g['first_sentence_p50_cohort_ttft_ms']:.0f} + "
              f"{g['first_sentence_p50_cohort_generation_ms']:.0f} = "
              f"{g['first_sentence_p50_ms']:.0f} | "
              f"{split['fast_lt_3000_ms']}/{split['slow_gte_3000_ms']} |")

    print("\nPROVIDER CACHE ISOLATION (ms)")
    print("| condition | p50 | p90 | p95 | p99 | max | cache hits | fast/slow (<5s) |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for condition, path in PROVIDERS.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["results"]
        summary = stats([float(row["ttft_ms"]) for row in rows])
        hits = sum(int(row["cached_tokens"]) > 0 for row in rows)
        fast = sum(float(row["ttft_ms"]) < 5000 for row in rows)
        analyses["providers"][condition] = {
            "n": len(rows), "ttft_ms": summary, "cache_hits": hits,
            "fast_lt_5000_ms": fast, "slow_gte_5000_ms": len(rows) - fast,
        }
        print(f"| {condition} | " + " | ".join(
            f"{summary[name]:.0f}" for name in ("p50", "p90", "p95", "p99", "max")
        ) + f" | {hits}/100 | {fast}/{len(rows) - fast} |")

    on = analyses["providers"]["cache on"]["ttft_ms"]
    off = analyses["providers"]["cache off"]["ttft_ms"]
    analyses["providers"]["on_minus_off_ms"] = {
        name: on[name] - off[name] for name in ("p50", "p90", "p95", "p99", "max")
    }
    print("\nCACHE ON SAVINGS VS OFF (ms; positive means faster)")
    print("  " + "  ".join(
        f"{name} {off[name] - on[name]:.0f}" for name in ("p50", "p90", "p95", "p99", "max")
    ))

    tts_ms = 300
    analyses["tts_assumption_ms"] = tts_ms
    print("\nVOICE BUDGET (first sentence + 300 ms assumed Azure TTS first byte)")
    print("| model | mode | first-audio p50 | first-audio p95 | 1.5s p50/p95 | 2.5s p50/p95 |")
    print("|---|---|---:|---:|---|---|")
    for key, run in analyses["runs"].items():
        model, mode = key.rsplit("_", 1)
        sentence = run["metrics"]["first_sentence_ms"]
        p50, p95 = sentence["p50"] + tts_ms, sentence["p95"] + tts_ms
        verdict = lambda target: (
            f"{'PASS' if p50 <= target else 'FAIL'}/"
            f"{'PASS' if p95 <= target else 'FAIL'}"
        )
        print(f"| {model} | {mode} | {p50:.0f} | {p95:.0f} | "
              f"{verdict(1500)} | {verdict(2500)} |")

    (ROOT / "phase3_analysis.json").write_text(
        json.dumps(analyses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nanalysis JSON written to {ROOT / 'phase3_analysis.json'}")


if __name__ == "__main__":
    main()

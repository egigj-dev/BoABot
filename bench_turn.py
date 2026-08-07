#!/usr/bin/env python3
"""Reproducible /turn first-event, first-token, and completion benchmark."""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
from pathlib import Path

import requests


QUESTIONS = [
    "Sa është norma për depozita me afat 12-mujor në Banka Credins?",
    "Sa është komisioni për lëshimin e kartës së kreditit në Banka Kombëtare Tregtare?",
    "Sa është komisioni i administrimit për kredi konsumatore me hipotekë në Banka Procredit?",
    "Sa është interesi për depozitë me afat 3 muaj në Banka Tirana?",
    "Sa është komisioni për ndryshimin e kontratës së kredisë me hipotekë në Banka Credins?",
    "Kush e administron Regjistrin e Kredive?",
    "Cilat janë kërkesat për licencimin e një banke?",
    "Çfarë përmban raporti i mjaftueshmërisë së kapitalit?",
    "Kur klasifikohet një kredi si kredi me probleme?",
    "Cilat janë detyrimet e bankës për transparencën ndaj klientit?",
]

PRIMERS = [
    "Më trego për depozitat me afat në Banka Credins.",
    "Më trego për kartat e kreditit të Bankës Kombëtare Tregtare.",
    "Më trego për kreditë konsumatore me hipotekë në Banka Procredit.",
    "Më trego për depozitat në Banka Tirana.",
    "Më trego për kreditë me hipotekë në Banka Credins.",
    "Më trego për Regjistrin e Kredive.",
    "Më trego për licencimin e bankave.",
    "Më trego për mjaftueshmërinë e kapitalit.",
    "Më trego për klasifikimin e kredive.",
    "Më trego për transparencën e bankave ndaj klientëve.",
]

# A reproducible, model-neutral lexical token proxy.  The /turn SSE contract does
# not expose provider usage, so this benchmark counts words, numbers, and
# punctuation rather than pretending that one vendor tokenizer fits both models.
TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*%?|[^\W\d_]+(?:['’][^\W\d_]+)?|[^\w\s]", re.UNICODE)


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def request_turn(url: str, question: str, session_id: str | None = None) -> dict:
    started = time.perf_counter()
    marks: dict[str, float | str | None] = {
        "first_event_ms": None,
        "first_token_ms": None,
        "first_sentence_ms": None,
        "done_ms": None,
        "session_id": session_id,
    }
    answer = ""
    first_sentence_text = None
    with requests.post(
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
            elapsed_ms = (time.perf_counter() - started) * 1000
            event = json.loads(line[6:])
            if marks["first_event_ms"] is None:
                marks["first_event_ms"] = elapsed_ms
            if event.get("type") == "token" and marks["first_token_ms"] is None:
                marks["first_token_ms"] = elapsed_ms
            if event.get("type") == "token":
                answer += str(event.get("text") or "")
                if marks["first_sentence_ms"] is None and re.search(
                    r"[.!?][\"'»”\)\]]?(?:\s|$)", answer
                ):
                    marks["first_sentence_ms"] = elapsed_ms
                    first_sentence_text = answer
            if event.get("type") == "done":
                marks["done_ms"] = elapsed_ms
                marks["session_id"] = event.get("session_id") or session_id
                break
    if any(marks[key] is None for key in ("first_event_ms", "first_token_ms", "done_ms")):
        raise RuntimeError(f"Incomplete SSE stream: {marks}")
    # A completed punctuation-free answer is a complete first utterance for TTS.
    # Use stream completion as its boundary instead of dropping the observation.
    if marks["first_sentence_ms"] is None and answer:
        marks["first_sentence_ms"] = marks["done_ms"]
        first_sentence_text = answer
    sentence_match = re.search(r"[.!?][\"'»”\)\]]?(?:\s|$)", answer)
    if sentence_match:
        first_sentence_text = answer[:sentence_match.end()].rstrip()
    marks["answer"] = answer
    marks["output_tokens"] = len(TOKEN_RE.findall(answer))
    marks["first_sentence_text"] = first_sentence_text
    marks["first_sentence_tokens"] = (
        len(TOKEN_RE.findall(first_sentence_text)) if first_sentence_text else None
    )
    generation_ms = float(marks["done_ms"]) - float(marks["first_token_ms"])
    marks["output_tokens_per_second"] = (
        float(marks["output_tokens"]) / max(generation_ms / 1000, 0.001)
    )
    return marks


def histogram(values: list[float]) -> None:
    bounds = [500, 1000, 1500, 2000, 3000, 5000, 10_000, 20_000]
    counts = [0] * (len(bounds) + 1)
    for value in values:
        slot = next((i for i, bound in enumerate(bounds) if value < bound), len(bounds))
        counts[slot] += 1
    peak = max(counts) if counts else 1
    lower = 0
    for index, count in enumerate(counts):
        upper = bounds[index] if index < len(bounds) else None
        label = f"{lower:>5}-{upper:<5}" if upper is not None else f">={lower:<8}"
        bar = "#" * max(1, round(30 * count / peak)) if count else ""
        print(f"  {label} ms  {count:>4} {bar}")
        if upper is not None:
            lower = upper


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--requests", type=int, default=100)
    parser.add_argument("--history", action="store_true",
                        help="Prime a fresh session before each measured request")
    parser.add_argument("--url", default="http://127.0.0.1:8100")
    parser.add_argument("--output", type=Path,
                        help="write raw per-request measurements and answer text as JSON")
    args = parser.parse_args()
    if args.requests < 1:
        parser.error("--requests must be positive")

    results = []
    mode = "non-empty history" if args.history else "empty history"
    print(f"/turn benchmark: N={args.requests}, mode={mode}, url={args.url}")
    for index in range(args.requests):
        slot = index % len(QUESTIONS)
        session_id = None
        if args.history:
            priming = request_turn(args.url, PRIMERS[slot])
            session_id = str(priming["session_id"])
        result = request_turn(args.url, QUESTIONS[slot], session_id)
        result["question"] = QUESTIONS[slot]
        result["index"] = index + 1
        results.append(result)
        first_sentence = result["first_sentence_ms"]
        sentence_text = f"{first_sentence:7.0f} ms" if isinstance(first_sentence, float) else "    n/a   "
        print(
            f"{index + 1:2d} first_event={result['first_event_ms']:7.0f} ms  "
            f"first_token={result['first_token_ms']:7.0f} ms  "
            f"first_sentence={sentence_text}  "
            f"done={result['done_ms']:7.0f} ms  "
            f"tokens={result['output_tokens']:3d}  "
            f"tok/s={result['output_tokens_per_second']:6.1f}"
        )

    print("summary (ms)")
    for label, key in (("first SSE event", "first_event_ms"),
                       ("first token", "first_token_ms"),
                       ("first sentence", "first_sentence_ms"),
                       ("done", "done_ms")):
        values = [float(result[key]) for result in results if result[key] is not None]
        print(
            f"  {label:<16} p50 {statistics.median(values):7.0f}  "
            f"p90 {percentile(values, 0.90):7.0f}  "
            f"p95 {percentile(values, 0.95):7.0f}  "
            f"p99 {percentile(values, 0.99):7.0f}  "
            f"max {max(values):7.0f}  n {len(values)}/{len(results)}"
        )
    first_tokens = [float(result["first_token_ms"]) for result in results]
    throughputs = [float(result["output_tokens_per_second"]) for result in results]
    sentence_tokens = [float(result["first_sentence_tokens"]) for result in results
                       if result["first_sentence_tokens"] is not None]
    sentence_generation = [
        float(result["first_sentence_ms"]) - float(result["first_token_ms"])
        for result in results if result["first_sentence_ms"] is not None
    ]
    print("first-token histogram")
    histogram(first_tokens)
    print("generation (lexical-token proxy; words, numbers, and punctuation)")
    print(f"  output tokens/s  p50 {statistics.median(throughputs):.1f}  "
          f"p10 {percentile(throughputs, .10):.1f}  n {len(throughputs)}")
    if sentence_tokens:
        print(f"  first-sentence tokens mean {statistics.mean(sentence_tokens):.1f}  "
              f"n {len(sentence_tokens)}/{len(results)}")
        print(f"  first-sentence p50 decomposition  "
              f"TTFT {statistics.median(first_tokens):.0f} ms + "
              f"generation {statistics.median(sentence_generation):.0f} ms")
    if args.output:
        payload = {
            "requests": args.requests,
            "mode": mode,
            "url": args.url,
            "token_definition": "Unicode words, numbers, and punctuation",
            "results": results,
        }
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(f"raw JSON written to {args.output}")


if __name__ == "__main__":
    main()

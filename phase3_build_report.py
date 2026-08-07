#!/usr/bin/env python3
"""Build the phase-3 report and embed every required acceptance transcript."""
from __future__ import annotations

import json
from pathlib import Path


EVIDENCE = Path("latency_evidence")
REPORT = EVIDENCE / "FINAL_REPORT_PHASE3.md"
TRANSCRIPTS = [
    ("python3 bench_turn.py -n 100 (DeepSeek, empty history)",
     "phase3_deepseek_empty_N100.txt"),
    ("python3 bench_turn.py -n 100 --history (DeepSeek)",
     "phase3_deepseek_history_N100.txt"),
    ("python3 bench_provider.py -n 100 --vary --layout split --session-id ... "
     "--size-probe-requests 0 (cache on)", "phase3_provider_cache_on_N100.txt"),
    ("python3 bench_provider.py -n 100 --vary --layout combined "
     "--size-probe-requests 0 (cache off)", "phase3_provider_cache_off_N100.txt"),
    ("python3 bench_turn.py -n 100 (Gemini benchmark override, empty history)",
     "phase3_gemini_empty_N100.txt"),
    ("python3 bench_turn.py -n 100 --history (Gemini benchmark override)",
     "phase3_gemini_history_N100.txt"),
    ("python3 eval.py", "phase3_eval.txt"),
    ("python3 eval_calls.py", "phase3_eval_calls.txt"),
]


def answer(quality: dict, index: int) -> str:
    return quality["rates"][index]["answer"].replace("\n", "<br>")


def main() -> None:
    gemini = json.loads((EVIDENCE / "phase3_quality_gemini.json").read_text(encoding="utf-8"))
    deepseek = json.loads((EVIDENCE / "phase3_quality_deepseek.json").read_text(encoding="utf-8"))
    quote_rows = []
    for index in (1, 5, 18):
        question = gemini["rates"][index]["question"]
        quote_rows.append(
            f"| {question} | “{answer(gemini, index)}” | “{answer(deepseek, index)}” |"
        )

    body = f"""# BoABot Latency Phase 3 — FINAL REPORT

Completed 2026-08-07. All latency percentiles use nearest rank and N=100. Raw JSON,
full console transcripts, quality answers, and the reproducible analysis script are
committed beside this report.

## 1. Ambiguous calls and why

- `/turn` does not expose OpenRouter's native completion-token usage or generation ID.
  Output speed therefore uses one model-neutral lexical proxy: Unicode words, numbers,
  and punctuation divided by first-token-to-done wall time. This is comparable between
  the two `/turn` runs, but it is not a provider-token billing rate. OpenRouter documents
  that native counts normally arrive in the upstream usage object; production intentionally
  does not forward that object ([OpenRouter usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting)).
- A punctuation-free completed answer is one complete TTS utterance, so its `done` time is
  the first-sentence boundary. This affected one DeepSeek history answer. Its saved answer
  and timestamp were reconciled deterministically; no request was rerun or dropped.
- "Cache off" means exactly the requested structural rollback: combined system+evidence
  message and no `session_id`. DeepSeek/OpenRouter's automatic native caching cannot be
  disabled by that layout, so the off run still reported 41 incidental hits. The relevant
  comparison is shipped cache-friendly structure versus combined/no-session structure.
- The provider experiments were sequential matched runs, not a randomized crossover.
  They use the same ten prompts, k=5 context, current system prompt, model, and N=100;
  time-varying provider conditions remain a possible confound.
- "Fast/slow" is reported with an explicit operational threshold, not an inferred causal
  label: `/turn` fast is <3 s (the empty-history histogram valley), provider fast is <5 s.
  Phase 1's <500 ms provider split is also quoted but is not directly comparable to full
  `/turn` latency or today's varied-prompt provider workload.
- Gemini was selected only at process import time (`rag.MODEL` patched in memory before
  importing `api`); no model or caching source configuration was edited.
- Azure TTS was not available on this host. Voice calculations assume 300 ms from sentence
  availability to first audio byte with a preconnected, reused streaming synthesizer. This
  is an engineering assumption, not an Azure SLA. Microsoft defines client first-byte
  latency as synthesis start to first client audio chunk, recommends streaming, and advises
  preconnection/reuse ([Microsoft latency guidance](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-lower-speech-synthesis-latency)).

## 2. Prompt-caching verdict

| condition | TTFT p50 | p90 | p95 | p99 | max | cache hits | fast/slow (<5 s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| shipped split + sticky session | 4,197 | 8,253 | 9,349 | 12,271 | 15,443 | 95/100 | 65/35 |
| combined + no session | 4,512 | 11,249 | 14,529 | 20,720 | 27,331 | 41/100 | 59/41 |
| **shipped savings** | **315** | **2,996** | **5,180** | **8,449** | **11,888** | — | — |

Prompt caching/sticky routing measurably improved TTFT in this matched observation: only
315 ms at p50, but 5.18 s at p95 and 8.45 s at p99. It did **not** collapse the slow mode:
35% of cache-on provider calls were still >=5 s, while the full DeepSeek `/turn` split was
34 fast/66 slow for empty history and 32/68 with history at the stated 3-second threshold.
The phase-2 conclusion turns out directionally correct at the tail, but its evidence sentence
was wrong: **“the decisive win is hit rate 50% -> 90% and cached bytes doubled.”** Hit rate
and cached bytes establish cache use/cost, not latency. The corrected evidence is the N=100
TTFT delta above. The decision-table phrase **“big tail cut; 50->90% hits”** was therefore
unsubstantiated in phase 2 even though phase 3 now measures a real tail cut.

## 3. Generation speed and first-sentence decomposition

The additive decompositions use the two observations straddling the median first-sentence
rank, so their averaged TTFT + generation components equal the reported p50 exactly.

| model | mode | lexical tok/s p50 | p10 | mean sentence tokens | first-sentence p50 decomposition |
|---|---|---:|---:|---:|---:|
| DeepSeek | empty | 38.2 | 6.3 | 37.4 | 5,814 TTFT + 739 generation = 6,554 ms |
| DeepSeek | history | 48.6 | 4.9 | 42.4 | 6,054 + 621 = 6,675 ms |
| Gemini | empty | 171.4 | 86.2 | 37.7 | 933 + 129 = 1,062 ms |
| Gemini | history | 159.1 | 78.4 | 36.8 | 777 + 272 = 1,049 ms |

For reference, the independent component medians (which do not algebraically add because
they may come from different requests) are DeepSeek empty TTFT 4,912 ms + sentence
generation 855 ms, versus Gemini empty 844 + 170 ms. Gemini attacks both budget components:
roughly 4.5x higher median lexical delivery throughput and much lower TTFT.

## 4. Full `/turn` latency table (milliseconds)

| model | mode | metric | p50 | p90 | p95 | p99 | max |
|---|---|---|---:|---:|---:|---:|---:|
| DeepSeek | empty | first SSE | 164 | 188 | 195 | 228 | 278 |
| DeepSeek | empty | first token | 4,912 | 13,995 | 16,109 | 22,664 | 28,212 |
| DeepSeek | empty | first sentence | 6,554 | 17,022 | 20,369 | 25,803 | 29,460 |
| DeepSeek | empty | done | 8,848 | 44,802 | 56,454 | 94,374 | 215,591 |
| DeepSeek | history | first SSE | 164 | 185 | 191 | 197 | 199 |
| DeepSeek | history | first token | 4,728 | 13,370 | 18,125 | 30,927 | 42,336 |
| DeepSeek | history | first sentence | 6,675 | 17,071 | 20,613 | 33,439 | 43,179 |
| DeepSeek | history | done | 11,727 | 41,024 | 65,624 | 112,587 | 178,715 |
| Gemini | empty | first SSE | 162 | 187 | 195 | 208 | 236 |
| Gemini | empty | first token | 844 | 2,706 | 5,986 | 10,608 | 12,024 |
| Gemini | empty | first sentence | 1,062 | 3,066 | 6,992 | 10,611 | 12,054 |
| Gemini | empty | done | 1,982 | 6,441 | 8,664 | 12,945 | 23,838 |
| Gemini | history | first SSE | 163 | 181 | 188 | 208 | 223 |
| Gemini | history | first token | 789 | 1,243 | 1,562 | 2,949 | 4,761 |
| Gemini | history | first sentence | 1,049 | 1,580 | 2,001 | 4,925 | 5,702 |
| Gemini | history | done | 1,506 | 3,435 | 3,726 | 5,702 | 6,613 |

First-token fast/slow (<3 s / >=3 s): DeepSeek empty 34/66, DeepSeek history 32/68,
Gemini empty 90/10, Gemini history 99/1. The full histograms are in the embedded outputs.

## 5. Gemini quality on this corpus

| check | Gemini | current DeepSeek baseline | verdict |
|---|---:|---:|---|
| retrieval (`eval.py`) | model-independent; unchanged | unchanged | handwritten RegArt@1/5 0.550/0.650; RegDoc@1/5 0.800/0.950 |
| call policy | 16/16 | 16/16 | unchanged |
| strict normalized numeric grounding, 20 rate questions | 20/20 | 20/20 | tie; **Gemini failures: none** |
| names retrieved document/article | 20/20 | 20/20 | tie |
| direct empty-evidence refusals | 5/5 | 5/5 by reading | both refused parametric answers |
| `/turn` refusal cases from `eval_calls.jsonl` | 3/3 cases | deterministic baseline | Gemini server returned `unsupported` for every expected refusal |

Normalization removes apostrophe/comma grouping, percent signs, and insignificant decimal
zeros, so `1'000.00`, `1,000`, `0.00`, and `0.00%` compare canonically. Every numeric token
in each answer had to occur in serialized retrieved evidence. The original DeepSeek scorer
printed 4/5 because its phrase matcher omitted “nuk kam”; reading the output confirms the
fifth answer said **“Nuk kam informacion në korpus për receta gatimi”** and did not provide
a recipe. The scorer now recognizes that refusal form.

Grounding is not full semantic correctness: Gemini's small-business answer lists four
unlabeled values, and both models' Procredit answer labels a service/maintenance value in
response to an administration question. Those outputs passed the requested numeric test
because all numbers were retrieved; this was noticed, not fixed.

## 6. Albanian register and fluency — judged by reading

I read all 20 paired outputs. These are three same-question pairs (verbatim):

| question | Gemini | DeepSeek |
|---|---|---|
{chr(10).join(quote_rows)}

Both models are fluent enough for professional Albanian; Albanian quality rules out neither.
Gemini is usually more direct and natural, leading with the answer (“Komisioni ... është”)
instead of the repeated DeepSeek preamble “Sipas/Bazuar në materialet e marra nga korpusi.”
Specific DeepSeek awkwardness includes the agreement error **“komisioni i administrimit të
kredisë konsumatore të pasiguruara”** (singular *kredisë* with plural *të pasiguruara*), the
unnatural preposition **“materialit të marrë në korpus”** (natural: *nga korpusi*), and the
English leakage **“Bank of Albania.”** Both sometimes reproduce table-label noun stacks.
Gemini also retains the English corpus term **“cash”** where natural prose would say *para
në dorë*, and uses labels such as **“Komisioni MIN/MAX”**; these are minor, source-induced
awkward constructions rather than broad fluency failures.

## 7. Voice budget

Cells show `p50 verdict / p95 verdict`. First audio = measured first sentence + the stated
300 ms Azure first-byte assumption.

| model | mode | first-audio p50 | first-audio p95 | 1.5 s target | 2.5 s target |
|---|---|---:|---:|---|---|
| DeepSeek | empty | 6,854 ms | 20,669 ms | FAIL / FAIL | FAIL / FAIL |
| DeepSeek | history | 6,975 ms | 20,913 ms | FAIL / FAIL | FAIL / FAIL |
| Gemini | empty | 1,362 ms | 7,292 ms | PASS / FAIL | PASS / FAIL |
| Gemini | history | 1,349 ms | 2,301 ms | PASS / FAIL | PASS / PASS |

Neither model meets a strict 1.5-second p95 first-audio SLO. Gemini meets both targets at
the median, and meets 2.5 seconds at p95 for established/history turns, but its empty-session
p95 fails both because of sparse OpenRouter outliers. Therefore the phase-2 claim that
Gemini's provider TTFT alone closed the voice budget was too strong: end-to-end first-sentence
and routing tails still matter.

## 8. Recommendation

- **Voice:** use `google/gemini-3.1-flash-lite` as the candidate. It is dramatically faster
  at median and p95, generates much faster, and matches DeepSeek on the requested corpus
  checks. Do not promise a 1.5-second p95; for a 2.5-second p95 across first turns, pin/qualify
  the provider path or obtain a stronger routing SLO first.
- **Text:** keep the shipped `deepseek/deepseek-v4-flash` default when latency is not binding.
  Current catalog prices measured in this run are $0.14/M input and $0.28/M output versus
  Gemini's $0.25/M and $1.50/M. Gemini is also defensible for latency-sensitive text, but a
  default switch is a separate cost/product decision and was not made here.
- **Albanian:** rules out neither model; Gemini was slightly more concise/natural by reading.

## 9. Anything noticed but not fixed

- One Gemini empty-history request (sample 37) received an OpenRouter SSE event with empty
  `choices`; production appended the safe human-agent handoff after partial text. It remains
  in the N=100 full-path measurement.
- DeepSeek has uncapped generation tails: measured `/turn` completion max was 215.6 s, and
  several unmeasured history primers also ran for minutes.
- Native cache telemetry is provider-dependent: cache-on still had 5 misses, cache-off had
  41 hits, and prompt-token usage sometimes varied for the same fixture.
- The lexical token proxy is delivery throughput, not hidden provider generation speed;
  SSE batching can produce very high per-request values.
- Numeric grounding does not detect wrong labels, omissions, or selection of the wrong
  retrieved rate; add a semantic answer-correctness scorer in a separate quality task.
- The HF Hub warning about unauthenticated downloads remains cosmetic; bge-m3 was cached.

## 10. Restoration and verification

The shipped model remains `deepseek/deepseek-v4-flash`. The cacheable leading system message
in `rag.grounded_messages` and sticky `session_id` in `api.stream_answer` are unchanged.
No server is left on port 8100. Final git/config verification is recorded immediately before
the final commit and summarized to the user.

## 11. Acceptance — full command outputs

The commands below used `.venv/bin/python` (the repository interpreter); command labels use
`python3` to match the acceptance wording. Every latency percentile comes from N=100.
"""

    parts = [body]
    for command, filename in TRANSCRIPTS:
        transcript = (EVIDENCE / filename).read_text(encoding="utf-8").replace("\r", "")
        parts.append(f"\n### `{command}`\n\n```text\n{transcript.rstrip()}\n```\n")
    REPORT.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {REPORT} with {len(TRANSCRIPTS)} full acceptance transcripts")


if __name__ == "__main__":
    main()

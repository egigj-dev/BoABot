# BoABot performance: from multi-call RAG to a voice-capable latency profile

BoABot is an Albanian retrieval-augmented generation (RAG) customer-service chatbot built around a streaming `/turn` endpoint for web chat and a future voice/call-center path. The August 2026 performance work started from a 2,500 ms first-token target and voice goals of 1.5 seconds and 2.5 seconds to first audio. Three phases removed avoidable local work, made prompt caching more effective, and measured the provider and model choices at N=100. The result is a fast acknowledgement path—about 160 ms to the first SSE event—and no measured quality regression, but the shipped default model still takes about 4.9 seconds at p50 to produce its first token and has severe tail latency. A benchmark-only Gemini configuration shows that the same application can reach about 0.8 seconds to first token and, for established conversations, about 1.35 seconds to estimated first audio at p50 and 2.30 seconds at p95. The largest remaining performance decision is therefore model/provider selection, followed by actually implementing and operating the voice path.

## What the measurements mean

- **First SSE** is the first event returned by `/turn`. It proves the server has started responding, but it is usually a retrieval-status event rather than answer text.
- **First token** is the first model-generated answer text. This is the project's 2,500 ms text target.
- **First sentence** is the first punctuation-terminated unit that could be handed to streaming text-to-speech (TTS). A completed answer without punctuation is treated as one complete utterance.
- **Done** is the end of the streamed answer.
- **First audio** was not measured from a speech service. Phase 3 calculated it as first-sentence time plus an assumed 300 ms TTS first-byte delay for a preconnected streaming synthesizer. It is an engineering estimate, not an Azure SLA. (Sources: `bench_turn.py`, `phase3_analyze.py`, `FINAL_REPORT_PHASE3.md`.)

## How it was: the baseline

Before Phase 1, a warm-process `/turn` request had these headline latencies:

| Metric | p50 | p95 |
|---|---:|---:|
| First SSE event | 1,856 ms | 2,343 ms |
| First token | 4,724 ms | 8,062 ms |
| Done | 6,209 ms | 9,707 ms |

Source: `FINAL_REPORT.md`. That report explicitly identifies these as the task-spec baseline. The preserved file named `boabot_baseline_bench.txt` is confusingly named: it contains an optimized N=10 run with first-SSE p50 153 ms, not the original 1,856 ms baseline.

Cold start was much worse. With application lifespan startup disabled, the first request took 6,008 ms to its first SSE event, 10,046 ms to its first token, and 11,322 ms to finish (N=1; source: `boabot_cold_no_warmup.txt`).

The pre-Phase-1 request path did too much serial work before and around generation:

1. The model first completed a tool-decision request before retrieval. A ten-call component sample measured that removed stage at 4,471 ms p50 and 8,022 ms p95 (source: `boabot_model_stage_metrics.txt`). Because this component sample and the endpoint baseline were collected under different provider conditions, 4,471 ms should not be subtracted mechanically from the 1,856 ms first-SSE baseline.
2. Every history turn then made another non-streaming model call to rewrite the query, even when the question was already explicit. `FINAL_REPORT.md` quotes the original rewrite baseline as about 1,412 ms. A later ten-call raw sample in `boabot_model_stage_metrics.txt` instead measured 8,338 ms p50 and 12,494 ms p95. These disagree, so the safe conclusion is that the unconditional external round trip was both expensive and highly variable—not that one of those values is a universal per-turn saving.
3. The query was embedded once during call-center routing and again during retrieval: two BGE-M3 encodes per turn. One encode measured 129.776 ms p50 and 151.265 ms p95 over 100 calls (source: `boabot_component_metrics.txt`).
4. Retrieval opened a new PostgreSQL/pgvector connection per turn. The measured p50 was 39.367 ms with a new connection (source: `boabot_component_metrics.txt`).
5. There was no startup warmup, so the first user request paid for model loading, a first encode, connection setup, and a first vector query (sources: `FINAL_REPORT.md`, `boabot_cold_no_warmup.txt`).

## What was done to improve it

### Phase 1: remove deterministic application overhead — shipped

The central change was to make application retrieval the default path and reserve the model for the one grounded streaming answer. The full tool-calling decision completion no longer runs before the stream. The Phase 1 final acceptance report moved first-SSE p50 from 1,856 ms to 141 ms with empty history and 152 ms with history—roughly a 92% cut—and removed the separately measured 4,471 ms p50 tool-decision stage from the critical path (sources: `FINAL_REPORT.md`, `boabot_model_stage_metrics.txt`). Preserved N=10 raw runs show slightly different post-change first-SSE medians, 153 ms empty and 146 ms with history (`boabot_baseline_bench.txt`, `boabot_baseline_history_bench.txt`); all versions support the same approximately 150 ms result.

Rewriting became conditional. `needs_rewrite()` detects contextual or elliptical follow-ups such as “Po për 24 muaj?” and skips the model call for explicit questions. The original eight-explicit/seven-elliptical check found all 7 elliptical turns flagged and all 8 explicit turns skipped, but its 6/8 direct versus 5/8 rewritten retrieval result was too small to settle quality (`boabot_rewrite_quality.txt`). Phase 2 repeated the check with 40 labeled follow-ups: explicit retrieval was 0.800 with rewrite off versus 0.750 on; elliptical retrieval was 0.000 off versus 0.700 on; overall it was 0.400 off versus 0.725 on. That larger sample supports the shipped conditional policy: pay for rewriting where context is essential and avoid it where it is a wash (source: `FINAL_REPORT_PHASE2.md`).

Three smaller local optimizations also shipped:

- **Connection pooling:** pgvector retrieval fell from 39.367 ms p50 with a new connection to 27.566 ms with the pool, a measured 11.801 ms reduction per retrieval (N=100; source: `boabot_component_metrics.txt`).
- **Startup warmup:** the startup hook loads BGE-M3, performs a throwaway encode, opens the pool, and executes one vector query. The first request's first SSE fell from 6,008 ms to 165 ms, a 5,843 ms reduction; first token fell from 10,046 ms to 2,651 ms and done from 11,322 ms to 3,654 ms (two N=1 observations; sources: `boabot_cold_no_warmup.txt`, `boabot_cold_after_warmup.txt`).
- **Embedding reuse:** when the retrieval string is byte-identical to the question already embedded by routing, retrieval reuses that vector. The path went from two encodes to one; the benchmark shutdown log reported 26 hits and 0 misses, a 100% reuse ratio. An assertion prevents reuse for changed text (source: `FINAL_REPORT.md`; implementation: `rag.py`, `retrieve.py`, `api.py`).

Retrieval quality remained unchanged after these changes: handwritten RegArt@1 was 0.550, RegArt@5 0.650, RegDoc@1 0.800, and RegDoc@5 0.950; call-policy cases passed 16/16 (sources: `boabot_baseline_eval.txt`, `FINAL_REPORT.md`).

### Phase 2: isolate provider TTFT and test the remaining levers — caching shipped, other choices measured

Phase 2 bypassed BoABot and called OpenRouter directly. For `deepseek/deepseek-v4-flash`, one fixed k=5 fixture over N=100 produced provider time to first token (TTFT) of 361 ms p50, 2,406 ms p90, 3,791 ms p95, 11,465 ms p99, and 14,045 ms max. Sixty-eight of 100 calls were below 500 ms, leaving an approximately 32% long mode; completion p50 was 2,616 ms and p99 49,145 ms. This proved that the residual problem was primarily the external provider distribution rather than routing, embedding, or database work (source: `phase2_acceptance_bench_provider.txt`).

Prompt caching then shipped in two parts: `rag.grounded_messages()` puts the invariant system instruction in a stable leading message and dynamic evidence in the following message, while `api.stream_answer()` adds the conversation `session_id` for sticky OpenRouter routing. In the Phase 2 N=50 observations, the combined layout had 25/50 cache hits and 50,432 cached tokens; the split layout had 45/50 hits and 104,448 cached tokens. TTFT p50 was 457 ms before and 661 ms after, so this pair proved greater cache use but did **not** by itself prove a latency reduction (sources: `phase2_cache_before.txt`, `phase2_cache_after.txt`). Phase 3 later measured the latency benefit with matched N=100 runs.

The other Phase 2 experiments were deliberately not turned into production changes:

- **k sweep:** k=3, 5, and 8 had prompt-token p50 values of 1,568, 2,367, and 3,953; TTFT p50 values of 618, 1,314, and 1,087 ms; and TTFT p95 values of 1,169, 2,635, and 4,963 ms. The non-monotonic result was dominated by cache/provider variation, while the retrieval eval was unchanged, so k=5 stayed in place (N=50 each; sources: `phase2_k3.txt`, `phase2_k5.txt`, `phase2_k8.txt`, `FINAL_REPORT_PHASE2.md`).
- **System-prompt trim:** the median prompt shrank from 2,501 to 2,427 tokens, only 74 tokens. Current versus trimmed TTFT was 671/2,403 ms versus 1,953/5,288 ms at p50/p95, citations fell from 14 to 12, and unsupported-question refusals fell from 5 to 3. The trim was not shipped (N=20 per prompt; sources: `phase2_prompt_quality.json`, `FINAL_REPORT_PHASE2.md`).
- **Model comparison:** Gemini 3.1 Flash Lite produced TTFT p50/p99 of 629/1,137 ms and 193.0 provider tokens/s; Mistral Small 2603 produced 612/2,219 ms and 104.5 tokens/s; GPT-4.1 Mini produced 760/3,648 ms and 57.6 tokens/s. DeepSeek's reference was 361/11,465 ms and 20.6 tokens/s. Gemini had the tightest observed tail and was recommended for voice, but the default was not switched (DeepSeek N=100; alternatives N=30 each; sources: `phase2_acceptance_bench_provider.txt`, `phase2_model_google_gemini-3.1-flash-lite.txt`, `phase2_model_mistralai_mistral-small-2603.txt`, `phase2_model_openai_gpt-4.1-mini.txt`).

The 33-question Phase 2 model comparison—13 FAQ, 10 rate, and 10 regulation questions—found numeric-grounding/citation results of 25/32 and 27 citations for Gemini, and 26/31 and 24 citations for Mistral; the denominators exclude answers without numeric claims. Both were judged fluent and register-appropriate in Albanian (sources: `phase2_model_quality.json`, `FINAL_REPORT_PHASE2.md`).

### Phase 3: measure the complete path at N=100

Phase 3 ran four complete `/turn` distributions. Gemini was patched only in process for benchmarking; the source default remained DeepSeek. The headline table comes from `phase3_analysis.txt` and `phase3_analysis.json`, recomputed from the four named N=100 raw JSON/text runs.

| Model | Mode | Metric | p50 | p90 | p95 | p99 | Max |
|---|---|---|---:|---:|---:|---:|---:|
| DeepSeek | Empty | First SSE | 164 | 188 | 195 | 228 | 278 |
| DeepSeek | Empty | First token | 4,912 | 13,995 | 16,109 | 22,664 | 28,212 |
| DeepSeek | Empty | First sentence | 6,554 | 17,022 | 20,369 | 25,803 | 29,460 |
| DeepSeek | Empty | Done | 8,848 | 44,802 | 56,454 | 94,374 | 215,591 |
| DeepSeek | History | First SSE | 164 | 185 | 191 | 197 | 199 |
| DeepSeek | History | First token | 4,728 | 13,370 | 18,125 | 30,927 | 42,336 |
| DeepSeek | History | First sentence | 6,675 | 17,071 | 20,613 | 33,439 | 43,179 |
| DeepSeek | History | Done | 11,727 | 41,024 | 65,624 | 112,587 | 178,715 |
| Gemini | Empty | First SSE | 162 | 187 | 195 | 208 | 236 |
| Gemini | Empty | First token | 844 | 2,706 | 5,986 | 10,608 | 12,024 |
| Gemini | Empty | First sentence | 1,062 | 3,066 | 6,992 | 10,611 | 12,054 |
| Gemini | Empty | Done | 1,982 | 6,441 | 8,664 | 12,945 | 23,838 |
| Gemini | History | First SSE | 163 | 181 | 188 | 208 | 223 |
| Gemini | History | First token | 789 | 1,243 | 1,562 | 2,949 | 4,761 |
| Gemini | History | First sentence | 1,049 | 1,580 | 2,001 | 4,925 | 5,702 |
| Gemini | History | Done | 1,506 | 3,435 | 3,726 | 5,702 | 6,613 |

All values are milliseconds. Sources: `phase3_analysis.txt`, `phase3_analysis.json`, `phase3_deepseek_empty_N100.txt`, `phase3_deepseek_history_N100.txt`, `phase3_gemini_empty_N100.txt`, and `phase3_gemini_history_N100.txt`.

There is one material raw/recomputed discrepancy. The original DeepSeek history transcript reports first-sentence p50/p95/p99 of 7,032/20,663/43,179 ms over 99 observations (`phase3_deepseek_history_N100.txt`). One completed answer had no punctuation. `phase3_analyze.py` deterministically assigns its done time as its first-utterance boundary, producing the reconciled 100-observation values 6,675/20,613/33,439 ms shown above (`phase3_analysis.txt`, `FINAL_REPORT_PHASE3.md`). No request was rerun or dropped.

Gemini reduced empty-history first-token p50 by 4,068 ms relative to DeepSeek, from 4,912 to 844 ms, and done p50 by 6,866 ms, from 8,848 to 1,982 ms. Its median lexical delivery rate was 171.4 tokens/s versus DeepSeek's 38.2, about 4.5 times higher. These are arithmetic comparisons of the Phase 3 values, not separate experiments (source values: `phase3_analysis.txt`).

#### Cache isolation

Phase 3 compared the shipped split-prompt/sticky-session configuration with a combined-prompt/no-session structural rollback, using DeepSeek, the same ten prompts, k=5, and N=100:

| Condition | TTFT p50 | p90 | p95 | p99 | Max | Cache hits | Fast/slow at 5 s |
|---|---:|---:|---:|---:|---:|---:|---:|
| Cache-friendly structure on | 4,197 | 8,253 | 9,349 | 12,271 | 15,443 | 95/100 | 65/35 |
| Structural cache rollback | 4,512 | 11,249 | 14,529 | 20,720 | 27,331 | 41/100 | 59/41 |
| Observed saving | 315 | 2,996 | 5,180 | 8,449 | 11,888 | — | — |

Sources: `phase3_provider_cache_on_N100.txt`, `phase3_provider_cache_off_N100.txt`, `phase3_analysis.txt`. “Cache off” is only the structural rollback: provider-native automatic caching could not be disabled, which is why it still recorded 41 hits. The comparison measured a substantial tail reduction, correcting Phase 2's earlier inference from hit counts alone, but 35% of cache-on calls still took at least five seconds.

#### Voice budget

| Model | Mode | Estimated first-audio p50 | Estimated first-audio p95 | 1.5 s target, p50/p95 | 2.5 s target, p50/p95 |
|---|---|---:|---:|---|---|
| DeepSeek | Empty | 6,854 ms | 20,669 ms | FAIL / FAIL | FAIL / FAIL |
| DeepSeek | History | 6,975 ms | 20,913 ms | FAIL / FAIL | FAIL / FAIL |
| Gemini | Empty | 1,362 ms | 7,292 ms | PASS / FAIL | PASS / FAIL |
| Gemini | History | 1,349 ms | 2,301 ms | PASS / FAIL | PASS / PASS |

Source: `phase3_analysis.txt`. These values add the stated 300 ms TTS assumption; no TTS call ran. Neither model meets a strict 1.5-second p95 goal. Gemini passes 2.5 seconds at p95 only for the measured established/history mode.

Quality held on the checks that were run. Retrieval remained RegArt@1/5 0.550/0.650 and RegDoc@1/5 0.800/0.950, and call routing remained 16/16 (`phase3_eval.txt`, `phase3_eval_calls.txt`). On 20 rate questions, both models scored 20/20 for strict normalized numeric grounding and 20/20 for naming a retrieved document/article (`phase3_quality_deepseek.txt`, `phase3_quality_gemini.txt`, `FINAL_REPORT_PHASE3.md`). Gemini refused all 5/5 direct empty-evidence questions and all 3/3 selected `/turn` policy-refusal cases. The original DeepSeek script printed 4/5 direct refusals because its marker list omitted “nuk kam”; the saved answer is a refusal, so the Phase 3 report records 5/5 by manual review (`phase3_quality_deepseek.txt`, `FINAL_REPORT_PHASE3.md`). Numeric grounding is narrower than semantic correctness: the report found unlabeled values and one wrong service/administration label that still passed because every number occurred in retrieved evidence.

## Consolidated endpoint before and after

| State and test | First SSE p50/p95 | First token p50/p95 | First sentence p50/p95 | Done p50/p95 | Source |
|---|---:|---:|---:|---:|---|
| Original warm baseline | 1,856 / 2,343 | 4,724 / 8,062 | Not measured | 6,209 / 9,707 | `FINAL_REPORT.md` |
| Phase 1 after, empty, N=10 | 141 / 171 | 4,444 / 27,716 | Not measured | 7,175 / 171,656 | `FINAL_REPORT.md` |
| Phase 1 after, history, N=10 | 152 / 176 | 3,900 / 13,349 | Not measured | 7,499 / 28,660 | `FINAL_REPORT.md` |
| Phase 3 shipped DeepSeek, empty, N=100 | 164 / 195 | 4,912 / 16,109 | 6,554 / 20,369 | 8,848 / 56,454 | `phase3_analysis.txt` |
| Phase 3 shipped DeepSeek, history, N=100 | 164 / 191 | 4,728 / 18,125 | 6,675 / 20,613 | 11,727 / 65,624 | `phase3_analysis.txt` |
| Phase 3 benchmark Gemini, empty, N=100 | 162 / 195 | 844 / 5,986 | 1,062 / 6,992 | 1,982 / 8,664 | `phase3_analysis.txt` |
| Phase 3 benchmark Gemini, history, N=100 | 163 / 188 | 789 / 1,562 | 1,049 / 2,001 | 1,506 / 3,726 | `phase3_analysis.txt` |

The Phase 1 N=10 completion tails demonstrate why those small runs should not be compared as if they were controlled before/after samples. The durable Phase 1 signal is the removal of pre-stream work and the approximately 92% first-SSE reduction; Phase 3's N=100 runs are the stronger description of the current endpoint.

## Lever attribution and decisions

| Lever | Measured effect | Observed quality cost | Decision |
|---|---|---|---|
| Remove model tool decision | Removed a 4,471 ms p50 sampled model stage; endpoint first-SSE p50 fell from 1,856 to about 141–152 ms | None in retrieval/call evals | **Shipped** |
| Conditional rewrite | Avoids one variable external completion on explicit history turns; report baseline about 1,412 ms, later raw sample 8,338 ms p50 | Net retrieval benefit in N=40: 0.400 off to 0.725 on overall, with rewrite applied selectively | **Shipped** |
| PostgreSQL connection pool | 39.367 to 27.566 ms p50, saving 11.801 ms per retrieval | None measured | **Shipped** |
| Startup warmup | First-request first SSE 6,008 to 165 ms, saving 5,843 ms in the N=1 comparison | None measured | **Shipped** |
| Byte-identical embedding reuse | Avoids one 129.776 ms p50 encode; 26/26 observed reuse hits | Eval unchanged; guarded by byte-equality assertion | **Shipped** |
| Split prompt + sticky session | Phase 3 TTFT savings: 315 ms p50, 5,180 ms p95, 8,449 ms p99 | None measured | **Shipped** |
| k=5 to k=3 | No clean latency win across the independent N=50 runs | Potential recall/context loss by construction; no benefit justified it | **Do not ship** |
| Trim system prompt | 74 prompt tokens saved; no observed latency win | Citations 14 to 12; refusals 5 to 3 | **Do not ship** |
| Switch DeepSeek to Gemini | Empty `/turn` first token 4,912 to 844 ms p50 and 22,664 to 10,608 ms p99; history first-audio estimate meets 2.5 s at p95 | Requested Phase 3 checks tied; Gemini Albanian judged slightly more direct/natural | **Recommended for voice; not switched** |
| Start TTS on the first sentence | No implementation measurement; the budget table only models +300 ms | Not evaluated | **Proposed, not implemented** |

Sources, in row order: `boabot_model_stage_metrics.txt`, `FINAL_REPORT.md`, `FINAL_REPORT_PHASE2.md`, `boabot_component_metrics.txt`, `boabot_cold_no_warmup.txt`, `boabot_cold_after_warmup.txt`, `phase3_analysis.txt`, `phase2_k3.txt`, `phase2_k5.txt`, `phase2_prompt_quality.json`, `phase3_quality_deepseek.txt`, and `phase3_quality_gemini.txt`.

One report-level inconsistency is worth making explicit: the Phase 2 decision table labels tool-call removal as “~1,400 ms,” while the raw tool-decision sample is 4,471 ms p50 and the Phase 1 report associates approximately 1,412 ms with rewriting (`FINAL_REPORT_PHASE2.md`, `boabot_model_stage_metrics.txt`, `FINAL_REPORT.md`). The attribution table above follows the named raw stages and does not use the Phase 2 label as a measured tool-call value.

## What still falls short of production level

The following are **measured facts**:

- The shipped default remains `deepseek/deepseek-v4-flash`. Its empty-history first-token p50 is 4,912 ms, almost twice the stated 2,500 ms target, with p95 16,109 ms and p99 22,664 ms. History p99 is 30,927 ms. Gemini is the evidence-backed tight-tail candidate, but the switch was deliberately left as a cost/product decision (sources: `rag.py`, `phase3_analysis.txt`, `FINAL_REPORT_PHASE3.md`).
- The default model fails both the 1.5-second and 2.5-second estimated first-audio goals at p50 and p95. Even Gemini does not meet a 1.5-second p95 target, and its empty-history p95 misses 2.5 seconds (source: `phase3_analysis.txt`).
- DeepSeek's generation tail is also uncontrolled: empty-history done p99 was 94,374 ms and max 215,591 ms; history done p99 was 112,587 ms and max 178,715 ms (source: `phase3_analysis.txt`).
- Caching helps, especially at the tail, but it does not eliminate the slow mode: 35/100 cache-friendly provider calls were at least five seconds (source: `phase3_analysis.txt`).
- The repository does not implement speech recognition, TTS, streaming audio, or telephony. “First audio” is therefore a modeled budget, not an end-to-end voice measurement (sources: `api.py`, `phase3_analyze.py`, `FINAL_REPORT_PHASE3.md`).
- `/turn` does not forward native provider token usage or generation IDs, so Phase 3 used a lexical speed proxy and offline benchmark artifacts (sources: `api.py`, `bench_turn.py`, `FINAL_REPORT_PHASE3.md`).

The following are **engineering assessments based on the repository and those measurements**, not claims from a production load test:

- A latency-sensitive voice deployment should qualify or pin a tight-tail model/provider before promising an SLO. Gemini is the current evidence-backed candidate; the N=100 empty-session outliers show that model choice alone does not guarantee a 1.5-second p95.
- TTS should consume the first complete sentence as it arrives, with a reused/preconnected synthesizer. That path still needs implementation and real first-audio measurement under the intended speech and telephony stack.
- The application has a 90-second `requests` timeout and converts provider errors to a human-handoff response, but it has no latency-budget timeout, retry policy, hedged request, or alternate-provider fallback. Continuous SSE activity can outlive the nominal read timeout, as the 215.6-second completion demonstrates (`api.py`, `phase3_deepseek_empty_N100.txt`). A production design needs explicit cancellation and fallback behavior that preserves conversation safety.
- All performance runs were sequential; there is no documented concurrency, saturation, soak, or multi-region failover test. Cache-on still had five misses, and there is no explicit cache-warm scheduling. Capacity and regional behavior therefore remain unknown rather than bad by measurement.
- Latency measurement lives in benchmark scripts and saved reports. No Prometheus/OpenTelemetry request histograms, SLO dashboards, provider-generation correlation, or alerting are present in the application. Production diagnosis would need in-service first-SSE/first-token/first-sentence/done telemetry, model/provider identity, cache telemetry, and error/fallback outcomes.
- The quality harness is useful but small: the three retrieval JSONL sets contain 40 cases each, the call-policy set has 16 cases, the Phase 2 model comparison has 33 questions, and the Phase 3 numeric check has 20 rate questions. Numeric grounding does not prove correct labeling or complete semantic answers. There is no CI workflow or regression gate in this repository, so these tests should become continuous and should add a semantic answer-correctness judge before a model default changes.

## Appendix: how these numbers were produced

`bench_provider.py` isolates the external model path. It does not import BoABot; instead it builds prompts from ten frozen production-retrieval fixtures and calls OpenRouter's streaming chat-completions endpoint directly. It records first content TTFT, completion time, native prompt/completion/reasoning/cache usage when available, cost, and provider-token throughput. Phase 2 used N=100 for the fixed DeepSeek TTFT run, N=50 for cache and k experiments, N=20 per system-prompt variant, and N=30 per alternative model. Phase 3 used N=100 varied-prompt runs for both cache structures (sources: `bench_provider.py`, `FINAL_REPORT_PHASE2.md`, `FINAL_REPORT_PHASE3.md`).

`bench_turn.py` measures the complete local HTTP `/turn` path, including routing, retrieval, prompt construction, provider streaming, and SSE handling. It cycles ten questions sequentially. In history mode, each measured request gets a fresh unmeasured primer and then reuses its returned session ID; primer latency and cost are incurred but excluded from the recorded target turn. Phase 1 and Phase 2 endpoint runs were generally N=10; the first Phase 2 history attempt stopped after two observations and was later rerun at N=10. Phase 3 used N=100 for each model/mode pair (sources: `bench_turn.py`, `phase2_acceptance_bench_turn_history.txt`, `phase2_bench_history_N10.txt`, `FINAL_REPORT_PHASE3.md`).

The scripts use `statistics.median` for p50. p90, p95, and p99 use nearest rank: sort the N values and select rank `ceil(p × N)`. Phase 3 reports describe all percentiles as nearest-rank, but the implementation's even-N p50 is the average of the two middle observations rather than a single nearest-rank observation; rounded headline values come from the scripts (`bench_provider.py`, `bench_turn.py`, `phase3_analyze.py`).

`phase3_analyze.py` recomputes the headline tables from the six saved JSON datasets, reconciles a punctuation-free completed answer, calculates the cache deltas and operational fast/slow splits, and adds the fixed 300 ms TTS assumption. `phase3_build_report.py` embeds the full console transcripts in the final report. `phase3_quality.py` retrieves evidence through production code, normalizes numeric formats, requires every answer number to exist in the retrieved evidence, checks citation metadata, and exercises selected refusal paths. `eval.py` measures retrieval recall and latency; `eval_calls.py` checks deterministic call-center routing (sources: the named scripts).

Finally, the comparisons are matched but not randomized crossovers. Runs were sequential and provider conditions could change between them; cache state also varied. The Phase 3 lexical “token” rate counts Unicode words, numbers, and punctuation divided by first-token-to-done wall time because `/turn` does not expose provider usage. It is useful for comparing the two saved `/turn` runs, but it is neither a billing-token rate nor a direct measure of hidden provider generation speed, and SSE batching can inflate individual values (source: `FINAL_REPORT_PHASE3.md`).

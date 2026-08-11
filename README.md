# BoABot

BoABot is a trustworthy Albanian banking and contact-center assistant. It answers questions about Bank of Albania regulations and comparative Albanian-bank fees and rates, using a controlled corpus of 98 Bank of Albania PDFs plus rate tables, and routes the caller to a human when the available evidence is insufficient, unsafe, or account-specific. The implemented foundation is a FastAPI text service with an SSE `/turn` endpoint; future voice transport is intended to use the same structured, retrieval-guarded conversation contract.

## BEFORE / AFTER

| Dimension | Before | After |
|---|---|---|
| Retrieval evaluation | The older generated rate set contained known bank/gold inconsistencies and overlapped its source chunks heavily, so it mainly checked whether the pipeline was wired together. | On the 40-case handwritten set—20 rate and 20 regulation questions—exact-ID recall is **0.575 at 1**, **0.950 at 5**, and **1.000 at 10** (**40/40** gold chunks found in the top 10). The separate FAQ diagnosis found a data gap: **0/13** gold URLs had ever been indexed, so `eval.py` now skips that block instead of publishing misleading zero recall. |
| Rate-family gate | Enforcement was incomplete for some institutional price queries, while fitted exemptions also let some regulation passages bypass the intended evidence-family rule. | The intent-based rule is enforced for **19/20** handwritten rate fixtures. `rate_0027`, a non-institution small-business category question, is the sole inactive case by design. |
| Handoff-intent evaluation | The old row-stratified **232/100** split leaked normalized phrase families: **64%** of test rows belonged to families also present in training. | The grouped split has **233** training rows and **99** test rows, with **zero phrase-family overlap**. `eval_handoff.py` reports both splits so the leakage effect remains visible. |
| Prompt caching | On DeepSeek, the split prompt plus sticky session produced **45/50** cache hits and **104,448** cached tokens. That measurement was later presented as a shipped latency lever. | The default is now Gemini, and a controlled closeout run measured **0/30** turns with `cached_tokens > 0`. The prompt structure remains, and per-turn cache telemetry is exposed, but the cache claim in `PERFORMANCE.md` is stale for the shipped model. |
| Response start and model TTFT | The historical warm baseline took **1,856 ms p50** to emit the first SSE event. | Removing avoidable pre-stream work brought first-SSE p50 to roughly **145–170 ms** in small runs. In the Phase 3 Gemini benchmark, first-token TTFT p50/p95/p99 was **844/5,986/10,608 ms** with empty history and **789/1,562/2,949 ms** with history. These are provider-sensitive benchmark results, not a voice-service SLO. |
| Version control and repository hygiene | Corrective briefs and load-bearing artifacts were untracked, and `.gitignore` broadly ignored `*.jsonl`, including data required by serving and evaluation. | The verified baseline was captured in `initial commit: Albanian banking RAG service, evals, and performance work`; Tasks 7–9 were committed separately. Required JSONL data is tracked, while secrets, large generated corpus artifacts, local database state, and virtual environments remain ignored. |

## FAILURES CAUGHT AND CORRECTED

### 1. (a) THE EVAL-GENERATOR FALLBACK

**Failure.** The older generator in Cell 41 assigned rate IDs from row order and chose a random bank whenever it could not identify a known bank in the source chunk. That fallback created documented cases where the question named one bank while the gold rate chunk described another. Because the generated questions were also derived directly from their source chunks, the set substantially overlapped the material it was testing.

**Why it mattered.** A retrieval score can look healthy while evaluating mislabeled examples or near-copies of indexed text. That makes the set useful as a pipeline sanity check, but weak evidence for how the assistant will handle natural caller questions.

**Correction.** `make_eval.py` extracts bank names from each chunk's own text, skips or resamples chunks without an identifiable institution, and writes both `gold_id` and `gold_url`. The harder reference is `eval_handwritten.jsonl`, whose **20 rate and 20 regulation** cases are checked by `validate_eval.py`; the generated set remains explicitly positioned as a smoke test rather than a hard benchmark.

### 2. (b) THE ROUTING LEAKAGE

**Failure.** The original handoff evaluation used a row-stratified **232/100** train/test split. Normalized variants of the same underlying phrase could land on both sides, and **64%** of old test rows were members of phrase families already represented in training.

**Why it mattered.** The classifier could appear to generalize while mostly recognizing spelling, punctuation, whitespace, or diacritic variants of phrases it had already seen. That inflated confidence in escalation accuracy.

**Correction.** The data is now grouped by intent plus normalized-text phrase family before splitting. `handoff_split_grouped.json` contains **233 train / 99 test** rows and keeps every family wholly on one side, producing **zero family overlap**. `eval_handoff.py` recomputes the old and grouped results side by side and verifies the frozen production probe against the grouped split.

### 3. (c) THE FITTED GATE EXEMPTION

**Failure.** An early rate-family rule was patched with a `librin bankar` exception and a fixed `REGULATION_TERMS` list so several regulation eval questions containing words such as *norma* or *interesit* would pass. The exemption was fitted to known cases and too broad: matching one of those terms could let regulation evidence escape the `rate_`-chunk requirement.

**Why it mattered.** The gate exists to prevent a regulation passage from being quoted as a commercial tariff. Eval-specific wording inside production trust logic both weakens that protection and makes the evaluation circular.

**Correction.** `trust.py` now requires a `rate_` chunk only when a folded query contains both a rate term and either a corpus-derived commercial-bank identity or a price-intent phrase. Bank tokens are loaded from `rate_tables.jsonl`; regulator labels, non-institution categories, and generic/geographic forms such as `albania` and `shqiperise` are filtered out. The follow-up acronym fix derives `otp` from the corpus and restores OTP enforcement. The final result is **19/20** enforced rate fixtures, with only `rate_0027` inactive by design, and no eval/trap literal is allowed in `trust.py`.

### 4. (d) THE STALE CACHE ROW

**Failure.** `PERFORMANCE.md` records a real DeepSeek result: separating the stable system prompt from dynamic evidence and adding sticky `session_id` routing yielded **45/50** cache hits and **104,448** cached tokens. The document still describes that layout as a shipped caching benefit even though the default model later changed to Gemini.

**Why it mattered.** Cache behavior is provider- and model-dependent. Carrying the DeepSeek result forward implied a latency benefit that had not been demonstrated for the model users actually reach.

**Correction.** The API now records provider usage, including `cached_tokens`, for each turn. The first live Gemini turn reported zero cached tokens, and the controlled Task 9 history-mode run confirmed **0/30** turns with a positive cache count. The split prompt remains a reasonable structure, but no Gemini cache saving is claimed until telemetry demonstrates one; the corresponding `PERFORMANCE.md` row should be read as historical DeepSeek evidence.

## ARCHITECTURE / HOW IT FITS TOGETHER

- **Corpus and indexing:** Bank of Albania regulation chunks and comparative fee/rate rows are prepared as JSONL, embedded with `BAAI/bge-m3`, stored in a portable Parquet artifact, and loaded into PostgreSQL/pgvector by `load.py`.
- **Retrieval:** `retrieve.py` embeds the search query, performs cosine search in pgvector, and preserves the load-bearing `status IN ('canonical', 'base')` filter so amendment and superseded rows are excluded from serving.
- **Trust boundary:** `trust.input_gate()` rejects encoded or instruction-override input before generation. `trust.trusted_hits()` then checks evidence relevance and, for institutional price intent, requires evidence from the `rate_` family.
- **Grounded generation:** `rag.py` builds the split system/evidence prompt and calls the configured OpenRouter model. `api.py` streams response tokens and provider usage over SSE; generation is allowed only after retrieval and trust checks succeed.
- **Turn contract:** `POST /turn` finishes with one of `{answer, clarify, unsupported, handoff, repeat}`, plus an opaque session ID, vetted source metadata, and handoff/PII flags. A voice bridge is expected to consume this contract rather than bypass it; vetted passage text is exposed only via an opt-in `include_vetted_text` request flag (default OFF), intended solely for the authenticated voice bridge.
- **Contact-center policy:** `callcenter.py` owns the handoff-intent classifier, deterministic sensitive-intent and PII handling, repeat/clarification behavior, and the bounded in-process session store. The frozen classifier is audited by `eval_handoff.py`.

## CURRENT STATE / REMAINING WORK

The guarded text and SSE service is implemented; production voice and contact-center operation still require:

1. A Gemini Live/WebSocket audio bridge connected to the `/turn` contract.
2. A telephony provider and call-control integration.
3. Redis-backed sessions, authentication, audit logging, metrics, and agent-queue integration.
4. A Caddy reverse proxy with HTTPS and a privately bound application service.
5. Richer evaluation for Albanian ASR noise, interruptions/barge-in, live latency targets, and confirmed live-agent handoff acceptance.

## REPOSITORY MAP

| Path | Role |
|---|---|
| `api.py` | FastAPI application, web client, `/turn` SSE orchestration, structured outcomes, and per-turn usage telemetry. |
| `rag.py` | OpenRouter configuration, conditional query rewrite, grounded split-prompt construction, and model helpers. |
| `trust.py` | Deterministic input, relevance, unsupported-category, and intent-based rate-family gates. |
| `retrieve.py` | Shared bge-m3 embedding path, PostgreSQL connection pool, live-status filtering, and pgvector search. |
| `callcenter.py` | Session state, PII and sensitive-intent policy, repeat/clarification behavior, and frozen handoff classifier. |
| `eval.py` | Retrieval recall and latency reporting for generated and handwritten sets; skips the unindexed FAQ source. |
| `eval_calls.py` | Deterministic call-policy scenario evaluation. |
| `eval_handoff.py` | Phrase-family leakage audit, grouped-split classifier evaluation, and production-probe verification. |
| `eval_asr_noise.py` | Evaluation of routing and trust behavior under Albanian ASR-like text corruption. |
| `rate_rule_gap.py` | Measures top-result exposure for handwritten rate questions where the rate-family rule is inactive. |
| `make_eval.py` | Generates answerable rate retrieval fixtures from institution names found in their own source chunks. |
| `inspect_parquet.py` | Read-only sanity inspection for the embedded Parquet artifact. |
| `load.py` | Recreates and loads the pgvector `chunks` table from the embedding artifact. |
| `bench_provider.py` | Provider-only TTFT, usage, cache, context, and model comparison harness. |
| `bench_turn.py` | End-to-end sequential `/turn` SSE latency benchmark, with optional history priming. |
| `eval_retrieval.jsonl`, `eval_generated.jsonl`, `eval_handwritten.jsonl`, `eval_faq.jsonl` | Retrieval fixtures: historical, cleaned generated, handwritten, and FAQ-derived data. |
| `eval_calls.jsonl`, `handoff_phrases.jsonl`, `handoff_split*.json`, `handoff_probe.json` | Call-policy scenarios, handoff phrase bank, split definitions, and frozen serving artifact. |
| `rate_tables.jsonl`, `manifest.jsonl` | Load-bearing comparative-rate corpus and crawl provenance. |
| `PROJECT_SUMMARY.md`, `EXPLAINED.md`, `FUNCTIONS.md`, `PERFORMANCE.md` | Product summary, architectural explanation, function-level reference, and historical latency evidence. |
| `HANDOFF.md`, `HANDOFF_updated.md` | Historical project handoffs; useful for provenance, but superseded where current code and closeout evidence differ. |

# BoABot voice-pipeline feasibility analysis

Date of investigation: 2026-08-11

## Scope and decision criterion

This report evaluates `pipelineA`, `pipelineB`, `pipelineC`, and `Shared_RAG_Architecture` against BoABot's stated aim: a trustworthy Albanian banking/contact-center assistant whose authoritative decision engine is the guarded text `POST /turn` contract. The decisive question is not merely whether a provider can accept and emit audio. A feasible design must preserve the implemented order of policy, retrieval, evidence validation, generation, structured outcome, and handoff signaling.

Repository evidence was taken from the current code and artifacts. Provider capability statements are limited to official documentation checked on the date above. A provider listing Albanian as supported establishes API availability, not accuracy on Albanian banking terms, telephone audio, names, figures, dialects, or noisy calls.

## Implemented baseline and invariant

The current service is text-only:

1. `api.py`, `TurnReq` accepts `question` and optional `session_id`; `turn()` exposes only HTTP/SSE `POST /turn`, not an audio WebSocket.
2. `api.py`, `generate_turn()` gets server-owned session state and calls `callcenter.decide()` before retrieval or generation.
3. `callcenter.py`, `decide()` runs `trust.input_gate()`, repeat handling, credential-incident detection, unsupported business-deposit handling, PII redaction/handoff, clarification, and the frozen semantic handoff classifier.
4. If policy does not terminate the turn, `api.py`, `generate_turn()` may call `rag.rewrite()` for an elliptical follow-up, then calls `rag.retrieve_evidence()`.
5. `rag.py`, `retrieve_evidence()` calls `retrieve.retrieve()` and then `trust.trusted_hits()`. It refuses before generation when evidence is absent, below the `0.50` top-score threshold, or from the wrong family for an institutional price question.
6. `retrieve.py`, `retrieve()` performs BGE-M3 cosine search with `WHERE status = ANY(%s)` and the default `LIVE = ("canonical", "base")`. Amendments and superseded rows cannot enter the normal serving result.
7. Only vetted hits reach `rag.grounded_messages()` and `api.stream_answer()`. The current default is `google/gemini-3.1-flash-lite` through OpenRouter's text chat-completions API; this is not Gemini Live or native audio.
8. `/turn` streams `tool` and `token` events and ends with a `done` event containing exactly one of `answer`, `clarify`, `unsupported`, `handoff`, or `repeat`, plus session ID, source metadata, handoff state, PII-redaction state, and usage. Exceptions fail closed to `handoff` in `api.py`, `generate_turn()`.

The required voice invariant is therefore:

```text
caller audio -> final transcript -> /turn -> only /turn text is spoken
                                      |
                                      `-> done outcome drives handoff/call control
```

Interim ASR may support captions and endpointing, but it must not trigger an unguarded factual answer. The voice layer must not speak a model's parallel answer, raw retrieval chunks, the `tool` search query, or content created before `/turn` has admitted the turn.

## Pipeline A: Azure STT, guarded text RAG, Azure TTS

### 1. Component delta and reuse

What can be reused without changing its authority:

- `api.py`, `TurnReq`, `generate_turn()`, `turn_done()`, and `turn()` provide the complete text turn contract and structured outcome.
- `callcenter.py`, `decide()` and `SessionStore` provide current routing, PII/handoff policy, repeat/clarification behavior, and bounded session history.
- `rag.py`, `retrieve_evidence()` and `grounded_messages()` provide evidence gating and the grounded text request.
- `retrieve.py`, `model()`, `pool()`, and `retrieve()` provide BGE-M3 query embedding, connection pooling, and live-status-filtered vector retrieval.
- `trust.py`, `input_gate()` and `trusted_hits()` remain the load-bearing deterministic gates.

What does not exist and must be added outside the existing trust modules:

- Client or telephony audio capture and a binary WebSocket/WebRTC/media-stream endpoint. The present FastAPI application exposes text HTTP/SSE only.
- Audio authentication, connection lifecycle, backpressure, per-call/session mapping, and reconnection behavior.
- Audio codec negotiation and transcoding/resampling, including likely telephone narrowband audio versus the format required by the speech service.
- VAD/end-of-turn detection and a policy for when an interim Azure transcript becomes the final text submitted to `/turn`.
- Azure Speech SDK/service integration for continuous Albanian STT, credentials, regions, timeouts, partial/final result handling, and confidence/diagnostic capture.
- An SSE consumer that buffers arbitrary `token` deltas into speakable units and sends only answer/policy text to streaming Azure TTS.
- Azure TTS stream handling, speaker playback, buffer management, pronunciation/SSML decisions, and selection between the available Albanian voices.
- Barge-in: stop playback, clear queued synthesized audio, cancel/close the current text and TTS streams, and define whether an interrupted partial answer enters history. `api.py`, `stream_answer()` and `generate_turn()` do not expose explicit cancellation control.
- Telephony provider/call control, DTMF if required, agent queue/transfer, and confirmation that a human accepted the handoff. `callcenter.py` returns advisory flags only.
- Production session storage, authentication, audit logging, metrics, rate limiting, and multi-instance coordination, all already listed as remaining work in `README.md`.

The diagram's FTS, HNSW, metadata filtering, merge/rerank, and variable top-3-to-5 layer are not reusable because they are not implemented; the actual reusable retrieval is the exact top-five dense path described under `Shared_RAG_Architecture` below.

### 2. Trust-boundary integrity

The safe path is:

```text
audio -> VAD/final Azure transcript -> POST /turn
      -> callcenter.decide()
      -> optional rewrite
      -> retrieve_evidence()
      -> retrieve() with canonical/base filter
      -> trusted_hits()
      -> grounded text generation
      -> SSE token/done
      -> Azure TTS for /turn text only
      -> done.handoff drives transfer
```

This preserves the current boundary if the bridge submits the finalized transcript to `/turn` and treats `/turn` as the only answer source. Immediate `clarify`, `unsupported`, `handoff`, and `repeat` messages are already emitted as token plus done events, so the bridge can synthesize them through the same output path.

Bypass risks are integration errors, not requirements of the modular design:

- Calling `retrieve.retrieve()` directly would retain its status filter by default but skip `callcenter.decide()`, `input_gate()`, business-deposit policy, PII/handoff policy, and `trusted_hits()`.
- Calling a model directly with retrieved chunks would skip the structured outcomes and fail-closed exception behavior in `generate_turn()`.
- Speaking an Azure or LLM response created directly from the transcript, rather than `/turn`'s token stream, would create a second unauthoritative answer path.
- Letting the speech layer decide that a transcript is “safe enough” would be insufficient. ASR errors can remove PIN/OTP terms, bank names, numbers, or price intent and thereby change the downstream policy or evidence-family decision. The final transcript must still pass the text gates, and ASR quality must be evaluated specifically on safety-routing cases.
- The bridge must consume the final `done` event. Merely speaking the text without acting on `handoff: true` would preserve answer wording but fail the contact-center aim.

### 3. Albanian voice support

Official Azure documentation lists `sq-AL` for Speech-to-Text and describes real-time transcription of streaming audio. It also lists two standard Albanian neural TTS voices, `sq-AL-AnilaNeural` and `sq-AL-IlirNeural`: [Azure language and voice support](https://learn.microsoft.com/en-au/azure/ai-services/speech-service/language-support) and [Azure Speech-to-Text overview](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-to-text).

That makes the provider/API combination available in principle. Nothing in this repository instantiates Azure Speech, records WER, measures finalization latency, or evaluates those voices. Availability does not establish performance on Banka e Shqipërisë document names, Albanian bank names, percentages, currency amounts, dates, code-switching, accents, background noise, or 8 kHz telephony.

`eval_asr_noise.py` is not an audio benchmark. Its `strip_diacritics()`, `spell_digits()`, and `delete_stopword()` functions deterministically alter existing text and then call `retrieve()`. It measures retrieval robustness to three synthetic text perturbations; it does not run ASR, compute WER/entity error, compare providers, test telephone audio, or exercise end-to-end handoff.

### 4. Effort and risk

Effort is medium-high. The architecture is conventional and has a clean textual seam, but almost the entire real-time media plane is new.

- Streaming ASR does not make the existing RAG incremental. `/turn` accepts one complete text question, so VAD/endpointing and final-ASR delay occur before its measured latency. Sending unstable interim text would create duplicate or contradictory turns.
- `PERFORMANCE.md`, “Voice budget,” measured Gemini text first-token p50/p95 at 844/5,986 ms for empty-history turns and 789/1,562 ms for history turns. First-sentence p50/p95 was 1,062/6,992 ms and 1,049/2,001 ms respectively.
- The reported first-audio figures of 1,362/7,292 ms and 1,349/2,301 ms add an assumed 300 ms TTS first-byte delay. No Azure TTS call ran. Real VAD, ASR finalization, network transit, synthesis, jitter buffering, codec conversion, and telephony playback would add time not represented in those figures.
- The current synchronous `requests` stream has no explicit end-to-end latency cancellation, hedging, or provider fallback. Barge-in needs cancellation propagation so old tokens and audio do not continue after a new caller turn.
- Cost includes metered Azure STT, OpenRouter/Gemini text generation, Azure TTS, telephony minutes, and supporting infrastructure. The repository measures some LLM usage/cost in benchmark artifacts but contains no speech or telephony cost measurement.
- Operationally, one vendor supplies both speech ends, simplifying credentials and observability compared with Pipeline B, but the system still has three sequential remote stages: ASR, text LLM, and TTS.

A live proof of concept must measure end-of-speech-to-final-transcript, transcript-to-first `/turn` token, first-sentence-to-first-audio, end-to-end first audio, completion, cancellation, and transfer acceptance at p50/p95 under realistic calls.

### 5. Verdict

**Feasible with changes.** Pipeline A naturally preserves BoABot's trust boundary if its “RAG service” means the public `/turn` contract, not direct retrieval. The missing work is substantial but well-bounded: media transport, VAD, Azure STT/TTS, cancellation/barge-in, codecs, telephony, and operations. Albanian API availability is documented, while quality and latency remain unproven live. The diagram must be corrected to show the actual dense retrieval implementation unless hybrid retrieval is separately built and revalidated.

## Pipeline B: Google Chirp STT, guarded text RAG, Azure TTS

### 1. Component delta and reuse

Pipeline B reuses exactly the same authoritative components as Pipeline A: `api.py`, `generate_turn()` and `/turn`; `callcenter.py`, `decide()` and sessions; `rag.py`, `retrieve_evidence()` and grounded generation; `retrieve.py`, `retrieve()`; and `trust.py` gates.

It requires all of Pipeline A's missing media, VAD, audio-codec, TTS, barge-in, telephony, call-control, and production-operation work. It additionally requires:

- A provider-neutral streaming STT interface with common events such as partial transcript, final transcript, speech start/end, confidence/diagnostics, error, and cancellation.
- An Azure implementation and a Google Cloud Speech-to-Text V2/Chirp implementation behind that interface.
- Configuration that holds VAD, input recordings/codecs, `/turn`, Gemini text model, Azure TTS voice, prompts, corpus version, and evaluation cases constant while changing only the STT provider.
- Normalized timing and transcript logging so the benchmark does not confuse provider endpointing settings, regions, punctuation, or transport with recognition quality.
- Google Cloud credentials, regional configuration, quotas, billing, monitoring, and cross-cloud failure handling in addition to Azure and OpenRouter.

No STT abstraction exists in the repository today. The diagram's abstraction is a sound experimental requirement, not shipped code.

### 2. Trust-boundary integrity

The safe trace is identical to Pipeline A after ASR:

```text
audio -> common VAD -> final Chirp transcript -> POST /turn
      -> decide() -> retrieve_evidence() -> live-filtered retrieve()
      -> trusted_hits() -> grounded generation -> SSE/done
      -> the same Azure TTS -> call-control action
```

This preserves the boundary if both STT adapters emit only a transcript and neither adapter can call retrieval, the LLM, or TTS directly. The abstraction improves trust testing because identical audio, turn boundaries, and downstream components make differences in PII/handoff routing and evidence selection attributable to the transcript.

The same bypasses remain possible if an adapter calls `retrieve()` directly, bypasses `/turn`, or emits a provider answer. A benchmark that compares Azure's full bot with Google's full bot would not isolate STT and would not validate Pipeline B's engineering claim. Final transcripts from each provider must be fed to fresh but equivalently primed `/turn` sessions, and the resulting structured outcomes, hit IDs, and spoken text must be compared.

### 3. Albanian voice support

Google's official Chirp 3 documentation says `Speech.StreamingRecognize` is supported and lists Albanian `sq-AL` as **Preview**, not GA: [Chirp 3 model and language availability](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3). The current V2 language table also lists Albanian for Chirp-family models in European locations: [Cloud Speech-to-Text V2 supported languages](https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-supported-languages).

The exact model matters. “Google Chirp” is not a sufficient production configuration; the proof of concept should pin the API version, `chirp_3` model ID, region, endpointing parameters, punctuation/adaptation options, and launch status. Preview status increases change, support, and production-approval risk even though streaming `sq-AL` is documented.

The Azure TTS half has the same documented Albanian voices and the same unvalidated quality concerns as Pipeline A. The repository contains no Google STT call and no real Azure-versus-Google Albanian transcript corpus. Again, `eval_asr_noise.py` is synthetic text perturbation only.

### 4. Effort and risk

Effort is high relative to A because the team must implement and operate two STT adapters to obtain the promised controlled comparison, while production will eventually retain only one or preserve two for fallback.

- Latency has the same sequential shape and the same unmeasured VAD/ASR/TTS additions as Pipeline A. Chirp's streaming support does not remove the need for a final text turn before `/turn`.
- The abstraction must not erase provider-specific diagnostics needed to understand failures, but it must expose a provider-neutral core sufficient for a fair benchmark.
- Cross-cloud Azure TTS plus Google STT adds credentials, regional placement, quotas, observability, incident surfaces, and potentially extra network transit.
- Cost includes Google STT, OpenRouter/Gemini text generation, Azure TTS, telephony, and infrastructure; there is no in-repo measurement. The experiment should report cost per completed and per handed-off minute, not only list prices.
- Preview `sq-AL` in Chirp 3 is a material operational risk. It does not prove poor quality, but it prevents treating the diagram as production-ready without provider and compliance review.
- The main benefit is empirical: the same recorded Albanian calls can settle Azure-versus-Chirp WER, bank/entity/number accuracy, endpointing delay, and downstream outcome preservation without changing the bot.

### 5. Verdict

**Feasible with changes.** Pipeline B can preserve every BoABot gate and is the best design for a fair STT bake-off. It is not currently implemented, costs more engineering/operations than A, and its named Chirp 3 Albanian support is Preview. It is most valuable as a benchmark harness or qualified alternative, not an evidence-free production choice.

## Pipeline C: Gemini Live native voice with `search_knowledge_base()`

### 1. Component delta and reuse

As drawn, Pipeline C reuses only the corpus embedding/database and some retrieval logic. It does not reuse the authoritative `/turn` orchestration or its text model answer as-is, because Gemini Live hears the caller, decides whether factual retrieval is needed, invokes a raw knowledge tool, and composes its own spoken answer.

New components would include:

- Gemini Live bidirectional audio session management, authentication, audio formats, client/server transport, and session resumption.
- Live input transcription capture, automatic or custom VAD, turn-complete handling, interruption events, playback buffers, and reconnection.
- A function declaration and server-side handler for `search_knowledge_base()` or, in the safe redesign, a `process_turn()` tool that invokes `POST /turn`.
- A bridge between the Live session and `/turn` SSE, including session-ID mapping and structured outcome handling.
- Enforcement that no Live-generated factual or policy audio reaches the caller outside the guarded `/turn` result.
- Telephony codecs/call control, agent transfer, logging, and all production controls absent from the repo.

Official Gemini documentation confirms that Live can expose input/output audio transcription, VAD/interruption events, and function calling. For Gemini 3.1 Flash Live Preview, function calling is sequential: the model waits for the tool response before responding. Those capabilities help implement a prototype, but neither the integration nor enforcement exists in this repository: [Gemini Live capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities).

### 2. Trust-boundary integrity

The diagram's path is not equivalent to `/turn`:

```text
audio -> Gemini decides whether retrieval is needed
      -> search_knowledge_base() -> chunks
      -> Gemini composes and speaks answer
```

It creates several direct bypasses:

- Gemini receives and reasons over caller input before `callcenter.decide()`. It can answer a repeat, short/ambiguous request, PII-bearing request, credential incident, unsupported business-deposit question, or account-specific request without the deterministic policy ever seeing it.
- The model decides whether to call the tool. A factual banking question it answers from model memory never reaches retrieval or `trusted_hits()`.
- If `search_knowledge_base()` calls `retrieve.retrieve()`, the default canonical/base status filter is retained, but the tool still skips `input_gate()`, `callcenter.decide()`, `is_business_deposit_question()`, and `trusted_hits()`.
- If the tool calls `rag.retrieve_evidence()`, relevance and rate-family gates are restored, but repeat/clarification, PII and sensitive-intent handoff, session policy, structured outcomes, and fail-closed `/turn` orchestration are still missing.
- Raw chunks are not an approved answer. `trusted_hits()` approves whether evidence may reach the grounded text model; it does not guarantee that a different Live model will quote the right bank, label, or number. Pipeline C also loses `rag.SYSTEM`, the exact grounded prompt order, `api.py` error-to-handoff behavior, and `/turn` source/outcome semantics unless explicitly rebuilt.
- A prompt telling Live to “always call the tool” is model behavior, not a deterministic application gate. It cannot be the sole enforcement mechanism for a banking trust boundary.

To conform to BoABot, the tool must not be a raw `search_knowledge_base()` returning chunks. It must submit the finalized input transcript to `/turn` and return `/turn`'s token stream plus final outcome. The Live model's own answer must be suppressed; the caller may hear only the answer/policy text authorized by `/turn`, and `handoff`/PII flags must drive call control.

Even that redesign needs application enforcement. The bridge should mute/drop any native response to caller audio until a matching guarded turn is complete, reject audio associated with absent/stale tool IDs, cancel queued playback on interruption, and prevent Live from paraphrasing or adding facts around the `/turn` result. If native speech generation cannot render the approved text without semantic additions or numeric changes, use a conventional TTS engine instead. Tool-result text alone is trusted only when it is `/turn`'s final response; raw hits alone are not.

### 3. Albanian voice support

Current official Gemini Live documentation lists Albanian with code `sq` among supported languages, provides `input_audio_transcription`, supports native audio output, and documents automatic/custom VAD and interruption events. The same page labels the Live API and current Gemini 3.1 Flash Live model as Preview: [Gemini Live capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities).

This is stronger availability evidence than the repository had when `VOICE_TTS_ANSWER.md` was written, but it is not BoABot-quality evidence. The repo's text-only Gemini benchmarks show usable Albanian prose, not Live ASR WER, native-voice pronunciation, exact reading of banking figures, telephone-bandwidth behavior, or tool-use reliability. `eval_asr_noise.py` does not exercise Gemini Live.

The Live API's language code is `sq`, while the modular providers use locale `sq-AL`; that difference should not be treated as a quality conclusion. A real benchmark must use the same Albanian audio and score transcript, entity, numeric, routing, and spoken-output fidelity.

### 4. Effort and risk

The apparent component count is smaller because one service supplies VAD, speech understanding, and speech generation, but trust enforcement makes the engineering risk high.

- Built-in VAD and interruption events reduce low-level work, but the application still has to stop playback and clear queued audio; the official docs explicitly assign that client responsibility.
- The as-drawn design could make a single Live model/tool turn, but it is unacceptable for the aim. The safe redesign uses Live for audio plus the existing `/turn` text-generation call, so a user turn can incur both Live-model and OpenRouter/Gemini-model work. Cost and latency cannot be inferred from the simpler diagram.
- `PERFORMANCE.md` does not benchmark Gemini Live. Its Gemini numbers are for OpenRouter text chat completions plus a modeled 300 ms TTS delay. They neither predict native Live first audio nor prove that a Live tool round trip through `/turn` meets p95 goals.
- Sequential function calling can prevent the Live model from responding before a tool result once it chooses the tool, but it does not by itself guarantee that every relevant turn invokes the tool or that output is verbatim. Application-side output gating remains necessary.
- Preview model/API lifecycle, session limits/resumption, regional/privacy requirements, audio retention policy, and provider changes are operational and compliance risks that require current review.
- Barge-in introduces two states: Live's conversation state and BoABot's server-owned `SessionStore`. The bridge must define whether canceled partial `/turn` answers are recorded, avoid repeating unheard content, and keep the correct `session_id` across reconnects.
- Telephony and actual agent transfer remain separate work; native audio does not supply a call-center queue.

### 5. Verdict

**Not feasible for the stated aim as drawn.** A Gemini Live model that conditionally calls `search_knowledge_base()`, receives chunks, and composes its own answer replaces the guarded decision engine and can bypass every policy except whichever parts the tool happens to call.

**Feasible with changes as a constrained bridge.** A modified Pipeline C aligns with the README only if Live supplies audio input/transcription and audio rendering around `/turn`, the application calls `/turn` for every finalized caller turn, independent Live answers are suppressed, raw chunks are never exposed as sufficient authorization, and only `/turn`'s response/outcome controls speech and handoff. Whether Live can be constrained and can render exact Albanian banking content is an open proof-of-concept question.

## Shared_RAG_Architecture

### What is implemented

The repository implements a substantial dense-RAG subset:

- `boa_scraper_v2 (2).ipynb` contains the exploratory Bank of Albania crawl, PDF extraction, rate-table extraction, article/window chunking, and status heuristics. Artifacts include `manifest.jsonl`, `pdf_text.jsonl`, `chunks.jsonl`, and `rate_tables.jsonl`.
- `boa_embed.ipynb` loads regulation/rate chunks, assigns IDs, embeds text with normalized `BAAI/bge-m3`, and writes `embedded.parquet`.
- `load.py` creates one PostgreSQL table named `chunks` with document metadata, text, status, and a 1,024-dimensional vector, then inserts the Parquet rows.
- Read-only inspection with `inspect_parquet.py` reports 4,168 rows and eight columns (`id`, `doc`, `article`, `status`, `section`, `url`, `text`, `embedding`), with 3,804 base, 189 canonical, 107 amendment, and 68 superseded rows.
- `retrieve.py`, `retrieve()` embeds the query with the same model, performs top-k cosine search, returns text and citation fields, and filters to `LIVE` statuses by default.
- Production uses `k=5` through `rag.retrieve_evidence()`. `api.py` reuses the `callcenter.decide()` embedding only for byte-identical query text and re-embeds rewritten queries.

### Concrete differences from the diagram

| Diagram claim | Shipped repository reality | Consequence |
|---|---|---|
| PostgreSQL `documents`, `document_chunks`, `embeddings`, and metadata structures | `load.py` creates one denormalized `chunks` table | Not a voice blocker, but the diagram is not an accurate schema or provenance model. |
| HNSW vector search | `load.py` creates only a B-tree index on `status`; `retrieve()` orders by `<=>` with no HNSW/IVFFlat index | Current 4,168-row exact scan is simple and avoids approximate-recall loss. Revisit only with scale/latency evidence. |
| PostgreSQL FTS keyword candidates | No `tsvector`, `tsquery`, FTS index, or FTS SQL exists in the serving path | Exact terms, numbers, and names receive no separate lexical retrieval channel. This may matter more with ASR errors but is not proven by current tests. |
| Merge/RRF | No candidate merge or reciprocal-rank-fusion implementation exists | Every diagram overstates query-time sophistication. |
| Optional reranker | No cross-encoder or other reranker is present | Top five are raw dense-cosine order. |
| JSONB metadata filtering | No JSONB column or JSONB predicate exists; only the scalar `status = ANY(...)` filter is applied | The load-bearing live-status filter exists, but arbitrary metadata filtering does not. |
| Top 3-5 selection | Production defaults to fixed `k=5`; experiments measured other k values but did not ship them | Diagrams should say top five unless a policy is implemented. |
| Generic PDF/HTML/FAQ/Docs ingestion | The notebooks/artifacts cover Bank of Albania PDFs and selected rate-table web pages. Crawled HTML is mostly not chunked; `eval.py`, `main()` explicitly skips FAQ evaluation because those pages are not indexed. Six scanned PDFs lack OCR. | Corpus coverage, not voice transport, remains a trust limitation and can cause correct `unsupported` outcomes. |
| Reproducible production ingestion service | The pipeline is notebook/artifact driven, includes heuristic status labeling, and `load.py` drops/recreates the table | Adequate for the present controlled corpus, but freshness, legal status review, provenance, migrations, and automated repeatability need production work. |
| Query normalization | There is conditional LLM rewrite for elliptical history in `rag.needs_rewrite()`/`rewrite()`, but no general normalization stage before dense retrieval | The diagram's label should not be read as a shipped standalone component. |

There is no FTS, merge/RRF, or reranker elsewhere in active Python serving code. `EXPLAINED.md` also explicitly states that dense semantic search is used instead of PostgreSQL full-text hybrid search because there is no Albanian PostgreSQL stemmer. Historical `.orig` files and prose do not change the active runtime.

### Do the gaps matter to the three voice pipelines?

They do not prevent A, B, or a constrained C bridge from calling `/turn`; all can reuse the current dense implementation. Voice feasibility should not be made conditional on building hybrid retrieval.

They do matter to accuracy claims. Audio transcription can corrupt bank names, numeric terms, and inflected words. A lexical channel might help some exact-term cases and hurt others, especially without Albanian stemming. That must be tested, not assumed. Any hybrid change also affects the trust boundary: `trust.trusted_hits()` currently interprets the first result's `score` as cosine similarity and enforces `MIN_RELEVANCE_SCORE = 0.50`. An RRF or reranker score is not automatically comparable. Hybrid retrieval would require preservation of the canonical/base filter in every candidate branch, a defined/fused score contract, gate recalibration, and regression tests for rate-family and weak-evidence refusals before deployment.

## Comparison table

| Dimension | Pipeline A | Pipeline B | Pipeline C as drawn | Constrained Live bridge |
|---|---|---|---|---|
| Primary speech path | Azure STT + Azure TTS | Chirp STT + Azure TTS | Gemini Live native audio | Gemini Live audio around `/turn` |
| Reuses `/turn` as authority | Yes, if integrated at transcript/SSE seam | Yes, if both adapters use the same seam | No | Yes, by mandatory application orchestration |
| Preserves `decide()` PII/handoff | Yes | Yes | No | Yes |
| Preserves `trusted_hits()` | Yes | Yes | Not with raw search tool | Yes |
| Preserves canonical/base filter | Yes through `/turn` | Yes through `/turn` | Only if tool uses `retrieve()` correctly | Yes through `/turn` |
| Independent answer risk | Low and avoidable | Low and avoidable | Fundamental to diagram | High unless output is application-gated |
| Albanian API availability | Azure documents `sq-AL` STT and two TTS voices | Chirp 3 documents streaming `sq-AL` in Preview; Azure TTS documents `sq-AL` | Live documents Albanian `sq` in Preview | Same Live availability; exactness unproven |
| Missing engineering | Full media plane, VAD, STT/TTS, cancellation, telephony | A plus dual-STT abstraction and cross-cloud operations | Live/tool integration, enforcement, telephony | Live bridge, transcript/SSE mapping, strict output gating, telephony |
| Comparative-test value | Baseline | Highest: changes only STT | Low because bot itself changes | Useful against modular baseline if `/turn` stays fixed |
| Cost shape | Azure STT + text LLM + Azure TTS + telephony | Google STT + text LLM + Azure TTS + telephony | Live + telephony, but unsafe shortcut | Live + existing text LLM + telephony; possible double model work |
| Main risk | Real Albanian quality and unmeasured end-to-end tail | Same plus Preview Chirp and cross-cloud complexity | Trust-boundary bypass | Enforcing no independent/paraphrased Live answer |
| Verdict | Feasible with changes | Feasible with changes | Not feasible for stated aim | Feasible with changes if proven |

## RECOMMENDATION

Use a constrained Gemini Live/WebSocket bridge as the first proof of concept because it matches `README.md`, “CURRENT STATE / REMAINING WORK,” but do not implement `pipelineC`'s raw `search_knowledge_base()` architecture. Preserve the contract in application code:

1. Stream caller audio to a server-controlled Live session with input transcription enabled. Use explicit/final turn boundaries and retain raw test audio only under an approved privacy policy.
2. For every finalized caller turn, send the transcript to `POST /turn` with the mapped BoABot session ID. Do not let Live decide whether the turn needs retrieval or policy.
3. Suppress all Live-generated answer audio associated with the caller input. Render only `/turn`'s token/policy response, preferably after complete sentence boundaries, and prevent additions or paraphrases. If exact rendering cannot be enforced and verified, use Azure TTS as in Pipeline A.
4. On the `done` event, preserve the returned outcome and source metadata in logs and route `handoff: true` to actual call transfer/queue control. Treat transfer acceptance, failure, and fallback as observable outcomes.
5. Make interruption cancel `/turn` consumption and output playback, clear buffers, and reconcile BoABot and Live session state. Never play stale audio after a new turn starts.

Build the modular STT interface from Pipeline B as the evaluation harness, even if it is not the first production implementation. Run Azure STT and pinned Chirp 3 on the exact same recorded call set, VAD boundaries, `/turn` code/corpus/model, Azure TTS settings, and fresh equivalent sessions. This produces a defensible fallback decision if Live transcription or contract enforcement fails.

The proof of concept should settle these open questions:

- Albanian ASR: WER plus exact bank-name, product, currency, percentage, date, and digit accuracy on clean, noisy, accented, code-switched, and telephone-bandwidth audio.
- Safety preservation: outcome agreement with reference transcripts for PII, PIN/CVV/OTP incidents, account-specific requests, repeats, short clarifications, unsupported business deposits, and handoff phrases.
- Grounding preservation: hit IDs, live statuses, top score, rate-family decisions, refusal rate, numeric/citation correctness, and no raw-chunk or model-memory answer path.
- Speech fidelity: human review plus automated comparison that spoken/output-transcribed Albanian contains every approved number and bank/product label and introduces no new factual content.
- Turn behavior: false endpoint rate, time to final transcript, double-talk, background speech, barge-in cancellation, stale-buffer leakage, recovery after reconnect, and correct repeat behavior after an interrupted answer.
- Latency: caller end-of-speech to final transcript, `/turn` first SSE/token/sentence/done, first audio, and completion at p50/p95/p99. Compare actual Live and modular measurements; do not reuse the assumed 300 ms TTS value as evidence.
- Operations: concurrent calls, quota/rate-limit behavior, regional failure, session resumption, provider timeout/fallback, privacy/retention, auditability, per-completed-minute cost, and human-transfer acceptance.

Promotion criteria should require zero observed trust-path bypasses in adversarial tests, predefined safety/outcome agreement, acceptable entity/number accuracy, a reviewed p95 latency target, and a working human-handoff path. If the constrained Live prototype cannot guarantee those properties, select Pipeline A with the better of Azure or Chirp from the controlled Pipeline B benchmark. Do not add FTS/RRF/reranking merely to match the diagrams; treat it as a separate retrieval experiment with gate recalibration and regression evidence.

## FINAL SUMMARY

- Pipeline A is feasible with changes: it cleanly wraps the guarded `/turn` contract, but VAD, streaming Azure STT/TTS, audio transport/codecs, cancellation, telephony, and live Albanian validation are all absent.
- Pipeline B is feasible with changes and is the strongest controlled STT comparison design; its abstraction is not implemented, cross-cloud operations add cost/complexity, and current official Chirp 3 `sq-AL` support is Preview.
- Pipeline C as drawn is not feasible for BoABot's stated aim because Gemini Live decides whether to retrieve and composes its own answer, bypassing `callcenter.decide()`, `trust.trusted_hits()`, and the authoritative structured outcome.
- A constrained Gemini Live bridge is feasible with changes only if every finalized transcript goes through `/turn`, independent Live answers are suppressed, and only `/turn` text and handoff flags control caller output and call routing.
- Azure documents Albanian `sq-AL` STT and TTS voices, Chirp 3 documents streaming `sq-AL` in Preview, and Gemini Live documents Albanian `sq` in Preview; none of those listings proves BoABot-quality Albanian calls.
- `eval_asr_noise.py` is a deterministic text-perturbation retrieval test, not an audio/ASR benchmark and not evidence comparing Azure, Chirp, or Gemini Live.
- `Shared_RAG_Architecture` is only partially implemented: the repo has BGE-M3 plus exact pgvector cosine search and canonical/base filtering, but no HNSW, PostgreSQL FTS, JSONB filtering, merge/RRF, or reranker.
- The missing hybrid retrieval features do not block voice integration; adding them would require preserving live-status filtering and recalibrating `trusted_hits()` because its `0.50` threshold assumes cosine scores.
- Live proof must establish Albanian entity/number accuracy, safety-outcome preservation, exact spoken rendering, barge-in/cancellation, true p50/p95 end-to-end latency, cost, concurrency, and accepted human transfer before any production choice.

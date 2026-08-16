# Production Voice Pipeline Schemas for BoABot

Date: 2026-08-11

## Scope, evidence discipline, and non-negotiable invariant

This document defines two production voice schemas. They are the two viable families established by `PIPELINE_FEASIBILITY.md`: a guarded modular speech pipeline and a constrained Gemini Live bridge. A third hybrid is not presented as a separate schema because Live transcription with Azure rendering is already a fallback mode of Schema 2 and still has the same authoritative path.

The designs preserve the implemented decision order in `api.py`, `generate_turn()`: server-owned session lookup, `callcenter.decide()`, optional `rag.rewrite()`, `rag.retrieve_evidence()`, `retrieve.retrieve()` with live-status filtering, `trust.trusted_hits()`, grounded generation, and one structured `done` outcome. The invariant is:

```text
caller audio -> accepted final transcript -> POST /turn -> approved sentence(s)
                                             |                  |
                                             |                  `-> speech renderer
                                             `-> done outcome -> call control/handoff
```

Only text emitted and authorized by `/turn` may become answer audio. ASR interim text, speculative retrieval, the SSE `tool.query`, raw retrieved chunks, provider-native answers, local bridge prose, and stale output are never speakable.

Evidence labels used in every latency table are:

- **[R] Measured in repository:** a value reproduced by the saved BoABot benchmarks.
- **[M] Modeled/assumed:** an engineering model, not a provider observation.
- **[P] Must be measured in the PoC:** a qualification target or unknown live value.

The existing first-sentence measurements start when `/turn` is submitted. They exclude VAD, ASR finalization, speech-provider transport, actual TTS, codec conversion, jitter buffering, and telephony playback. Adding p50 or p95 stage values is a budget allocation, not a statistically valid prediction of the end-to-end percentile. No schema can claim the voice SLO until the complete correlated distribution is measured.

## Implementation status (2026-08-14)

The production schemas and their **[M]**/**[P]** qualification work below remain
in scope, but the current working tree now contains one real local implementation
of each guarded family. These are browser-microphone development harnesses, not
telephony or production media gateways:

- **[R] Arm A implements the Schema 1 guarded modular path.**
  `voice.web_app:app` on loopback port `8100` accepts one browser-recorded 16 kHz,
  16-bit, mono PCM WAV and calls the same real `voice.cli.live_run.run_single()`
  cascade as the CLI: Azure `sq-AL` ASR -> guarded `POST /turn` -> Azure TTS.
  `api.py` buffers model tokens into complete sentences and runs the fail-closed
  `FidelityGuard` before emitting `approved_sentence`; only those server-approved
  sentences enter correlated TTS. The web response is returned after the turn
  completes and contains public source metadata, never raw evidence passages.
- **[R] Arm B implements the Schema 2 constrained Gemini Live bridge.**
  `voice.web_app_b:app` on loopback port `8200` drives `LiveTurnBridge`: one Live
  session transcribes the caller, every finalized transcript is sent to `/turn`,
  and every native answer from that input session is counted in
  `native_response_dropped_events`/`native_response_dropped_bytes` and discarded.
  A separate constrained Live session receives only the complete approved
  `/turn` text; it receives no raw hits or passages. Native-response dropping,
  the restricted render context, and correlated output gating enforce the answer
  source boundary; approved-versus-spoken transcript fields expose renderer
  fidelity for audit. Handoff/unsupported outcomes emit no answer audio.
- **[R] The Azure confidence probe found no usable per-word confidence signal.**
  `voice.cli.probe_confidence` and commit `b312e82` recorded the constant value
  `0.78952557` across clean, noisy, silence, and degraded inputs. The critical-span
  threshold is therefore skippable only through the explicit
  `VOICE_CONFIDENCE_CRITICAL_DISABLED=1` opt-in, and every bypassed turn records
  `critical-span gate bypassed: provider confidence proven constant`. Without the
  opt-in, a bank/number/currency/percent span lacking confidence safely clarifies;
  the `0.75`/`0.85`/`0.55` thresholds remain unchanged.
- **[R] Sentence fidelity is now enforced at the `/turn` boundary.**
  `voice.sentence_buffer` preserves decimal fragments split across provider
  tokens, and `voice.fidelity_guard` can only suppress output; it cannot approve
  evidence or alter `trusted_hits()`. Its locale-aware number parser treats
  Albanian dot-thousands forms such as `10.000` and `1.000.000` without changing
  ordinary provider decimals such as `4.75`. Entity comparison folds only known
  Albanian head forms such as `bankës`/`bankën` -> `banka`, while
  `ENTITY_HEAD_FORMS`, `LABEL_TOKEN_FORMS`, and `LABEL_STOPWORDS` retain
  distinguishing bank/product labels. Commit `5883e44` and the current
  `test_fidelity_label_subset.py`, `test_entity_inflection.py`,
  `test_claim_rows.py`, and `test_sentence_fidelity.py` cover the wrong-label and
  inflection regressions.

These **[R]** entries establish implemented local control flow, not a measured
production voice SLO. Both web servers are single-turn, loopback-only,
unauthenticated, and return answer audio only after the request completes;
telephony codecs, streaming browser playback, production authentication, and
real call transfer remain **[P]** work. The component maps below preserve the
production design baseline as written on 2026-08-11; where they call an Arm A/B
component "genuinely new" or say that no Live call exists, this dated status
section supersedes that implementation claim, while their production-gap and
latency-evidence classifications remain unchanged.

## Schema 1 - Guarded modular near-real-time

### 1. Purpose and when it wins

This schema is the production-control baseline and the fallback if the constrained Live prototype fails. It wins when exact bank names and figures, auditable stage boundaries, provider substitution, and deterministic output rendering matter more than minimizing the number of vendors.

Use it for:

- the controlled Azure Speech versus Google Chirp 3 Albanian STT bake-off;
- the first telephony integration whose behavior must be easy to inspect;
- the production fallback for Gemini Live Preview instability or output leakage;
- calls containing rates, percentages, currencies, dates, account-safety language, or easily confused bank names;
- a stable eval harness in which only the STT adapter changes.

Azure streaming `sq-AL` STT is the primary qualification candidate. A pinned Google Cloud Speech-to-Text V2 Chirp 3 `sq-AL` adapter is the benchmark alternative behind the same interface; `PIPELINE_FEASIBILITY.md` records Chirp 3 Albanian as Preview. Azure streaming TTS is used for both, with one qualified Albanian voice per deployment so the STT comparison does not change the renderer.

### 2. Architecture diagram and flow

```text
PSTN/SIP/WebRTC client
  |  8 kHz G.711 or negotiated WebRTC audio, authenticated call_id
  v
Media gateway / codec normalizer ---------------------------> call-control adapter
  |  16 kHz PCM frames, sequence numbers, timestamps                |
  v                                                               transfer/queue
Server VAD + endpoint controller <----- barge-in detector           |
  | partial/final events                                            |
  v                                                                 |
Neutral StreamingASR interface                                      |
  |-- Azure sq-AL adapter (primary)                                 |
  `-- Chirp 3 sq-AL adapter (bake-off/qualified fallback)           |
  | accepted final transcript + confidence + alternatives           |
  v                                                                 |
Transcript acceptance gate inside /turn orchestration               |
  | every accepted/finalized turn                                   |
  v                                                                 |
POST /turn (question, session_id, turn_id, ASR diagnostics)          |
  |                                                                 |
  +-> callcenter.decide()                                           |
  |     `-> input, repeat, secret, business, PII, clarify, handoff   |
  +-> optional rag.needs_rewrite()/rewrite()                        |
  +-> rag.retrieve_evidence()                                       |
  |     `-> retrieve.retrieve(k=5, status=canonical/base)            |
  |           `-> trust.trusted_hits(score >= 0.50, rate family)     |
  +-> rag.grounded_messages() -> api.stream_answer()                 |
  |                                                                 |
  `-> SSE tool | token | done                                       |
       |       |       `-> outcome/sources/handoff/PII -> audit -----+
       |       `-> sentence buffer -> entity/figure verifier
       |                                |
       |                                v
       |                         SSML canonicalizer
       |                                |
       |                                v
       |                         preconnected Azure TTS
       |                                |
       |                         audio jitter/codec buffer
       |                                |
       |                                v
       |                         media gateway -> caller
       `-> telemetry only; tool.query is never spoken

Read-only speculative warm path, never an answer path:
ASR partial -> isolated retrieve-only warmer -> discard all hits/results
```

The unavoidable critical path is final ASR acceptance, then `/turn` through its first complete authorized sentence, then TTS first byte. Audio ingestion, provider connections, retrieval/model warmup, TTS connection establishment, and telephony codec setup happen before the caller finishes speaking. Sentence verification starts as soon as a punctuation boundary arrives. TTS starts on the first verified complete sentence while later `/turn` tokens are still arriving.

The speculative partial-transcript path may warm an embedding worker, connection pool, or read-only retrieval pages. Its result is tagged `speculative=true`, has no session write capability, is discarded at finalization, and cannot be supplied to generation. The authoritative `/turn` performs retrieval again. `rag.retrieve_evidence()`'s byte-identity assertion means an embedding from a different partial transcript must not be reused for the final text.

### 3. Components and reuse map

| New component | Responsibility | Repository reuse or new work |
|---|---|---|
| Media gateway | Authenticate a call, negotiate WebRTC/SIP/telephony transport, normalize G.711/Opus/PCM, maintain sequence numbers and backpressure. | Genuinely new; no audio endpoint exists in `api.py`. |
| VAD/endpoint controller | Emit speech-start/end, finalize one caller turn, reject background-only segments, and trigger cancellation on barge-in. | Genuinely new. It must not decide answer safety. |
| `StreamingASR` interface | Normalize `partial`, `final`, `confidence`, alternatives, timing, error, and cancel events while retaining provider diagnostics. | Genuinely new; Pipeline B's abstraction is not shipped. |
| Azure `sq-AL` adapter | Primary continuous recognition with phrase/entity adaptation and final transcript timing. | Genuinely new. Availability is documented in `PIPELINE_FEASIBILITY.md`; quality is unmeasured. |
| Chirp 3 `sq-AL` adapter | Pinned V2 model/region alternative under identical endpointing and audio fixtures. | Genuinely new; Albanian is Preview per the feasibility report. |
| Transcript acceptance gate | Apply calibrated utterance and critical-entity confidence policy before retrieval; return a structured `/turn` clarify/handoff rather than bridge-authored speech. | New, but placed inside the authoritative `api.py`, `generate_turn()`/`callcenter.decide()` path. It is stricter than current policy, never a bypass. |
| Voice turn orchestrator | Correlate `call_id`, opaque `session_id`, monotonic `turn_id`, SSE stream, cancel token, provider requests, and `done`. | New; reuses `api.py`, `TurnReq`, `turn()`, and `turn_done()` as the contract. |
| Guarded text engine | Own policy, retrieval, evidence admission, generation, failure outcome, and history. | Reuse `api.generate_turn()`, `callcenter.decide()`/`SessionStore`, `rag.retrieve_evidence()`/`grounded_messages()`, `retrieve.retrieve()`, and `trust` gates as authority. |
| Sentence buffer | Convert arbitrary SSE token deltas into punctuation-terminated units and retain the remainder until complete/done. Never speak `tool` events. | New consumer; mirrors the boundary measured by `api.py`, `_SENTENCE_END_RE`, and `bench_turn.py`. |
| Answer fidelity guard | Before a sentence is renderable, extract bank/product/document names, numbers, percentages, dates, and currencies; compare them with the vetted hit text and cited metadata. Suppress and hand off on mismatch. | New server-side output restriction with access to the already-vetted `hits`; extends the narrower checks in `phase3_quality.py`. It cannot admit evidence rejected by `trusted_hits()`. |
| SSML canonicalizer | Preserve the approved display text, generate deterministic Albanian readings for decimal separators, percent, currency, dates, acronyms, and bank-name pronunciation lexicons. | Genuinely new. Input is only fidelity-verified `/turn` text. |
| Azure TTS pool | Keep region/voice connections warm, stream SSML, expose first-byte timing, and cancel by `turn_id`. | Genuinely new. The repository's 300 ms value is only an assumption. |
| Barge-in coordinator | On new speech, stop media playback, clear jitter/TTS/sentence queues, close the model HTTP response, cancel ASR/TTS work, and reject late frames by generation ID. | Genuinely new; current synchronous `requests` stream has no explicit cancellation API. |
| Call-control adapter | Act on `done.handoff`, enqueue/transfer, observe agent acceptance, and fail closed if transfer fails. | Genuinely new. `callcenter.py` currently returns advisory flags only. |
| Session/audit/metrics plane | Redis-backed session mapping, ASR and SSE correlation, safe transcript policy, latency histograms, cost, outcome, transfer, and cancellation metrics. | New production replacement/extension for process-local `callcenter.SessionStore`; aligns with `README.md` remaining work. |

The proposed ASR diagnostic fields are an additive, authenticated extension of `TurnReq`; they do not allow the bridge to select an answer outcome. `generate_turn()` remains the only component that emits caller-facing text and the final outcome.

### 4. Latency budget

#### Evidence baseline

For the current Gemini default, `PERFORMANCE.md`, Phase 3 reports `/turn` first-token p50/p95 of 844/5,986 ms with empty history and 789/1,562 ms with history. First-sentence p50/p95 is 1,062/6,992 ms empty and 1,049/2,001 ms with history. The document's first-audio values add an assumed 300 ms and did not run TTS. `README.md` further reports 0/30 Gemini turns with cached tokens, so no Gemini cache saving is budgeted.

#### Established-call target envelope

Origin is caller end-of-speech; destination is the first audible frame. These are qualification caps, not achieved measurements.

| Sequential stage | Target p50 ms | Target p95 ms | Evidence status | Compression mechanism |
|---|---:|---:|---|---|
| VAD end decision plus final ASR event | 80 | 120 | **[P]** aggressive PoC qualification target | Audio and ASR already stream while caller talks; tune endpointing on held-out calls, not on test transcripts. |
| Final transcript dispatch and `/turn` network admission | 10 | 15 | **[P]** must measure | Co-locate bridge and API, reuse HTTP connections, no extra model/tool decision. |
| `/turn` start to first complete sentence, established/history mode | 1,049 | 2,001 | **[R]** N=100 Phase 3 Gemini history | Keep process/model/pgvector warm and preserve session ID; do not attribute the result to cache because current Gemini measured 0/30 cache hits. |
| Preconnected TTS to first audio byte after sentence | 300 | 300 | **[M]** repository assumption converted into a design cap; **[P]** actual | Reused synthesizer connection, fixed voice/region, sentence-aligned SSML. |
| Codec, jitter buffer, and first-frame egress | 40 | 60 | **[P]** must measure | Pre-negotiate codec and use a deliberately small bounded first-chunk buffer. |
| **Budget sum** | **1,479** | **2,496** | **[M/P]** conditional envelope | Meets approximately 1.5 s/2.5 s only if every unmeasured cap holds. |

This envelope is extremely tight: the measured `/turn` p95 plus modeled TTS already consumes 2,301 ms, leaving only 199 ms for ASR finalization, dispatch, and egress. It is not credible as a production claim before live testing. A normal endpointing tail can break it.

For an empty-history first turn, substituting the measured 1,062/6,992 ms first-sentence values produces a budget sum of approximately 1,492/7,487 ms. Thus the p50 may fit, but the current measured empty-turn p95 cannot meet the 2.5 s goal under this architecture. No amount of speech preconnection repairs a 6,992 ms text first-sentence p95. The release SLO must either distinguish established from first turns, tighten the text-provider tail through a separately qualified change, or accept a slower first-turn p95.

Parallel work off the critical path includes media authentication, codec negotiation, ASR/TTS connection creation, `retrieve.warmup()`, pgvector pool opening, and read-only speculative retrieval. Sequential ASR-final -> `/turn`-first-sentence -> TTS-first-byte remains dominant and cannot be overlapped without speaking content that has not passed the gates. Prompt layout and sticky session are retained, but no Gemini cache saving is assumed. Speculative retrieval is only a discardable warmer.

### 5. Accuracy mechanisms

#### Albanian ASR and confidence policy

- Build provider phrase/entity lists from commercial bank labels in `rate_tables.jsonl`, Bank of Albania document names and articles in the indexed chunk metadata, currency labels (ALL, EUR, USD), product names, and safety vocabulary (PIN, CVV, CVC, OTP). Generate the lists from versioned corpus data rather than maintaining an eval-fitted production list.
- Preserve `ë`/`ç`, punctuation, casing, decimal separators, percent signs, digit sequences, and n-best alternatives. Score both WER and critical-entity error rate; ordinary WER can hide a changed `0.10`, `10`, OTP, or bank identity.
- Initial PoC policy, to be recalibrated on a held-out Albanian call set: proceed only when calibrated utterance confidence is at least 0.75 and every detected bank/number/currency/percent span is at least 0.85 with no close conflicting alternative. A lower-confidence critical span produces `/turn` outcome `clarify`; overall confidence below 0.55, safety-keyword ambiguity, or two consecutive failed clarifications produces `/turn` outcome `handoff`. Provider confidence values are not comparable, so thresholds must be calibrated separately. If an adapter lacks usable confidence, use n-best disagreement; if neither exists, it is ineligible for automatic factual turns.
- The acceptance gate runs within `/turn` after input/sensitive/PII inspection and before retrieval. A possible secret, PII, or handoff phrase is routed conservatively even when ASR confidence is poor. The bridge itself does not author a clarification.
- Retain raw audio only under an approved privacy/retention policy. Keep redacted diagnostics and immutable correlation IDs for incident review.

#### Retrieval and answer fidelity

- The final accepted transcript still passes `trust.input_gate()`, `callcenter.decide()`, and `rag.retrieve_evidence()`; phrase adaptation never substitutes for those controls.
- `retrieve.retrieve()` remains the actual top-five dense cosine path with `LIVE = ("canonical", "base")`. There is no shipped FTS, RRF, HNSW, or reranker. Voice promotion is not contingent on inventing those components.
- `eval_asr_noise.py` strips diacritics, spells digits, or removes one stopword from existing text and measures retrieval recall. It does **not** run audio, measure WER, test endpointing, compare Azure/Chirp/Live, evaluate safety outcomes, or prove spoken bank/number fidelity. It is a useful text-perturbation regression only.
- Before each complete sentence becomes TTS input, the fidelity guard extracts normalized number-value/unit pairs and named entities. Every factual figure and institution/product/document label must be found in the sentence's cited vetted chunks with compatible labeling. Missing, conflicting, or unlabeled values fail closed: suppress the sentence, cancel generation, and return/act on a `/turn` handoff. This only narrows output; it never upgrades weak evidence or changes the `MIN_RELEVANCE_SCORE = 0.50` decision.
- The verifier must check labels as well as number occurrence. `PERFORMANCE.md` notes that the current numeric-grounding check accepted an answer with a wrong service/administration label because all numbers occurred somewhere in evidence.
- SSML uses deterministic readings while preserving the verified text value: for example, decimal and percent tokens are represented as structured value/unit pairs, acronyms are spelled according to a reviewed lexicon, and bank names use a versioned pronunciation lexicon. The synthesizer cannot paraphrase.

### 6. Trust-boundary compliance

The complete trace is audio -> final transcript -> `/turn` -> `decide()` -> optional rewrite -> live-filtered retrieval -> `trusted_hits()` -> grounded generation -> verified `/turn` sentence -> TTS. The `done` event, not voice sentiment or provider intent, drives handoff.

| Possible bypass | Preventive design element |
|---|---|
| Interim ASR triggers retrieval, generation, or speech | Interim events have no `/turn`, model, TTS, or session-write capability. The speculative warmer is read-only and its outputs are discarded. |
| ASR adapter supplies an answer or intent | The neutral interface accepts transcript/diagnostics only; schemas reject provider answer fields. |
| Partial-transcript embedding or hits are reused after finalization | Tag partial artifacts speculative and discard them; preserve `rag.retrieve_evidence()` byte-identity assertions; authoritative `/turn` retrieves again. |
| Bridge calls `retrieve()`/`rag.ask()` or a model directly | Network policy and module boundaries permit answer orchestration only through authenticated `POST /turn`. |
| Raw chunks or `tool.query` are spoken | SSE consumer allowlists verified `token` sentence events only; `tool` is telemetry. Source metadata never contains passage text (`api.source()`). |
| Unverified streamed tokens reach TTS | Sentence buffer plus server-side fidelity guard must release a sentence before the TTS queue accepts it. |
| TTS receives locally written fallback prose | TTS input requires a signed/correlated `/turn` sentence event. On `/turn` loss, call control transfers silently or plays only a non-answer network tone. |
| Stale/cross-call audio is played | Every transcript, SSE event, TTS chunk, and playback frame carries `call_id` and monotonic `turn_id`; generation mismatch means drop. |
| `done.handoff` is ignored | The orchestrator requires a terminal `done`, and `handoff=true` invokes the call-control state machine with acceptance telemetry. |
| Output verifier replaces the trust gate | It runs only after `trusted_hits()` and may suppress/handoff only; it cannot approve evidence or lower 0.50. |

### 7. Failure modes and fallbacks

| Failure | Behavior and fallback |
|---|---|
| Azure ASR error, low confidence, or malformed final | Do not call retrieval with an unstable transcript. One controlled re-ask is returned as `/turn` `clarify`; then try the already-qualified alternate STT on buffered audio if privacy and latency policy allow. Otherwise transfer to a human. |
| Primary STT timeout/outage | Circuit-break to the qualified alternate adapter for new turns. Never merge provider transcripts silently; conflicting critical entities clarify or hand off. |
| Chirp Preview change or region/quota failure | Remove it from rotation and continue with qualified Azure. Preview is never the sole production path. |
| `/turn` model/provider timeout | Preserve `api.py`'s fail-closed `RAGError`/exception -> `HANDOFF_MESSAGE`/`Outcome.HANDOFF` behavior. Add a true wall-clock cancellation deadline because `FIRST_TOKEN_BUDGET_MS` is currently checked only when a line arrives and the synchronous stream can outlive it. |
| Retrieval/DB failure or weak/wrong-family evidence | Use the existing exception-to-handoff or `trusted_hits()` unsupported response. Never fall back to model memory. |
| Fidelity-verifier mismatch | Do not render the suspect sentence. Cancel downstream work, log the mismatch with vetted hit IDs, and fail closed to handoff. |
| TTS timeout or voice/region outage | Retry only before any audio is emitted and only within the turn deadline; otherwise use a prequalified second Azure voice/region or transfer. Never ask an LLM to paraphrase for easier speech. |
| Telephony disconnect | Cancel ASR, SSE/provider stream, TTS, and playback; close the call mapping; keep only retention-approved audit data. Do not auto-replay on reconnect without a new caller turn. |
| Transfer request rejected/no agent | Keep `handoff=true`, record queue/acceptance failure, follow contact-center overflow policy, and do not resume factual automation. |
| Barge-in during generation/playback | Speech-start cancels SSE consumption and closes the upstream response, cancels TTS, clears sentence/jitter buffers, and increments `turn_id` so late data is dropped. No partial generated answer is written by current `generate_turn()` because `sessions.record()` occurs after completion. If generation already completed before playback interruption, the full authorized answer is already in `SessionStore`; log the delivered prefix separately. A later repeat currently returns that full answer. Production Redis should retain an `interrupted`/delivered-offset ledger rather than pretending the caller heard it all. Policy/refusal turns are recorded before emission by current code and remain recorded. |

The bridge must explicitly close the synchronous `requests` response used by `api.stream_answer()` when possible. Client disconnect alone is not accepted as proof that upstream model work stopped.

### 8. Eval plan

Build a consented, versioned recorded Albanian call set with reference transcripts and expected `/turn` results. Include clean microphone and 8 kHz telephone versions; Tosk/Gheg and regional accents; fast/slow speech; code-switching; crosstalk/noise; digits, decimal comma/point, percent, dates, ALL/EUR/USD; every corpus bank; regulation/document names; short follow-ups; PII; PIN/CVV/OTP incidents; account-specific requests; repeats; unsupported business deposits; and human-handoff requests.

Run Azure and pinned Chirp 3 on exactly the same audio, endpoint boundaries, phrase-list version, API region, `/turn` revision, corpus, model, session priming, Azure TTS configuration, and load profile. Report:

- WER/CER, bank/product/document entity error rate, exact digit/decimal/currency/percentage accuracy, and confidence calibration/coverage;
- false endpoint rate, speech-end-to-final latency, partial stability, and double-talk behavior;
- outcome agreement against reference transcripts for all five outcomes, `handoff`/PII flag agreement, retrieved hit-ID and top-score agreement, wrong-family/weak-evidence refusal agreement, and answer/citation agreement;
- approved-text to spoken-output entity/figure fidelity, pronunciation review, and absence of added factual content;
- caller-end-of-speech to ASR final, `/turn` first SSE/token/sentence/done, TTS first byte, audible first frame, completion, and cancellation at p50/p95/p99 under sequential, concurrent, and soak loads;
- stale-frame leakage after barge-in/reconnect, cost per completed/handed-off minute, provider error rate, and human-transfer request/acceptance/time-to-agent.

Extend `eval_asr_noise.py` with recorded transcript pairs rather than treating its synthetic transforms as ASR proof. Feed reference and provider transcripts through equivalent fresh sessions and extend `eval_calls.py`/`eval_handoff.py` for outcome preservation. Reuse `eval.py` for hit recall/latency, `bench_turn.py` for SSE timestamps, and extend `phase3_quality.py` from number occurrence to value-plus-label/entity verification. Add a media benchmark that correlates one `call_id`/`turn_id` across all stages. Promotion requires a held-out set and frozen thresholds; tuning cases cannot be the acceptance set.

## Schema 2 - Constrained Gemini Live bridge

### 1. Purpose and when it wins

This schema is the first proof of concept because it matches `README.md`'s stated Gemini Live/WebSocket roadmap and can reduce custom VAD/audio-session work. It wins only if Live can be made a transport and renderer around `/turn`, not a second answer engine.

Use it for:

- the first roadmap-aligned voice PoC;
- low-friction WebRTC/WebSocket conversational testing with built-in input transcription, VAD, interruption events, and session resumption;
- measuring whether one audio platform lowers operational media complexity;
- a production path only after zero observed independent-answer leakage, exact rendering qualification, acceptable Albanian entity accuracy, and SLO compliance.

Current feasibility evidence lists Gemini Live Albanian `sq` and input/output audio capabilities as Preview. Availability is not evidence of Albanian banking-call accuracy or exact speech rendering. The as-drawn Pipeline C with `search_knowledge_base()` is explicitly excluded.

### 2. Architecture diagram and flow

```text
PSTN/SIP/WebRTC client
  |
  v
Media/call gateway <------------------------------------------ call-control adapter
  | audio in                                                       ^
  v                                                                |
Server-owned Gemini Live session (input transcription enabled)     |
  |-- built-in/custom VAD + interruption events                    |
  |-- input audio transcription                                    |
  |-- NATIVE ANSWER AUDIO/TEXT: muted and dropped                   |
  |                                                                |
  v finalized transcript + live_session_id                         |
Bridge correlation/enforcement layer                               |
  | call_id + session_id + monotonic turn_id + tool/request_id      |
  v                                                                |
POST /turn for every finalized caller turn                         |
  |                                                                |
  +-> callcenter.decide()                                          |
  +-> optional rewrite                                             |
  +-> retrieve_evidence()                                          |
  |     `-> retrieve(k=5, canonical/base) -> trusted_hits(>=0.50)   |
  +-> grounded_messages() -> stream_answer()                       |
  |                                                                |
  `-> SSE token/done                                               |
       |       `-> outcome/handoff/PII -----------------------------+
       v
sentence buffer -> entity/figure verifier -> renderer policy
                                               |
                         +---------------------+--------------------+
                         |                                          |
                         v                                          v
             constrained Live render                      Azure TTS fallback
             approved text only                           exact SSML rendering
                         |                                          |
                         +---------------------+--------------------+
                                               v
                                  correlated output audio gate
                                  (drop absent/stale request_id)
                                               |
                                  clear on barge-in -> caller
```

Live never decides whether `/turn` is needed: every finalized turn is submitted. It never receives raw retrieval chunks. Native response audio/text to caller input is muted at the first server-controlled receive boundary and discarded before the media gateway. A sequential Live function-call behavior is not treated as enforcement; the application gate is enforcement.

For rendering, the bridge sends one already-verified complete `/turn` sentence under a dedicated render request ID. It forwards only audio frames attributable to that request. No preamble, paraphrase, continuation, or Live response tied to the caller-input generation ID is allowed. If the API cannot expose enough correlation or literal-render behavior to enforce this, the native renderer is disabled and the same approved sentence goes to Azure TTS. Sentences containing a figure, currency, percent, date, product, bank, or document name use Azure TTS until Live exactness is proven on the held-out set; this can become an all-turn fallback to avoid voice changes within an answer.

### 3. Components and reuse map

| New component | Responsibility | Repository reuse or new work |
|---|---|---|
| Media/call gateway | Telephony/WebRTC codec transport, authentication, backpressure, and transfer interface. | Genuinely new. |
| Live session manager | Create/resume server-owned Live sessions, stream audio, enable input transcription, configure VAD, receive interruption, and expose provider IDs. | Genuinely new; no Live integration exists. |
| Native-response sink | Mute/drop every Live text/audio response caused by caller input before it can reach playback. Count dropped bytes/events as an invariant metric. | Genuinely new and load-bearing. Prompt instructions are not sufficient. |
| Turn correlation registry | Bind `call_id`, BoABot `session_id`, Live session, monotonic `turn_id`, `/turn` request, render request/tool ID, and buffer generation. Reject missing, duplicate, or stale IDs. | Genuinely new. |
| `/turn` client | Submit every finalized transcript and consume `tool`, `token`, and terminal `done`; propagate cancellation. | New bridge code reusing `api.py`, `TurnReq`, `turn()`, and the exact SSE contract. |
| Guarded decision engine | Own all policy, retrieval, generation, structured outcomes, and server history. | Reuse `api.generate_turn()`, `callcenter.decide()`/`SessionStore`, `rag`, `retrieve`, and `trust` unchanged in authority. |
| Sentence/fidelity gate | Buffer only `/turn` tokens, verify exact entities/figures against vetted cited hits, and release renderable sentences. | Same new restrictive component as Schema 1. |
| Live literal renderer adapter | Request audio for approved text only, reject extra text/audio segments, expose time-to-first-audio and cancellation. | New and eligible only after exactness/enforcement qualification. |
| Azure TTS fallback | Render the same approved sentence with deterministic SSML when Live exactness/correlation is absent, risky, or degraded. | Same new TTS pool as Schema 1; required fallback, not optional architecture decoration. |
| Output audio gate | Forward only frames with the active render request and turn generation; clear all buffers on interruption. | Genuinely new and separate from provider prompting. |
| Call-control/audit/metrics plane | Drive handoff from `done`, measure accepted transfer, and audit leakage, correlation, latency, cost, session resumption, and fallback selection. | New; aligns with `README.md` remaining work. |

### 4. Latency budget

No Gemini Live call has run in this repository. Its ASR finalization and literal-render latency are therefore entirely **[P]**. The only reusable measurements are the text `/turn` distributions. Native audio must not be credited with removing the `/turn` model call because doing so would remove the authority.

#### Established-call target envelope with qualified Live rendering

| Sequential stage | Target p50 ms | Target p95 ms | Evidence status | Compression mechanism |
|---|---:|---:|---|---|
| Live VAD end plus finalized input transcription | 80 | 150 | **[P]** must measure | Live session is already receiving audio; tune explicit/custom endpointing on held-out calls. |
| Correlation check and `/turn` dispatch | 10 | 20 | **[P]** must measure | Co-located persistent bridge; no raw knowledge tool or extra model decision. |
| `/turn` start to first complete sentence, established/history mode | 1,049 | 2,001 | **[R]** N=100 Phase 3 Gemini history | Warm application/retrieval and persistent BoABot session; no Gemini cache saving assumed. |
| Verified approved-text request to first Live-rendered audio | 250 | 280 | **[P]** must measure and prove literal | Pre-open Live session; sentence-aligned request; no native answer generation admitted. |
| Output gate, codec buffer, and first-frame egress | 40 | 50 | **[P]** must measure | Correlation check is local; bounded buffer. |
| **Budget sum** | **1,429** | **2,501** | **[M/P]** target envelope | Approximately meets the goals only if all unknown Live stages qualify. |

The p95 sum is already at the approximate 2.5 s limit and percentile sums are not a prediction. With Azure TTS at the repository's modeled 300 ms instead of the 280 ms Live target, the envelope becomes approximately 1,479/2,521 ms. That fallback protects exactness, not latency.

For empty-history turns, replacing the `/turn` row with measured 1,062/6,992 ms gives approximately 1,442/7,492 ms. Thus Live transport cannot solve the existing empty-turn text tail while retaining `/turn`. There is no repository evidence that Live's audio connection changes OpenRouter text latency. Provider sessions, WebSocket, audio codecs, `/turn` HTTP, and Azure fallback connections are all pre-opened where possible. Input audio processing overlaps the caller's speech; finalized transcript -> `/turn` first sentence -> verified render remains sequential.

### 5. Accuracy mechanisms

- Configure Albanian input transcription and endpointing, but evaluate the returned text exactly as an ASR provider. `sq` availability does not establish `sq-AL` banking accuracy.
- Supply the same corpus-derived bank/document/currency/safety vocabulary where Live adaptation supports it. Keep a versioned normalization layer for `ë`/`ç`, digit and decimal forms, while retaining the provider's raw transcript and alternatives.
- Apply the same initial confidence policy as Schema 1 only if Live exposes calibratable confidence/n-best data. If it does not, use repeated-hypothesis stability and critical-entity disagreement. If critical uncertainty cannot be observed, route numeric/entity turns to modular ASR or handoff; do not infer confidence from fluent Live behavior.
- Every final transcript passes `/turn`; low-confidence clarification/handoff is a stricter `/turn` policy extension, not a Live-authored conversational answer.
- Use the same server-side value-plus-label verifier against cited vetted hits before rendering. Live never sees raw hits and cannot add its own source interpretation.
- Exact rendering qualification uses both the exact approved text and an independent transcription/human audit of generated audio. Score inserted, deleted, or changed bank/product/document names, digits, decimals, currencies, percentages, and dates. Output ASR is an eval signal, not a real-time safety guarantee because detection after playback is too late.
- The production enforcement rule is prospective: if literal rendering and audio-frame correlation are not guaranteed by the API and verified with zero critical changes on the acceptance set, disable Live output and use Azure TTS. Until then, all entity/figure-bearing sentences use Azure TTS. Do not alternate voices within one answer; choose renderer for the whole turn based on the extracted risk set.
- `eval_asr_noise.py` remains only a synthetic text retrieval perturbation test and proves none of Live transcription, VAD, native rendering, tool discipline, or interruption behavior.

### 6. Trust-boundary compliance

Trace: audio -> Live final input transcript -> `/turn` -> `decide()` -> vetted retrieval -> grounded text generation -> verified `/turn` sentence -> approved renderer -> correlated audio. `done.handoff` alone drives the contact-center action.

| Possible bypass | Preventive design element |
|---|---|
| Live answers caller before/without a tool | Server-side native-response sink mutes and drops every caller-input response; media gateway accepts no such stream. |
| Live decides a turn does not need `/turn` | Bridge submits every finalized transcript deterministically; no model choice controls submission. |
| Raw `search_knowledge_base()` returns chunks | That tool is absent. The only callable business operation is guarded `POST /turn`; raw hits never enter Live. |
| Sequential function calling is mistaken for a gate | Application output gating, not model sequencing or prompts, controls all audio frames. |
| Live paraphrases `/turn` tool result | Renderer accepts one approved sentence under a dedicated request ID; compare/qualify literal behavior; otherwise switch to Azure TTS. |
| Live adds a preamble or continuation | Allowlist active render-request audio only and terminate at its sentence boundary; drop all other output events. |
| Stale tool/audio response follows barge-in | Increment `turn_id`, cancel `/turn` and render request, clear Live/client buffers, and drop mismatched IDs. |
| Live and BoABot histories diverge | BoABot `session_id` is authoritative for policy/rewrite. Live conversation state is transport-only and is reset/resumed from the correlation registry, never used as answer memory. |
| A risky sentence silently falls back to native audio | Renderer policy is deterministic and audited; extracted entity/figure presence selects Azure until Live is qualified. |
| Handoff flag is spoken but not acted on | Terminal `done` is mandatory and the call-control state machine records queue request and human acceptance. |

Zero dropped-native-answer events is not the expected invariant metric; the sink may observe and drop such events. The required safety metric is zero native-answer bytes reaching the media gateway and zero caller-audible bypasses.

### 7. Failure modes and fallbacks

| Failure | Behavior and fallback |
|---|---|
| Live Preview API/model change, quota, disconnect, or session-resume failure | Circuit-break new calls to Schema 1. For an active call, cancel all Live generations and continue only if a qualified modular ASR can resume from consented buffered audio; otherwise hand off. |
| Missing/unstable final transcript | Do not call retrieval on partial text. Use `/turn` clarify if confidence/stability policy permits, then modular ASR or handoff. |
| Native Live answer leakage attempt | Drop frames, increment security metric, terminate the Live render capability for that session, and switch to Azure TTS/Schema 1. Any byte that reached caller is a release-blocking incident. |
| Missing/stale render or tool ID | Drop the content, clear buffers, and use Azure TTS only if the approved sentence and active `/turn` ID remain valid; otherwise cancel/handoff. |
| Exact-render mismatch in qualification or production audit | Disable Live renderer globally or for the affected version/voice. Route complete turns to Azure TTS; do not repair a changed number with a second generated paraphrase. |
| `/turn`, retrieval, or model failure | Preserve `api.py` exception-to-handoff behavior. A Live native answer is never a fallback. Add a hard wall-clock cancellation path while retaining the terminal handoff outcome. |
| Azure fallback outage | Transfer; do not re-enable an unqualified Live renderer merely because TTS failed. |
| Telephony drop | Cancel Live audio/session work, `/turn` SSE/upstream model, TTS, and buffers; invalidate call correlation and follow retention policy. |
| Barge-in mid-answer | Live interruption/speech-start triggers immediate media mute; increment `turn_id`; clear Live output, Azure TTS, bridge, and client buffers; close `/turn` consumption/upstream response. History semantics match Schema 1: no partial generated answer is recorded if cancellation precedes completion; a fully completed answer may already be in `SessionStore` even if playback was interrupted, and the delivered offset is logged separately. Never insert Live-native text or partial rendered text into BoABot history. |
| Human transfer not accepted | Record failure and remain in fail-closed call-control policy; do not let Live resume answering. |

### 8. Eval plan

Use the same recorded call set, reference transcripts, `/turn` revision, corpus, model, and expected outcomes as Schema 1 so Live and modular results are comparable. Measure:

- input WER/CER, bank/product/document entity accuracy, exact figures/units, critical safety-term recall, endpointing latency, confidence/stability calibration, and outcome/hit agreement versus reference transcripts;
- zero native answer audio bytes at the media gateway, rejected/missing/stale request IDs, raw-chunk exposure attempts, and adversarial turns that try to make Live skip or override `/turn`;
- approved text versus Live audio fidelity for names, figures, labels, language, additions, and omissions, with independent transcription plus bilingual human review;
- end-of-speech to Live final, `/turn` first SSE/token/sentence/done, render request to audio, audible first frame, completion, interruption mute time, and stale-buffer leakage at p50/p95/p99;
- WebSocket reconnect/session resumption, Preview version change, rate limits, concurrent calls, cost per completed/handed-off minute, and privacy/retention behavior;
- transfer requests, actual queue entry, agent acceptance, time-to-agent, and fallback success from Live to modular service.

Extend `eval_calls.py` and `eval_handoff.py` with provider-transcript outcome agreement, `eval.py` with transcript-specific retrieval comparisons, `bench_turn.py` with Live/media timestamps, and `phase3_quality.py` with exact text-to-audio entity/label checks. Retain `eval_asr_noise.py` only as a cheap regression. Add fault-injection tests that deliberately send native Live response frames, stale IDs, duplicate tool results, reconnects, and barge-in at every buffer boundary. Release requires zero observed trust-path bypasses, not merely a high percentage.

## 9. Comparison matrix

| Dimension | Schema 1: guarded modular | Schema 2: constrained Live bridge |
|---|---|---|
| Estimated/target first-audio p50/p95 | Established-call target envelope **1,479/2,496 ms**; `/turn` part measured, speech stages not. Empty-turn envelope about **1,492/7,487 ms**. | Established-call target envelope **1,429/2,501 ms** with unmeasured qualified Live render; Azure fallback about **1,479/2,521 ms**. Empty-turn envelope about **1,442/7,492 ms**. |
| Albanian availability status | Azure documents streaming STT/TTS `sq-AL`; Chirp 3 streaming `sq-AL` is Preview per feasibility report. Neither has repo quality data. | Gemini Live documents Albanian `sq`, input transcription, and native audio as Preview per feasibility report; no repo quality data. Azure `sq-AL` TTS is the required exactness fallback. |
| Trust-preservation effort | Medium: clean transcript and SSE seams, but cancellation and sentence verification are new. | High: native answers and paraphrases are inherent bypass risks requiring multiple application enforcement points. |
| New-build effort | High: media plane, VAD, two STT adapters/harness, TTS, cancellation, telephony, operations. | Medium-high apparent component count, but high enforcement/testing work; telephony and Azure fallback still needed. |
| Cost shape | STT + OpenRouter text model + Azure TTS + telephony; dual STT during bake-off. Cost per completed/handoff minute must be measured. | Live audio/session + OpenRouter text model + telephony, plus Azure TTS on fallback/risky turns; possible double model/audio cost. Must be measured. |
| Ops complexity | Multiple sequential services; clear isolation and replaceability. Cross-cloud if Chirp remains. | Fewer primary audio services, but Preview lifecycle, dual session state, leakage controls, and mandatory fallback add operational risk. |
| Accuracy control | Strongest deterministic pronunciation/SSML and provider-neutral STT comparison. | Input and conversational behavior may be good, but exact output is unproven; risky turns fall back to deterministic TTS. |
| Tail-latency reality | Established measured text p95 leaves only 199 ms beyond modeled TTS for ASR/dispatch/egress. Empty-turn p95 fails badly. | Same `/turn` tail remains. Live can only win in unmeasured ASR/render stages; it cannot erase the text contract. |
| Eval value | Highest for isolating Azure versus Chirp and establishing the reference pipeline. | Highest for testing transport simplification, interruption, enforcement, and whether native rendering is safe. |
| Best role | Production-control baseline, STT bake-off, telephony baseline, and guaranteed fallback. | First roadmap-aligned PoC; production only after strict promotion gates. |

The comparison intentionally does not credit prompt caching for Gemini. `README.md` reports 0/30 cached-token turns for the current default. The better measured history distribution is useful operating evidence, not proof of a cache mechanism.

## 10. RECOMMENDATION

Build **Schema 2 first as a constrained PoC**, because it matches the README roadmap and quickly tests the biggest architectural unknown: whether Gemini Live can be reduced to an Albanian audio/transcription/rendering bridge without creating a second answer path. Do not implement Pipeline C's raw `search_knowledge_base()` tool.

Build **Schema 1 second as the controlled reference and production fallback**, starting with the neutral STT interface, the recorded-call harness, Azure `sq-AL`, pinned Chirp 3 `sq-AL`, and one preconnected Azure TTS voice. The modular reference is necessary even if Live looks promising because it identifies whether errors arise in transcription, `/turn`, or rendering and supplies a non-Preview operational route.

Promote constrained Live only when a version-pinned prototype demonstrates all of the following on held-out adversarial and realistic telephone audio:

1. zero caller-audible trust-path bypasses and zero native-answer bytes past the media gateway;
2. every finalized caller turn reaches `/turn`, every spoken answer sentence originates from `/turn`, and every terminal `handoff` drives real call control;
3. predefined outcome agreement, Albanian critical-entity/number accuracy, and confidence coverage against reference transcripts;
4. zero critical bank/name/number/unit/label changes in approved-text-to-audio rendering, with enforceable request correlation;
5. real caller-end-of-speech-to-first-audio p50/p95 within the reviewed targets under concurrency, plus bounded barge-in cancellation and no stale audio;
6. acceptable Preview lifecycle, privacy, regional, cost, session-resumption, and human-transfer acceptance results.

If any trust or exact-render condition cannot be guaranteed, switch output immediately to Azure TTS. If constrained Live still misses the p95 target, has unacceptable Albanian transcript accuracy, is operationally unstable, or cannot guarantee zero bypasses, switch the production path to Schema 1 using whichever ASR wins the held-constant Azure-versus-Chirp bake-off. A faster pipeline is not acceptable if it changes the authoritative answer path.

## FINAL SUMMARY

- Two schemas cover the genuinely distinct viable families: guarded modular ASR-to-`/turn`-to-TTS and a constrained Gemini Live bridge around `/turn`.
- Schema 1 is the production-control baseline and STT bake-off harness; Azure `sq-AL` is primary and pinned Chirp 3 `sq-AL` Preview is the measured alternative.
- Schema 2 is the first roadmap-aligned PoC, but native Live answers are muted and every finalized caller transcript must go to `POST /turn`.
- Only sentence-aligned, entity/figure-verified `/turn` text is renderable; raw hits, interim transcripts, `tool.query`, provider answers, and stale frames are never spoken.
- The established-call budget envelopes are about 1.48/2.50 s p50/p95 for modular and 1.43/2.50 s for qualified Live rendering, but all speech-stage values remain modeled or must-measure.
- Empty-turn measured first-sentence p95 is 6,992 ms, so neither schema can honestly promise a 2.5 s first-turn p95 without separately improving the text-provider tail.
- Gemini prompt-cache savings are not assumed: the current default measured 0/30 turns with cached tokens despite preserving sticky sessions and split prompts.
- Albanian qualification must score real recorded-call WER plus exact bank, product, document, digit, decimal, currency, percent, safety-outcome, and handoff accuracy; `eval_asr_noise.py` does not prove those properties.
- Barge-in cancels ASR/SSE/model/TTS work, clears every audio buffer, rejects stale turn IDs, and records no partial generated answer; completed-but-unheard output is tracked with a delivery ledger.
- Promote Live only after zero observed trust bypasses, exact rendering, p50/p95 targets, stable Preview operations, and accepted human transfers; otherwise use Azure TTS or fall back fully to Schema 1 with the ASR bake-off winner.

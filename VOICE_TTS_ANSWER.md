# Voice bot: separate TTS or native voice model?

> "For the voice bot do we need to setup TTS or we can use a model that directly handles
> voice without overheads?"

## Short answer

We do **not strictly need a separate TTS service** if we use a native realtime audio-in/audio-out model such as the Gemini Live bridge already named in the project plan. However, it would not be “without overheads”: there is still audio transport, speech processing, model/network latency, interruption handling, and telephony encoding. More importantly for BoABot, a native voice model must remain a bridge around the existing text `/turn` contract; it must not answer directly and bypass the repository’s text-based safety, handoff, retrieval, and evidence gates.

For this project, the recommended design is therefore: use Gemini Live/WebSocket as the voice bridge if it can reliably transcribe Albanian, submit the resulting text to `/turn`, and render only the approved `/turn` response as audio. That can avoid operating a separate TTS vendor while preserving BoABot’s trust boundary. If the native model cannot be constrained to that contract, use explicit streaming ASR plus streaming TTS instead. The repository does not yet contain evidence sufficient to choose between those two implementations on Albanian quality or real voice latency, so both require a live proof of concept.

## What exists today

There is no audio pipeline in the repository. The implemented product is a text FastAPI service: `POST /turn` accepts a textual `question` and streams tool, token, and done events over SSE. The done event exposes a structured outcome (`answer`, `clarify`, `unsupported`, `handoff`, or `repeat`), session ID, vetted sources, and PII/handoff flags. There is no microphone or telephony input, ASR, WebSocket audio stream, speech synthesis, audio codec handling, or barge-in implementation. See `README.md`, “ARCHITECTURE / HOW IT FITS TOGETHER” and “CURRENT STATE / REMAINING WORK,” and `api.py`, `TurnReq`, `generate_turn()`, and `turn()`.

The current default in `rag.py` is `google/gemini-3.1-flash-lite`, called through OpenRouter’s text chat-completions endpoint. Despite the Gemini name, this code sends text messages and receives text; it is not a Gemini Live native-audio integration. `rag.py`, `grounded_messages()` and `retrieve_evidence()`, also show that generation occurs only after retrieval evidence has been vetted.

The README explicitly lists the production voice work still required: “A Gemini Live/WebSocket audio bridge connected to the `/turn` contract,” telephony/call control, production session and agent infrastructure, and richer evaluation for Albanian ASR noise, interruptions/barge-in, live latency, and accepted human handoff. The key wording is “connected to the `/turn` contract”: native voice is planned as transport around the guarded service, not as a replacement for it.

## Why the text contract must stay in the loop

BoABot’s important controls operate on text. `callcenter.py`, `decide()`, applies the input gate, detects repeat requests and credential incidents, redacts textual PII, classifies human-handoff intent from a text embedding, and returns a structured routing decision. `trust.py`, `input_gate()` and `trusted_hits()`, reject encoded/instruction-override input, weak evidence, and the wrong evidence family for institutional price questions. `retrieve.py`, `retrieve()`, embeds a text query and searches only canonical/base pgvector records. `rag.py` then constructs an Albanian, evidence-grounded text prompt.

A fully end-to-end speech model that independently hears and answers the caller could skip those controls or make them observe only a transcript after the model has already decided what to say. That is not an acceptable substitution for the current architecture. The safe integration sequence is:

1. Convert caller audio to a stable Albanian transcript.
2. Send that transcript through `POST /turn` unchanged enough for the text gates, classifier, and retrieval to operate.
3. Speak only the token stream or final policy response returned by `/turn`.
4. Use the returned handoff and PII flags to drive call control.

A native audio model can perform steps 1 and 3, eliminating a separately managed TTS service, but it still has to honor steps 2 and 4.

## Albanian support and validation

The repository demonstrates Albanian text handling, not production speech quality. `rag.py` instructs the model to answer in Albanian, and `PERFORMANCE.md`, “Phase 2,” reports that Gemini and Mistral were judged fluent and register-appropriate in Albanian on a small text comparison. That does not establish Albanian ASR accuracy, voice naturalness, pronunciation of bank names and figures, or the behavior of either native speech-to-speech or a standalone TTS engine.

`eval_asr_noise.py` is also not an audio or ASR benchmark. It deterministically edits text by stripping Albanian diacritics, spelling digits, or deleting a stopword, then measures retrieval recall. It is useful as an early robustness check, but it does not compare actual Albanian transcripts from competing speech systems. The proof of concept should therefore test real calls, names, rates, currency/percentage readings, noisy speech, interruptions, and whether sensitive or handoff utterances still route correctly.

## Latency evidence and recommendation

The latency case for removing separate TTS has not been measured here. `PERFORMANCE.md`, “What the measurements mean,” states that first audio was calculated as first-sentence time plus an **assumed 300 ms TTS first-byte delay** for a preconnected streaming synthesizer. No TTS call ran. Consequently, 300 ms is a modeled engineering assumption, not measured overhead or a provider SLA.

In `PERFORMANCE.md`, “Phase 3” and “Voice budget,” the Gemini text benchmark measured first-token p50/p95 of 844/5,986 ms with empty history and 789/1,562 ms with history. Adding the modeled TTS delay produced estimated first-audio p50/p95 of 1,362/7,292 ms and 1,349/2,301 ms respectively. Gemini therefore passed the 1.5-second target only at p50, and passed the 2.5-second target at p95 only for established/history turns. These are sequential text-path benchmarks plus a TTS assumption—not native-audio measurements and not a voice SLO. The current code and README now default to Gemini, while `PERFORMANCE.md` also preserves historical DeepSeek-era wording and results; the measured distributions should be treated as provider-sensitive benchmark evidence.

**Recommendation:** implement the README’s Gemini Live/WebSocket bridge as the first proof of concept, but keep `/turn` as the authoritative decision and answer engine. Use native audio output instead of a separate TTS service only if tests confirm good Albanian transcription and speech, exact rendering of approved banking figures, reliable barge-in, and no bypass of trust or handoff decisions. Benchmark its real end-to-end first audio against a preconnected streaming ASR/TTS baseline at p50 and p95. Do not select native voice merely to subtract the modeled 300 ms: the present evidence shows that model first-sentence tail latency, contract preservation, and Albanian accuracy are the larger risks.

## Sources

- `README.md` — “ARCHITECTURE / HOW IT FITS TOGETHER” and “CURRENT STATE / REMAINING WORK”
- `PERFORMANCE.md` — “What the measurements mean,” “Phase 2,” “Phase 3,” “Voice budget,” and “What still falls short of production level”
- `callcenter.py` — `decide()` and session/handoff/PII policy
- `api.py` — `TurnReq`, `stream_answer()`, `generate_turn()`, and `/turn`
- `rag.py` — model configuration, `grounded_messages()`, and `retrieve_evidence()`
- `trust.py` — `input_gate()` and `trusted_hits()`
- `retrieve.py` — `retrieve()` and live-status filtering
- `eval_asr_noise.py` — deterministic Albanian ASR-like text perturbations

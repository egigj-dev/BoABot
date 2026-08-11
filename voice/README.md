# BoABot guarded voice pipelines

This package implements the two designs in `VOICE_PIPELINE_SCHEMAS.md` without
changing the existing text service. In both designs, the only business-answer
authority is HTTP `POST /turn`. Speech-provider answers, ASR partials, SSE
`tool` events, retrieval passages, locally written fallback prose, and stale
audio are never caller output.

The package imports and its offline suite run with no cloud SDK, credentials,
database, model, or audio device. Provider SDKs load only when a real adapter is
started.

## Ten-minute offline start

From the repository root:

```bash
.venv/bin/python -m pip install pytest httpx httpx-sse fastapi
.venv/bin/python -m pytest voice/tests/ -q
.venv/bin/python -m voice.cli.schema1_demo
.venv/bin/python -m voice.cli.schema2_demo
.venv/bin/python -m voice.cli.bakeoff
```

The Schema 1 demo runs fake microphone frames through fake streaming ASR, a
deterministic `/turn` service double, the real confidence/sentence/fidelity and
correlation gates, fake TTS, and an audited output sink. Schema 2 injects a
malicious native Live answer and demonstrates that its bytes are counted and
dropped before sending a separate `/turn`-authorized sentence through the
correlated Azure-fallback path. The bake-off prints a stable Azure/Chirp table
skeleton when real providers are unconfigured.

## Trust path and vetted evidence

The production path is:

```text
final transcript -> HTTP POST /turn -> token sentences -> fidelity guard -> TTS
                               `-----> done.handoff -> call control
```

`TurnClient` sends the current `api.py:TurnReq` fields (`question`, `session_id`,
and `include_vetted_text=True`) and parses its `tool`, `token`, and terminal
`done` data events. It sets `Accept: text/event-stream`, requires one of the five
terminal outcomes, closes the response on cancellation, and applies a true
wall-clock first-token deadline even if no SSE line arrives.

The opt-in `include_vetted_text` extension adds `passage_text` to each cited
entry in `done.sources`. The voice bridge verifies value/unit and label claims
against that vetted text; older servers that omit it still fail closed. The
default remains `False` for all other `/turn` callers, and `tool` events never
contain passage text. A production voice bridge requesting this field must be
authenticated and use TLS so vetted chunks are not exposed to public or
unaudited consumers. The text is supplied only at the `/turn` trust boundary;
the voice package never calls retrieval, RAG, a model, or call-center internals
to obtain it. The offline service double mirrors this opt-in source shape.

Similarly, the existing `TurnReq` has no ASR diagnostics. Both schemas still
submit every finalized transcript to the real endpoint. If the local calibrated
confidence gate requires clarification or handoff, caller output is allowed only
when `/turn` returns an equally restrictive structured outcome; otherwise the
bridge suppresses output and transfers silently. An authenticated additive
server extension can later accept the diagnostics without weakening this rule.

## Module map

- `config.py`: environment-only settings and adapter-scoped validation.
- `events.py`: immutable call, transcript, turn, render, and audio contracts.
- `asr/`: neutral `StreamingASR`, deterministic fake, continuous Azure `sq-AL`,
  and pinned Speech-to-Text V2 `chirp_3` `sq-AL` Preview adapters. Chirp 3 uses
  explicit LINEAR16 decoding because auto-detection failed on raw PCM streams.
  `StreamingASR.start()` accepts an async iterable of PCM frames and normalizes
  a sync iterable if one is accidentally supplied.
- `phrases.py`: phrase/entity labels derived from versioned
  `rate_tables.jsonl`, plus currency and credential-safety vocabulary.
- `vad.py`: swappable PCM16 energy VAD/endpointing; it has no answer authority.
- `turn_client.py`: guarded HTTP/SSE client, cancellation, validation, and hard
  first-token wall timer.
- `sentence_buffer.py`: allowlisted token deltas to punctuation-terminated
  sentences with `api.py` boundary semantics.
- `fidelity_guard.py`: post-`/turn`, fail-closed entity and value/unit/label
  verifier. It can suppress only; it never admits evidence.
- `tts/`: approved-text-only interface, deterministic Albanian SSML, reusable
  Azure synthesizer, turn cancellation, and an offline fake.
- `correlation.py`, `barge_in.py`: monotonic call/turn/generation/render IDs,
  playback-first interruption, cancellation, clearing, and stale rejection.
- `telephony.py`: fail-closed call-control contract and simulator. Twilio, ACS,
  or SIP media and real transfer are deployment adapters, not implemented here.
- `metrics.py`: in-memory p50/p95/p99 stages and outcome, handoff, stale-output,
  and native-answer-drop counters; optional caller-supplied Redis publisher.
- `schema1.py`: modular VAD/ASR -> `/turn` -> verified TTS orchestration and a
  discard-only speculative warmer stub that issues no requests.
- `schema2.py`: constrained Gemini Live transport, unconditional native-response
  sink, deterministic Azure fallback policy, and correlated output gate. Live
  state is transport-only; BoABot `session_id` remains authoritative.
- `cli/`: two offline traces and the recorded-audio bake-off scaffold.
- `tests/`: fast offline smoke and trust-invariant coverage.

## Environment variables

All variables are read by `VoiceSettings.from_env()`. Missing cloud values are
not errors until their corresponding real adapter is selected.

### Shared

| Variable | Default | Meaning |
|---|---|---|
| `BOABOT_TURN_BASE_URL` | `http://127.0.0.1:8000` | Guarded text service base URL |
| `BOABOT_FIRST_TOKEN_BUDGET_MS` | `6000` | Hard wall-clock budget until first token |
| `VOICE_CONFIDENCE_PROCEED` | `0.75` | Calibrated utterance proceed threshold |
| `VOICE_CONFIDENCE_CRITICAL` | `0.85` | Bank/number/currency/percent span threshold |
| `VOICE_CONFIDENCE_HANDOFF` | `0.55` | Overall confidence handoff threshold |
| `VOICE_PCM_SAMPLE_RATE_HZ` | `16000` | PCM sample rate fed to ASR/TTS; used by the Chirp adapter's explicit LINEAR16 decoding config. Must match the 16 kHz mono PCM the bridge supplies (8 kHz telephony later). |
| `VOICE_LATENCY_P50_TARGET_MS` | `1500` | Measurement target, not an achieved SLO |
| `VOICE_LATENCY_P95_TARGET_MS` | `2500` | Measurement target, not an achieved SLO |
| `VOICE_REDIS_URL` | unset | Optional production metrics/session plane |
| `VOICE_TELEPHONY_MODE` | `simulated` | Deployment call-control selector |

The existing text service separately needs `OPENROUTER_API_KEY` and its database
settings as documented at repository root. The voice layer does not consume that
key and never invokes the model itself.

### Schema 1: modular Azure or Chirp input, Azure output

| Variable | Required when | Meaning |
|---|---|---|
| `BOABOT_ASR_PROVIDER` | live Schema 1 | `azure` or `chirp` (`fake` offline) |
| `AZURE_SPEECH_KEY` | Azure ASR | Azure Speech subscription key |
| `AZURE_SPEECH_REGION` | Azure ASR | Azure resource region |
| `AZURE_TTS_KEY` | Azure TTS | Optional distinct TTS key; falls back to Speech key |
| `AZURE_TTS_REGION` | Azure TTS | Optional distinct region; falls back to Speech region |
| `AZURE_TTS_VOICE` | Azure TTS | Default `sq-AL-AnilaNeural` |
| `GOOGLE_CLOUD_PROJECT` | Chirp ASR | GCP project with Speech-to-Text V2 enabled |
| `GOOGLE_SPEECH_REGION` | Chirp ASR | Default `europe-west4`; pin an approved region |
| `GOOGLE_CHIRP_MODEL` | Chirp ASR | Default pinned ID `chirp_3` |

Google Application Default Credentials must also be available to the Cloud
Speech client. Chirp 3 Albanian is Preview and must not be the only production
route until qualified.

### Schema 2: constrained Gemini Live plus Azure fallback

| Variable | Required when | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | Gemini Live input | Server-owned Gemini Live credential |
| `GEMINI_LIVE_MODEL` | Gemini Live input | Version-pinned Live model name |
| `AZURE_TTS_KEY` | fallback rendering | Azure key (or `AZURE_SPEECH_KEY`) |
| `AZURE_TTS_REGION` | fallback rendering | Azure region (or `AZURE_SPEECH_REGION`) |
| `AZURE_TTS_VOICE` | fallback rendering | One qualified voice for the entire answer |

Gemini Live native output is never a fallback. Until literal rendering and
request correlation are qualified, every turn uses Azure TTS. Entity/figure
turns continue to use Azure even after an optional literal renderer qualifies.

## Live preparation and smoke

Install provider extras only in an environment that will run them:

```bash
.venv/bin/python -m pip install -r voice/requirements.txt
```

Start the existing service, preserving its text authority:

```bash
.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Validate Schema 1 variables:

```bash
BOABOT_ASR_PROVIDER=azure .venv/bin/python -m voice.cli.schema1_demo --live
```

or set Google Application Default Credentials and use
`BOABOT_ASR_PROVIDER=chirp`. Validate Schema 2 variables with:

```bash
.venv/bin/python -m voice.cli.schema2_demo --live
```

These flags intentionally stop after validation because this repository has no
selected microphone, WebRTC/SIP media gateway, or telephony provider. A live
deployment supplies `AsyncIterable[bytes]` 16 kHz PCM frames to the selected ASR
or Live manager and an `AudioSink` that accepts only gated `AudioChunk` objects.

## Accounts and production decisions checklist

- Azure Speech resource, key, region, and a reviewed Albanian TTS voice.
- For the comparison route, a billed GCP project, Speech-to-Text V2 API,
  application credentials, regional quota, and approved Chirp 3 Preview use.
- For Schema 2, a Gemini API project/key with Live Preview access and a pinned
  model/version policy.
- Existing OpenRouter key and the existing service's PostgreSQL/pgvector data.
- Optional Redis endpoint and a deliberate retention/redaction policy.
- A chosen Twilio/ACS/SIP/WebRTC media and call-control adapter, including real
  queue entry, transfer rejection, and agent-acceptance observation.
- Authentication between media bridge and `/turn`, TLS/reverse proxy, regional
  privacy review, concurrency/soak tests, and consented Albanian call fixtures.

Production promotion requires zero caller-audible bypasses, zero native-answer
bytes past the gateway, complete terminal `done` handling, correct handoff
acceptance, calibrated critical-entity accuracy, bounded interruption/stale
output, and measured end-to-end p50/p95/p99. The documented latency values are
qualification targets, not claims.

# BoABot guarded voice pipelines

This package implements the guarded components in `VOICE_PIPELINE_SCHEMAS.md`
without creating a second business-answer authority. HTTP `POST /turn` remains
the single authority: speech-provider answers, ASR partials, SSE `tool` events,
retrieval passages, locally written fallback prose, and stale audio are never
caller output. Two real local, single-turn browser-microphone harnesses are
available: Arm A for Schema 1 and Arm B for Schema 2. Neither is a telephony or
production media gateway.

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
correlation gates, fake TTS, and an audited output sink. Schema 2 runs an
input-only Gemini Live transcription stand-in, measures final-transcript to
first-approved-sentence to first-audio latency, and injects a malicious native
Live answer to demonstrate that its bytes are counted and dropped. The bake-off
prints a stable Azure/Chirp table skeleton when real providers are unconfigured.

## Implemented local status

| Interface | Real local path | Server | Current boundary |
|---|---|---|---|
| Arm A | Browser PCM WAV -> Azure `sq-AL` ASR -> guarded `POST /turn` -> Azure TTS | `voice.web_app:app`, loopback port `8100` | One recorded turn; completed answer WAV returned in the HTTP result |
| Arm B | Browser PCM WAV -> Gemini Live transcription -> guarded `POST /turn` -> second constrained Gemini Live rendering session | `voice.web_app_b:app`, loopback port `8200` | One recorded turn; native input-session answers are counted/dropped and the completed gated WAV is returned |

Both depend on `api:app` at the configured `BOABOT_TURN_BASE_URL` (port `8000`
by default). The browser layers return public source metadata only. Arm A's
backend may request vetted passage text inside the trusted `/turn` boundary for
defensive fidelity checking, while Arm B requests no passage text and exposes
none to either Gemini Live session.

## Trust path and vetted evidence

The guarded answer path is:

```text
final transcript -> HTTP POST /turn -> server-approved sentence -> streaming TTS
                               `-----> done.handoff -> call control
```

`TurnClient` sends the current `api.py:TurnReq` fields (`question`, `session_id`,
and `include_vetted_text=True`) and parses its `tool`, `token`,
`approved_sentence`, and terminal `done` data events. The server emits an
`approved_sentence` only after a complete sentence passes its evidence-fidelity
gate, allowing TTS to start while `/turn` is still streaming. The client sets
`Accept: text/event-stream`, requires one of the five terminal outcomes, closes
the precisely correlated response on cancellation, and applies a true
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
- `schema2.py`: input-only Gemini Live transcription transport, unconditional
  native-response sink, guarded rendering policy, per-utterance milestone
  latency, and a correlated output gate. Live state is transport-only; BoABot
  `session_id` remains authoritative.
- `live_bridge.py`: Arm B's real two-session Live path. The first session
  transcribes and has every native response counted and discarded; the second
  receives only complete `/turn`-approved text and returns correlated audio.
- `web_app.py`, `arm_a.html`: loopback Arm A microphone UI around the real
  `voice.cli.live_run.run_single()` Azure cascade.
- `web_app_b.py`, `arm_b.html`: loopback Arm B microphone UI around
  `LiveTurnBridge`, including public fidelity and native-drop audit fields.
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
| `VOICE_CONFIDENCE_CRITICAL_DISABLED` | unset | Set to `1` only after `voice.cli.probe_confidence` proves the selected ASR provider returns constant confidence and cannot supply meaningful critical-span scores; the audit reason records every bypassed turn |
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

### Schema 2: constrained Gemini Live and optional Azure fallback components

| Variable | Required when | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | Arm B / Gemini Live input | Server-owned Gemini Live credential |
| `GEMINI_LIVE_MODEL` | Arm B / Gemini Live input | Configured Live model name; code default `gemini-3.1-flash-live-preview` |
| `AZURE_TTS_KEY` | Schema 2 Azure fallback components | Azure key (or `AZURE_SPEECH_KEY`) |
| `AZURE_TTS_REGION` | Schema 2 Azure fallback components | Azure region (or `AZURE_SPEECH_REGION`) |
| `AZURE_TTS_VOICE` | Schema 2 Azure fallback components | One qualified voice for the entire answer |

Gemini Live output produced while transcribing caller audio is never an answer
or a fallback: it is counted and discarded. The local Arm B harness uses a
separate, zero-temperature constrained Live session whose only input context is
the complete approved `/turn` text. The general `schema2.py` orchestration also
retains an Azure TTS fallback policy for production qualification; Arm B does
not exercise that fallback.

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

These CLI demo flags intentionally stop after provider validation. The local
browser harnesses below now supply a selected microphone path, but the repository
still has no WebRTC/SIP media gateway or telephony provider. A production
deployment must supply authenticated streaming media, call control, and an
`AudioSink` that accepts only gated `AudioChunk` objects.

## Local Arm A browser microphone

For a graphical, turn-based interface, keep `api:app` running on port 8000 and
start the local microphone app in a second terminal:

```bash
set -a; source .env; set +a
.venv/bin/python -m uvicorn voice.web_app:app --host 127.0.0.1 --port 8100
```

The recorded Azure diagnostics found the same confidence value (`0.78952557`)
across clean, noisy, silence, and degraded inputs, so there is no meaningful
per-word signal for the critical-span gate. Export
`VOICE_CONFIDENCE_CRITICAL_DISABLED=1` only as an explicit operational opt-in;
the per-turn `confidence_reason` then records the bypass. Without the opt-in,
questions whose transcript contains a bank, number, currency, or percentage
safely return `clarify` when span confidence is unavailable. The configured
thresholds are not lowered or silently reinterpreted.

Open `http://127.0.0.1:8100`, allow microphone access, press **Regjistro**, speak
one question, and press **Ndalo dhe dërgo**. The browser resamples the recording
to a 16 kHz, 16-bit, mono PCM WAV. The backend runs the same real
Azure ASR -> `/turn` -> Azure TTS `run_single()` path as `voice.cli.live_run`,
then returns the transcript, structured outcome, public sources, timings, and a
playable answer WAV. It never sends raw evidence passages to the browser.

This is a local single-turn development interface, not a telephony or production
media gateway. Bind it to loopback as shown: it has no authentication, and answer
audio is returned after the turn completes rather than streamed during synthesis.

## Local Arm B browser microphone

Arm B has its own web server and interface on port `8200`. Keep the same guarded
`api:app` authority running on port `8000`, then start Arm B separately:

```bash
set -a; source .env; set +a
.venv/bin/python -m uvicorn voice.web_app_b:app --host 127.0.0.1 --port 8200
```

Open `http://127.0.0.1:8200`. The browser records and resamples one question to
16 kHz mono PCM. Gemini Live transcribes it, `/turn` on port `8000` remains the
only answer authority, every native Gemini answer is counted and discarded, and
a second constrained Gemini Live session renders only the approved text. The UI
shows the input transcript, approved text, spoken transcript, verbatim and
normalized match results, dropped-native-response counters, public sources,
stage timings, and the gated answer WAV.

Like Arm A, this service is loopback-only, unauthenticated, single-turn, and
returns answer audio after the turn completes. Arm B requires `GEMINI_API_KEY`
and a configured `GEMINI_LIVE_MODEL`; it does not expose retrieved passage text
to the browser or either Gemini Live session. Handoff/unsupported results return
no answer audio, while a non-handoff result must contain non-empty PCM before the
web layer will construct a WAV.

## Accounts and production decisions checklist

- Azure Speech resource, key, region, and a reviewed Albanian TTS voice.
- For the comparison route, a billed GCP project, Speech-to-Text V2 API,
  application credentials, regional quota, and approved Chirp 3 Preview use.
- For Arm B/Schema 2, a Gemini API project/key with Live access and a pinned
  model/version policy; retain native-response drop counters and literal-render
  fidelity auditing as release evidence.
- Existing OpenRouter key and the existing service's PostgreSQL/pgvector data.
- Optional Redis endpoint and a deliberate retention/redaction policy.
- A chosen Twilio/ACS/SIP/WebRTC media and call-control adapter, including real
  streaming playback, queue entry, transfer rejection, and agent-acceptance
  observation. The local Arm A/B web servers do not satisfy this item.
- Authentication between media bridge and `/turn`, TLS/reverse proxy, regional
  privacy review, concurrency/soak tests, and consented Albanian call fixtures.

Production promotion requires zero caller-audible bypasses, zero native-answer
bytes past the gateway, complete terminal `done` handling, correct handoff
acceptance, calibrated critical-entity accuracy, bounded interruption/stale
output, and measured end-to-end p50/p95/p99. The documented latency values are
qualification targets, not claims.

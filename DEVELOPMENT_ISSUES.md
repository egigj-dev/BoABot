# BoABot voice development issues

Date: 2026-08-14

This log records problems encountered while building the two local voice
development arms and the repository-backed safeguards used to overcome them.
The arms are harnesses around the guarded text service, not independent bots:

```text
caller audio -> POST /turn -> only /turn-authorized text is spoken
```

The citations below point to commits or current working-tree artifacts. A fix in
the voice layer is always restrictive: it may suppress or route output, but it
does not replace `callcenter.decide()`, retrieval, `trusted_hits()`, or `/turn`.

## Arm A

### 1. Azure confidence looked granular but was constant

**Problem.** Azure detailed results exposed utterance and per-word confidence,
but the recorded probe found the same `0.78952557` value across clean speech, a
different sentence, a short utterance, silence, 0 dB noise, and time-degraded
audio. A critical-span gate therefore had no real word-level signal to distinguish
a bank name, number, currency, or percentage.

**Why it mattered.** Treating the constant as calibrated confidence would make a
safety gate look active while admitting critical recognition errors. Lowering
the existing thresholds would conceal the provider limitation.

**How it was overcome.** Commit `b312e82` added the reproducible diagnostics in
`voice/cli/probe_confidence.py` and saved calibration evidence in
`calibration.json`. The thresholds remain unchanged. The critical-span check can
be skipped only through explicit `VOICE_CONFIDENCE_CRITICAL_DISABLED=1`; the
per-turn decision reason is then `critical-span gate bypassed: provider confidence
proven constant`. Without the opt-in, a critical span with no usable confidence
returns `clarify`. Regression coverage is in `voice/tests/test_confidence.py`.

### 2. Azure Speech SDK result properties changed shape

**Problem.** With Azure Speech SDK 1.51.1, `result.properties` could be a plain
dictionary rather than the SDK property object expected by the adapter.
Unconditionally calling `get_property()` made detailed JSON—and therefore
alternatives and confidence diagnostics—unavailable.

**Why it mattered.** Recognition could still yield display text while silently
losing the diagnostic fields used by conservative confidence handling.

**How it was overcome.** The compatibility shim introduced with commit
`d46de59` in `voice/asr/azure_adapter.py` checks for `get_property`; otherwise it
uses dictionary `get()` with the same Azure property ID. The same compatible read
is used by `voice/cli/probe_confidence.py`.

### 3. Albanian numeric notation collided with provider decimals

**Problem.** The corpus uses decimal commas and dots as thousands separators,
including `10.000` and `1.000.000`, while generated/provider text can contain an
ordinary dot decimal such as `4.75`. Parsing every dot as a decimal turns source
amounts into the wrong value; stripping every dot corrupts provider decimals.

**Why it mattered.** The fidelity verifier could reject a correct approved
sentence or, worse, compare the wrong numeric values and miss a changed banking
figure.

**How it was overcome.** The current `voice/fidelity_guard.py`, `_number()`,
detects repeated three-digit groups as Albanian thousands separators while
preserving ordinary decimal-dot output. Commit `5883e44` introduced the
dot-thousands correction, and
`voice/tests/test_sentence_fidelity.py::test_fidelity_guard_treats_dot_as_albanian_thousands_separator`
covers the behavior.

### 4. Albanian inflection could hide or manufacture label matches

**Problem.** Natural output uses forms such as `bankës` and `bankën`, while
source text may use `banka`. Product and fee labels also have known inflections.
A literal comparison rejects correct Albanian; broad stemming can erase the
distinguishing words that separate one bank, document, product, or fee from
another.

**Why it mattered.** The same numeric value can appear under several labels. A
loose match reopens the wrong-label hole; an overly strict match suppresses
correct grounded answers.

**How it was overcome.** The current `voice/fidelity_guard.py` folds only the
known grammatical head forms in `ENTITY_HEAD_FORMS` and only known label forms in
`LABEL_TOKEN_FORMS`, then removes the controlled `LABEL_STOPWORDS` set. It still
requires the sentence's distinguishing label tokens to be a subset of the
evidence label and retains the complete entity name. Commit `5883e44` closes the
subset-label hole; current regression evidence is in
`voice/tests/test_entity_inflection.py`, `test_fidelity_label_subset.py`, and
`test_claim_rows.py`.

### 5. A client-side verifier must not become a second evidence gate

**Problem.** Adding post-`/turn` checks for names and figures creates a dangerous
design temptation: the voice client could appear to approve evidence or repair a
model answer independently of the server.

**Why it mattered.** Any client approval path would weaken the single-authority
invariant and could bypass `retrieve.retrieve()`, `trusted_hits()`, or the policy
outcome from `callcenter.decide()`.

**How it was overcome.** `FidelityGuard` explicitly has suppress-only semantics:
it compares claims with already-vetted passages and can reject output, but cannot
admit evidence or change relevance gates. The current server enforces this before
emitting `approved_sentence` in `api.authorized_sentences()`; Arm A sends only
those events to correlated Azure TTS. `voice/cli/live_run.py` also performs a
defensive post-turn check. Commit `a032897` added the server-approved-sentence
streaming path, with regression coverage in
`voice/tests/test_sentence_fidelity.py` and `test_schema1_e2e.py`.

## Arm B

### 1. Gemini Live can answer before `/turn` authorizes anything

**Problem.** The Live input session can emit model text, audio, and output
transcription in response to caller audio. Forwarding any of it would create a
native Gemini answer path beside `/turn`.

**Why it mattered.** A native answer could skip deterministic policy, vetted
retrieval, fidelity enforcement, structured outcomes, and handoff control.

**How it was overcome.** Commit `b3c5ab7` added `LiveTurnBridge` and the
load-bearing `NativeResponseSink`. `_drop_native_message()` counts every native
text/audio event and byte as `native_response_dropped_events` and
`native_response_dropped_bytes`, then discards it. A second Live session receives
only `{"approved_text": ...}` from `/turn`; assertions forbid chunks, passages,
sources, or tools in that context. `OutputAudioGate` also requires the active
render, turn, and generation IDs before forwarding audio. Evidence is in
`voice/live_bridge.py`, `voice/schema2.py`, and
`voice/tests/test_schema2_invariant.py`.

### 2. Live transcription reliability needed a reproducible test

**Problem.** A single successful interactive transcription did not establish
that Gemini Live would consistently finalize the same Albanian banking audio, or
make SDK failures and fabricated/degraded transcripts visible.

**Why it mattered.** Every finalized transcript is the question submitted to
`/turn`; instability can change policy routing, retrieval, figures, or named
institutions even though the downstream authority remains guarded.

**How it was overcome.** Commit `b3c5ab7` added the real-audio probes
`voice/cli/live_albanian_probe.py` and
`voice/cli/live_transcribe_repeat.py`, plus the checked-in
`voice/cli/live_albanian_probe.wav` fixture. The repeat harness opens independent
sessions, preserves each SDK error without retrying it away, and classifies every
result as `EXACT`, `MINOR`, `DEGRADED`, `FABRICATED`, or `UNCLASSIFIED` against a
fixed Albanian reference.

### 3. An empty WAV could look like a successful verification result

**Problem.** A WAV file can exist with only its 44-byte header and zero PCM
frames; the current working tree contains `arm_b_answer.wav` as a concrete
44-byte artifact. A verification that merely checked file existence—or compared
two empty text results—could report a vacuous match despite having no answer.

**Why it mattered.** “No result” is not evidence that approved text was rendered,
and a handoff with accidental audio is also a contract violation. Either mistake
can hide a broken output gate.

**How it was overcome.** The current `voice/live_bridge.py` rejects an empty input
transcript, empty approved answer text, and a render with no audio. The current
`voice/web_app_b.py`, `run_arm_b()`, separately requires handoff outcomes to emit
no caller audio and at least one handoff event, while non-handoff outcomes must
produce non-empty PCM before a WAV is built. Input WAV parsing also requires a
positive frame count. `voice/tests/test_web_app_b.py` verifies the browser
handoff/no-audio contract, and `voice/cli/live_bridge_demo.py` applies the same
no-result checks in the CLI harness.

## Cross-cutting rule

Both arms consume only server-approved sentence events or complete approved
`/turn` text. `voice/sentence_buffer.py` allowlists `token` events, keeps `tool`
queries out of the speech buffer, and defers an ambiguous digit-ending dot so a
provider split such as `0.` + `00.` cannot become two spoken fragments. Commit
`a032897` and `voice/tests/test_sentence_fidelity.py` provide the regression
evidence. These controls suppress unsafe output; none authorizes facts outside
`/turn`.

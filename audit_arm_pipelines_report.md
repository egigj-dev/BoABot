# Arm A / Arm B voice pipeline audit report — BoABot

Date: 2026-08-14. Auditor: automated engineering audit per
`audit_arm_pipelines_brief.md`. Scope: the two local voice pipelines
(`voice/web_app.py` + `voice/schema1.py`/`voice/cli/live_run.py` = Arm A;
`voice/web_app_b.py` + `voice/live_bridge.py` = Arm B) against
`PIPELINE_FEASIBILITY.md`, `VOICE_PIPELINE_SCHEMAS.md`, `DEVELOPMENT_ISSUES.md`.
`audit_rag_report.md` and `ARM_AB_LIVE_EVAL_2026-08-14.*` were read as context
only, per the brief; `api.py`'s exception-handling story was not re-audited.

## Executive summary

The non-negotiable invariant — **only text emitted and authorized by `POST
/turn` may become answer audio** — holds end-to-end in both arms as currently
shipped, with one exception found and fixed: Arm B's Live input-transcription
reader could return an **unfinalized** (interim) transcript to the caller of
`/turn` if the Gemini Live `receive()` stream ended before ever observing
`server_content.turn_complete`. That is now a hard `RuntimeError` (fail
closed) instead of a silent pass-through of possibly-unstable text into the
one place (`/turn`'s `question` field) that drives policy, retrieval, and the
structured outcome. This was reproduced against the real code before the fix
and is closed by a new regression test module, `voice/tests/test_live_bridge.py`
(previously the only 0-coverage seam in the whole audited surface — no test
file exercised `LiveTurnBridge` directly before this audit).

Beyond that one confirmed bug, the audit found the rest of the audited control
flow to be correctly restrictive: `SentenceBuffer` allowlists `token` events
only and defers ambiguous digit-terminal dots in both arms; `FidelityGuard` is
suppress-only and runs server-side in `api.authorized_sentences()` before any
`approved_sentence` event is ever emitted, so both arms consume already-vetted
text; `NativeResponseSink`/`OutputAudioGate`/`CorrelationRegistry` correctly
drop/gate native Live answers and stale-ID audio in the real Arm B code path;
empty-WAV and empty-transcript rejection is enforced on both browser entry
points and inside the bridge. Several **spec/code-drift** and **design**
findings are reported below (ranked) because they matter to the feasibility
verdict, but they do not violate the invariant and were intentionally **not**
"fixed" here because doing so would require non-surgical, architecture-level
changes (see "Not fixed" in each finding).

## Per-arm feasibility verdict vs `VOICE_PIPELINE_SCHEMAS.md`

### Arm A (Schema 1 guarded modular)

**Verdict: realizes the guarded-modular textual seam correctly for a
single-turn, loopback, unauthenticated harness. Feasible with changes,
matching `PIPELINE_FEASIBILITY.md`'s own verdict; the remaining gap is
exactly the gap the docs already claim (media plane, VAD-driven barge-in,
telephony), not a hidden defect.**

- `voice/web_app.py` + `voice/cli/live_run.py` implement Azure `sq-AL` ASR →
  `ConfidencePolicy` → guarded `POST /turn` → `approved_sentence`-driven Azure
  TTS, exactly as `VOICE_PIPELINE_SCHEMAS.md`'s Schema 1 §2 diagram describes
  for the collapsed "one browser recording = one turn" case. Confirmed by
  reading `live_run.py:279-671` line by line.
- **Deviation (file:line):** `voice/schema1.py`'s `Schema1Orchestrator`,
  `BargeInCoordinator` (`voice/barge_in.py`), `EnergyVAD` (`voice/vad.py`),
  and `CorrelationRegistry`/`SpeculativeWarmer` are a **separate, unused**
  reference implementation of the full streaming/telephony design (continuous
  audio, VAD-triggered barge-in, multi-turn `CorrelationRegistry`). They are
  exercised only by `voice/tests/test_schema1_e2e.py` and
  `test_correlation_barge.py`, never imported by `voice/web_app.py` or
  `voice/cli/live_run.py` (`grep -n "schema1 import\|from .schema1"
  voice/cli/live_run.py` shows only `CRITICAL_RE, ConfidenceAction,
  ConfidencePolicy` imported — not `Schema1Orchestrator`). This means the
  barge-in/VAD/correlation machinery described as "[R] implemented" in
  `VOICE_PIPELINE_SCHEMAS.md`'s "Implementation status" section is real code
  with real tests, but it is **not wired into the actual Arm A harness**,
  which is strictly single-turn request/response over HTTP with no VAD and no
  barge-in. This is not a false "[R]" claim — the doc never says the browser
  harness *has* barge-in — but it is a distinction future readers should not
  blur: "Arm A" (the running service) and "Schema1Orchestrator" (the
  reference design class) are different code paths with different coverage.
- Confidence policy: real, live-code-exercised (`live_run.py:374-462`). When
  `ConfidencePolicy.effective()` returns anything other than `PROCEED`,
  **`/turn` is never called at all** (`live_run.py:379-462` returns early with
  `turn_called: False`). This is stricter than `VOICE_PIPELINE_SCHEMAS.md`
  Schema 1 §3's own design intent, which places "the transcript acceptance
  gate ... inside the authoritative `api.py`, `generate_turn()`/
  `callcenter.decide()` path" so that `/turn`'s own independent PII/secret
  detection still sees every turn regardless of ASR confidence. Today, a
  caller whose Azure confidence looks low never has their utterance reach
  `callcenter.decide()` at all — not unsafe (nothing is spoken; `audio_out_bytes:
  0`), but a real **[spec/code drift]**: the confidence gate is currently
  bridge-authored, which is exactly what Schema 1 §3's own table says a
  correct implementation avoids ("It is stricter than current policy, never a
  bypass" — true for output, not true for the fact that `/turn`'s own gates
  never run). **Not fixed**: moving the confidence gate server-side is a
  cross-cutting architecture change outside "surgical, low-risk."
- Handoff/unsupported/clarify semantics: `api.py` emits `approved_sentence`
  events for its own fixed policy/refusal/handoff strings (never model prose)
  and Arm A speaks them like any other approved sentence
  (`live_run.py:477-530`). This is compliant with the invariant (the spoken
  text is still `/turn`-authorized), and is a legitimate design point,
  different from Arm B's choice (see below).

### Arm B (Schema 2 constrained Gemini Live bridge)

**Verdict: realizes the constrained bridge's core enforcement mechanisms
(native-answer drop, output correlation, forbidden-context assertions)
correctly. One confirmed invariant-relevant bug was found and fixed (see
above). Exact-render qualification (Azure-TTS fallback for
figure/entity-bearing sentences) described in Schema 2 §5/§6 is not wired
into the real harness — this is an acknowledged, still-open [P] item per
`PIPELINE_FEASIBILITY.md`, not a regression.**

- `voice/live_bridge.py::LiveTurnBridge` reuses `NativeResponseSink`,
  `OutputAudioGate`, `CorrelationRegistry` from `voice/schema2.py` directly
  (`live_bridge.py:14-19`), and correctly drops **every** message from the
  input Live session via `_drop_native_message()` (`live_bridge.py:296-307`,
  called unconditionally inside the `_transcribe()` receive loop at line 291)
  — confirmed by the new `test_transcribe_drops_native_answer_content_from_input_session`
  regression test.
- **Deviation (file:line):** `voice/schema2.py`'s `RendererPolicy` /
  `Renderer.AZURE` fallback (schema2.py:96-105), which Schema 2 §5/§6 requires
  ("all entity/figure-bearing sentences use Azure TTS" until Live exactness is
  proven) is **never invoked** by the real `live_bridge.py::_render()`
  (`live_bridge.py:309-394`). The real Arm B renders **every** approved
  sentence through the literal Gemini Live renderer, unconditionally, with no
  risk-based fallback to Azure TTS. `PIPELINE_FEASIBILITY.md`/
  `VOICE_PIPELINE_SCHEMAS.md` both explicitly flag exact-render fidelity as an
  open PoC question and the working `verbatim_match`/`normalized_match` audit
  fields exist precisely to expose this gap for eval, so this is **[spec/code
  drift] against the target design, not a false "[R]" claim** — the docs never
  assert this fallback is implemented in Arm B. It does **not** violate the
  invariant (only `/turn`-approved text is ever sent to the renderer; the risk
  is rendering *fidelity*, i.e. whether Live reproduces the exact text, not
  rendering *authority*). **Not fixed**: wiring a real `AzureTTS` fallback into
  `live_bridge.py::_render()` keyed off `FidelityGuard.extract_claims()`/
  `extract_entities()` is a genuine feature addition, not a surgical bug fix,
  and risks behavior change beyond this audit's remit.
- **Design note (not a bug):** Arm B's `run_turn()` treats **both**
  `outcome == "unsupported"` and `outcome == "handoff"` as `bridge_handoff`
  (`live_bridge.py:155-158`) and speaks **no** audio for either, only emitting
  a `handoff` event. Arm A, by contrast, speaks the `/turn`-approved
  `unsupported`/`handoff` policy message text as audio. Both are invariant-
  compliant (neither ever speaks unauthorized text), but they are materially
  different caller experiences and worth flagging for product review: on Arm
  B, a caller whose question is legitimately outside corpus coverage
  (`unsupported`, not a safety escalation) hears silence and a UI-only
  "handoff" banner rather than the refusal message `/turn` already generated
  for them. This exact behavior is documented as intentional in
  `DEVELOPMENT_ISSUES.md` Arm B §3 and has passing test coverage
  (`voice/tests/test_web_app_b.py::test_browser_turn_handoff_returns_no_audio`),
  so per the brief's "do NOT re-litigate already-fixed issues... unless you
  find a regression" instruction, it was left alone.

## Invariant-preservation audit (traced paths)

Traced every place raw/interim/native text or audio could reach a caller or
`/turn`:

| Path | Arm | File:line | Verdict |
|---|---|---|---|
| ASR interim text → `/turn` | A | `live_run.py:308-341` (only `final=True` transcripts appended to `final_transcripts`; interim only logged) | Safe |
| ASR interim/unfinalized text → `/turn` | B | `live_bridge.py:265-303` | **Was unsafe — fixed, see below** |
| Empty transcript → `/turn` | A | `live_run.py:336-341`, `_combine_final_transcripts:70-79` (raises if empty) | Safe |
| Empty transcript → `/turn` | B | `live_bridge.py:130-133` (`if not input_transcript.strip(): raise`) | Safe |
| Empty WAV upload | A | `web_app.py:32-56` (`_wav_duration`, requires `frames > 0`) | Safe |
| Empty WAV upload | B | `web_app_b.py:35-57` (`_read_pcm_wav`, requires `frames > 0` and non-empty `pcm`) | Safe |
| `tool.query` → speech buffer | A, B | `api.py:433` emits `tool` separately from `token`; `authorized_sentences()` (`api.py:119-150`) only ever consumes the `token`/text stream, never tool events; `SentenceBuffer.feed_event` (`sentence_buffer.py:52-57`) allowlists `type == "token"` for the client-side fallback path | Safe |
| Raw retrieved passages → renderer | A | `web_app.py:75-105 _browser_result` strips `passage_text` from the public JSON (asserted by `test_web_app.py`) | Safe |
| Raw retrieved passages → Live session | B | `live_bridge.py:147-150` explicit `assert not result.vetted_chunks`; `assert all("passage_text" not in source ...)`; `render_context = {"approved_text": approved_text}` only (`live_bridge.py:191-195`) | Safe |
| Fidelity guard before TTS | A, B | `api.py:119-150 authorized_sentences()` runs `FidelityGuard.verify_sources()` **before** yielding `approved_sentence`; both arms only ever queue/render text taken from `approved_sentence` events (`live_run.py:477-492`, `live_bridge.py:185-189` uses `result.tokens`, which are emitted 1:1 and in lockstep with `approved_sentence` at every emission site in `api.py` — verified by reading all 7 emission sites at `api.py:422-516`) | Safe, but see latent-risk note below |
| Stale/cross-generation audio forwarded | B | `voice/schema2.py::OutputAudioGate.forward()` (`schema2.py:76-88`) validates `render_request_id`/`turn_id`/`generation_id` via `CorrelationRegistry.validate()` before forwarding; correlation-mismatched Azure/Live chunks raise in Arm A (`live_run.py:514-524`) | Safe |
| `done.handoff` drives no-audio / call-control | A | `live_run.py` records `handoff`/`outcome` in the manifest; no real call-control exists in this local harness (documented [P] gap, not a bug) | Consistent with documented scope |
| `done.handoff`/`unsupported` drives no-audio | B | `live_bridge.py:155-183` (`bridge_handoff` → early return, `output_audio` never populated; enforced again defensively in `web_app_b.py::run_arm_b()`) | Safe |

**Latent risk (not a confirmed bug):** Arm B's `approved_text` is built as
`"".join(result.tokens)` (`live_bridge.py:185`) rather than
`" ".join(result.approved_sentences)`. These are equal today only because
every `api.py` emission site pairs a `token` event and an `approved_sentence`
event for the identical guard-approved text, in lockstep, with a consistent
leading-space convention (verified across all 7 emission sites). If a future
change to `api.py` ever broke that 1:1 pairing (e.g., a token event emitted
without a matching approved_sentence, or vice versa), Arm B would silently
diverge from the guard-approved text without any test catching it today,
because no existing or new-here test exercises `api.py` and `live_bridge.py`
together end-to-end. Recommend Arm B switch to joining
`result.approved_sentences` directly in a future change; not fixed here
because it requires touching call sites/formatting assumptions beyond a
single-bug surgical fix, and current behavior is not wrong.

## Ranked logic findings

1. **[CONFIRMED bug — fixed]** `voice/live_bridge.py::LiveTurnBridge._transcribe()`
   (pre-fix, was lines 265-294): if the Gemini Live `receive()` async
   iterator ends (session timeout, reconnect, provider closing the stream)
   before ever observing `server_content.turn_complete`, the function
   returned whatever text had been accumulated from `input_transcription`
   messages — text the provider had never marked final — as if it were the
   caller's accepted question. This text then flows unchanged into
   `TurnRequest(input_transcript.strip(), ...)` (`live_bridge.py:136-143`),
   i.e., directly into `/turn`'s authoritative `question` field, which drives
   `callcenter.decide()`, retrieval, and the structured outcome. This is a
   direct violation of the invariant's requirement that only an **accepted
   final transcript** reach `/turn`. Severity: high (silently degrades
   policy/retrieval routing on a real provider hiccup, with no error
   surfaced). See "Fixes applied" below.

2. **[spec/code drift]** `voice/live_bridge.py::LiveTurnBridge._render()`
   never applies `voice/schema2.py::RendererPolicy`'s figure/entity-based
   Azure-TTS fallback (schema2.py:96-105); every approved sentence is spoken
   through the unqualified literal Live renderer regardless of risk. See Arm
   B feasibility section above. Severity: medium (accuracy/fidelity risk on
   an already-flagged-as-open PoC question, not an authority-boundary
   violation). Not fixed (feature addition, not surgical).

3. **[spec/code drift]** Arm A's `ConfidencePolicy` gate runs entirely
   bridge-side before `/turn` is called, rather than inside `/turn` as Schema
   1 §3's component table specifies, so a low-confidence turn never reaches
   `callcenter.decide()`'s own independent PII/secret detection.
   `live_run.py:379-462`. Severity: low (fails safe — nothing is ever spoken
   in this path — but is an architecture gap worth tracking). Not fixed
   (cross-cutting change).

4. **[design note, already covered by existing tests]** Arm B conflates
   `unsupported` with `handoff` for audio-suppression purposes
   (`live_bridge.py:155-158`), silently dropping the `/turn`-authored
   `unsupported` refusal message that Arm A would speak. Documented as
   intentional in `DEVELOPMENT_ISSUES.md`; not re-litigated per the brief.

5. **[latent risk]** Arm B derives `approved_text` from joined `token` deltas
   rather than `approved_sentences` directly (`live_bridge.py:185`). See
   invariant-audit table above. Not fixed; flagged for a future
   `api.py`+`live_bridge.py` integration test.

6. **[correct as-is]** `SentenceBuffer` tool-event exclusion, decimal-fragment
   deferral (`sentence_buffer.py:36-44`), `FidelityGuard` suppress-only
   semantics (`fidelity_guard.py:173-327`, no method that could admit
   evidence or lower `MIN_RELEVANCE_SCORE`), `NativeResponseSink` counting-and-
   discarding (never plumbed to any output sink — confirmed by reading
   `_transcribe()` end-to-end), `OutputAudioGate`/`CorrelationRegistry`
   stale-ID rejection (`schema2.py:76-88`, `correlation.py:60-67`, exercised
   by `test_correlation_barge.py` and the new `test_live_bridge.py`) — all
   verified correct by reading the code, not by trusting
   `DEVELOPMENT_ISSUES.md`'s "[R] implemented" claims.

7. **[correct as-is]** ASR adapter SDK-shape compatibility shim
   (`azure_adapter.py:246-250`, `hasattr(properties, "get_property")`) is
   correctly defensive; the surrounding `try/except (AttributeError, TypeError,
   ValueError, json.JSONDecodeError)` (`azure_adapter.py:270-271`) fails closed
   to "no diagnostics" (constant/no confidence → `ConfidencePolicy` already
   treats missing confidence as `HANDOFF`), never to a crash or to fabricated
   diagnostics.

## Fixes applied

### Fix 1: reject an unfinalized Gemini Live transcript instead of forwarding it to `/turn`

File: `voice/live_bridge.py`, method `LiveTurnBridge._transcribe()`.

```diff
         transcript = ""
+        finalized = False
         async with client.aio.live.connect(
             model=self.settings.gemini_live_model, config=config
         ) as session:
             ...
             async for message in session.receive():
                 server = message.server_content
                 if server and server.input_transcription and server.input_transcription.text:
                     transcript = _append_transcript(
                         transcript, str(server.input_transcription.text)
                     )
                 self._drop_native_message(message)
                 if server and server.turn_complete:
+                    finalized = True
                     break
+        if not finalized:
+            raise RuntimeError(
+                "Gemini Live input session ended before turn_complete; "
+                "no finalized transcript is available"
+            )
         return transcript.strip()
```

This is restrictive-only: it adds a new fail-closed rejection path and does
not change behavior for any turn that already completes normally (i.e., every
turn in the existing test suite and every real successful Live turn).

Regression test: new file `voice/tests/test_live_bridge.py` (3 tests, none
existed for `LiveTurnBridge` before this audit):

- `test_transcribe_rejects_unfinalized_transcript_when_stream_ends_early` —
  fakes a Live session whose `receive()` yields one non-final
  `input_transcription` message and then ends without `turn_complete`;
  asserts `RuntimeError` with `"turn_complete"` in the message.
- `test_transcribe_accepts_transcript_when_turn_complete_observed` — same
  shape but the second message carries `turn_complete=True`; asserts the
  correctly-accumulated final text is returned.
- `test_transcribe_drops_native_answer_content_from_input_session` — asserts
  a native `model_turn` text part from the input session is counted via
  `NativeResponseSink` and never appears in the returned transcript.

## Remaining [P] / genuinely-new work — feasibility of what's left

Both arms remain, as `VOICE_PIPELINE_SCHEMAS.md` itself states, "single-turn,
loopback-only, unauthenticated" development harnesses. Nothing found in this
audit changes that verdict. Per remaining item, "blocks production?":

| Remaining [P] item | Exists today? | Blocks production? |
|---|---|---|
| Telephony/media gateway, codecs, call transfer | No (`telephony.py::SimulatedCallControl` only) | **Yes** — hard blocker, no code path bypasses this; must be built |
| Continuous-audio VAD + barge-in wired into the real arms | `EnergyVAD`/`BargeInCoordinator` exist and are tested, but unused by `web_app.py`/`web_app_b.py` (single-turn only) | **Yes** for multi-turn calls; the reference classes reduce the remaining work but are not yet integrated |
| Streaming (send-as-you-speak) ASR feeding `/turn` incrementally | No — both arms send one complete recording | **Yes** for real-time UX, but not for correctness; nothing in shipped code blocks adding it later since `/turn` already accepts one complete text turn by design |
| Azure-TTS fallback for figure/entity-bearing Live-rendered sentences (Schema 2 §5) | `RendererPolicy` exists in `schema2.py` but is unwired in the real bridge (finding #2 above) | **Yes for a Live-audio production launch** — until wired and qualified, Arm B cannot claim exact rendering; does **not** block Arm A |
| Redis-backed session/metrics plane, cancellation wall-clock deadline, TTS-first-byte pooling | No | No — these are latency/ops maturity items, not correctness blockers for a first PoC |
| Confidence gate moved server-side into `/turn` (finding #3) | No | No — current behavior fails safe; recommended before production but not a hard blocker |

Nothing in the *shipped* code was found to actively block closing these
gaps — they are additive work, not architectural dead ends.

## VERIFY-AND-REPORT

### 1. `pytest voice/tests` summary, BEFORE and AFTER

BEFORE (working tree with my changes stashed, i.e. HEAD-equivalent working
tree state as found at task start):

```
1 failed, 84 passed, 2 warnings in 6.45s
FAILED voice/tests/test_web_app.py::test_page_exposes_microphone_pcm_and_playback_controls
```

This failure is **pre-existing and unrelated**: `voice/arm_a.html` uses
`id="rec"`/`id="send"`/`id="cancel"` (a record/send/cancel UI), while
`test_web_app.py` still asserts the older `id="record"`/`id="stop"` markup.
It was not touched by, or related to, this audit's fix, and it fails
identically with my changes stashed out.

AFTER (with `voice/live_bridge.py` fix and new `voice/tests/test_live_bridge.py`):

```
1 failed, 87 passed, 2 warnings in 7.51s
FAILED voice/tests/test_web_app.py::test_page_exposes_microphone_pcm_and_playback_controls
```

Same single pre-existing/unrelated failure; +3 passing tests from the new
file; zero new failures.

### 2. Minimal repro, before/after, for the one CONFIRMED bug fixed

Before (run against the code with the fix stashed out):

```
$ .venv/bin/python - <<'EOF'
# ... construct a FakeSession whose receive() yields one non-final
# input_transcription message and then stops (no turn_complete) ...
text = await bridge._transcribe(frames(), 16000)
print("BUG: _transcribe returned an UNFINALIZED transcript:", repr(text))
EOF
BUG: _transcribe returned an UNFINALIZED transcript: 'Sa është'
```

After (same repro, now `voice/tests/test_live_bridge.py::test_transcribe_rejects_unfinalized_transcript_when_stream_ends_early`):

```
$ .venv/bin/python -m pytest voice/tests/test_live_bridge.py -q
...
3 passed in 1.34s
```

(the first `.` is the now-passing rejection test; the raise carries the
message `"Gemini Live input session ended before turn_complete; no
finalized transcript is available"`).

### 3. `git status --porcelain=v1` — files touched by this audit only

Full output was long-tailed with pre-existing modifications/untracked files
not touched by this audit (per the brief, only the files below are mine):

```
 M voice/live_bridge.py
?? voice/tests/test_live_bridge.py
?? audit_arm_pipelines_report.md
```

Everything else in `git status --porcelain=v1` (`.env.example`,
`PIPELINE_FEASIBILITY.md`, `README.md`, `VOICE_PIPELINE_SCHEMAS.md`, `api.py`,
`callcenter.py`, `rag.py`, `retrieve.py`, `trust.py`, `voice/README.md`,
`voice/asr/azure_adapter.py`, `voice/cli/*.py`, `voice/fidelity_guard.py`,
`voice/schema1.py`, `voice/sentence_buffer.py`, `voice/tests/test_confidence.py`,
`voice/tests/test_sentence_fidelity.py`, `voice/tts/azure_tts.py`,
`ARM_AB_LIVE_EVAL_2026-08-14.*`, `DEVELOPMENT_ISSUES.md`,
`PROJECT_ARCHITECTURE.md`, `_audit_live*.py/.sh`, `_audit_route.py`,
`arm_b_answer.wav`, `audit_rag_*.md`, `try_test.py`, `voice/arm_a.html`,
`voice/arm_b.html`, `voice/cli/eval_user_questions.py`,
`voice/tests/test_api_turn.py`, `voice/tests/test_azure_adapter.py`,
`voice/tests/test_callcenter_policy.py`, `voice/tests/test_claim_rows.py`,
`voice/tests/test_entity_inflection.py`, `voice/tests/test_live_run_transcripts.py`,
`voice/tests/test_web_app.py`, `voice/tests/test_web_app_b.py`,
`voice/web_app.py`, `voice/web_app_b.py`) predates this audit session and was
left untouched.

No `git commit` was run at any point in this audit.

### 4. Invariant verdict

**No path was found where non-`/turn` content (interim ASR, provider-native
answer, raw retrieved chunks, the `tool.query` SSE event, local bridge prose,
or stale/cross-generation audio) reaches the caller or the renderer in either
arm, after the fix in `voice/live_bridge.py` described above.** Before the
fix, one path existed where **non-finalized** (interim, not-yet-accepted)
transcript text could reach `/turn`'s authoritative `question` field —
`voice/live_bridge.py::LiveTurnBridge._transcribe()` (pre-fix lines 265-294)
— which is now closed.

## FINAL SUMMARY

- **Arm A feasibility verdict:** Feasible with changes, matching
  `PIPELINE_FEASIBILITY.md`. The real harness correctly implements the guarded
  modular seam for a single-turn recording; the full streaming/barge-in
  design (`Schema1Orchestrator`, `EnergyVAD`, `BargeInCoordinator`) is real,
  tested code but not wired into the actual running Arm A service.
- **Arm B feasibility verdict:** Feasible with changes, matching
  `VOICE_PIPELINE_SCHEMAS.md`'s Schema 2 verdict. Core enforcement
  (native-answer drop, output correlation, forbidden-context assertions) is
  real and correct; the risk-based Azure-TTS rendering fallback required by
  §5/§6 for exact-fidelity qualification is not wired into the live bridge,
  an acknowledged open item, not a regression.
- **Top real bug found and fixed:** `voice/live_bridge.py::LiveTurnBridge._transcribe()`
  could submit an unfinalized Gemini Live transcript to `/turn` if the Live
  receive stream ended before `turn_complete`; fixed to fail closed with a
  `RuntimeError`, covered by 3 new regression tests in the previously-untested
  `voice/tests/test_live_bridge.py`.
- **Single biggest remaining risk to the invariant:** Arm B's literal Live
  renderer has no fidelity-risk fallback to Azure TTS (finding #2) — nothing
  it renders is *unauthorized*, but nothing currently guarantees the audio the
  caller hears is a *verbatim* rendering of the approved text for
  figure/entity-bearing answers, which is exactly the open qualification
  question both design docs already flag as unresolved.

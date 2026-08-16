# Arm A / Arm B voice pipeline audit brief — BoABot

You are an experienced engineer auditing the LOGIC and FEASIBILITY of the two
local voice pipeline implementations of the Albanian banking-regulations
assistant BoABot at /home/egigj/projects/BoABot. This is the voice layer built
around the guarded text `POST /turn` authority; it is NOT the RAG/router audit
(audit_rag_report.md) which you should read for the established failure
classification but not repeat.

## NON-NEGOTIABLE INVARIANT (the whole point of these pipelines)

```text
caller audio -> accepted final transcript -> POST /turn -> approved sentence(s) -> speech renderer
                                                   |                    |
                                                   `-> done outcome -----`-> call control / handoff
```

Only text emitted and authorized by `/turn` may become answer audio. ASR
interim text, speculative retrieval, the SSE `tool.query`, raw retrieved
chunks, provider-native answers, local bridge prose, and stale output are
NEVER speakable. Any fix you make is a RESTRICTIVE one: it may suppress or
route output, but it must not replace `callcenter.decide()`, retrieval,
`trust.trusted_hits()`, or `/turn`.

## WHAT THE TWO ARMS ARE (current working tree, as of 2026-08-14)

- **Arm A** = Schema 1 guarded modular path. `voice/web_app.py` (FastAPI, loopback
  port 8100) accepts one browser-recorded 16 kHz/16-bit/mono PCM WAV and calls
  `voice.cli.live_run.run_single()` -> Azure `sq-AL` ASR -> guarded `POST
  /turn` -> Azure TTS. `api.py` buffers model tokens into sentences and runs the
  fail-closed `FidelityGuard` (`voice/fidelity_guard.py`) before emitting
  `approved_sentence`; only those server-approved sentences enter correlated TTS.
- **Arm B** = Schema 2 constrained Gemini Live bridge. `voice/web_app_b.py`
  (FastAPI, loopback port 8200) drives `LiveTurnBridge` (`voice/live_bridge.py`):
  one Live session transcribes the caller, every finalized transcript goes to
  `/turn`, and every native answer from that input session is counted in
  `native_response_dropped_events`/`native_response_dropped_bytes` and discarded.
  A separate constrained Live session receives only the complete approved `/turn`
  text (never raw hits). `OutputAudioGate` enforces the output boundary.

## AUTHORITATIVE SPECS TO HOLD THE CODE AGAINST

- `PIPELINE_FEASIBILITY.md` — feasibility analysis (Arm A realizes Pipeline A;
  Arm B realizes the constrained redesign of Pipeline C/Schema 2).
- `VOICE_PIPELINE_SCHEMAS.md` — the two production schemas, trust-boundary tables
  (§6 each), failure-mode tables (§7 each) — the design baseline.
- `DEVELOPMENT_ISSUES.md` — log of issues already hit & how they were overcome
  (constant Azure confidence, SDK property shape, Albanian dot-thousands,
  inflection/label matching, no-second-evidence-gate, Live answers before /turn,
  empty-WAV). Treat as KNOWN-AND-ADDRESSED; verify the fixes are still intact but
  do not re-litigate them unless you find a regression.
- `ARM_AB_LIVE_EVAL_2026-08-14.md` / `_results.json` — live A/B eval evidence.
  NOTE: audit_rag_report.md established this eval is NOT trustworthy as a live
  bug proof (hashes don't match HEAD/working tree; the mass-handoff is explained
  by the api.py system-error-vs-policy observability gap that was already fixed
  with `handoff_reason`). Use it as context, not as ground truth, and do NOT
  re-audit api.py's exception paths.

## KEY FILES (read these first)

- voice/schema1.py (Arm A orchestrator + ConfidencePolicy)
- voice/cli/live_run.py (real Arm A cascade)
- voice/web_app.py, voice/arm_a.html (Arm A browser entry)
- voice/asr/azure_adapter.py, voice/asr/base.py (ASR adapter + interface)
- voice/tts/azure_tts.py, voice/tts/base.py (TTS)
- voice/typo? -> voice/schema2.py, voice/live_bridge.py, voice/web_app_b.py,
  voice/arm_b.html (Arm B)
- voice/sentence_buffer.py, voice/fidelity_guard.py (output gating)
- voice/config.py, voice/events.py, voice/correlation.py, voice/barge_in.py,
  voice/metrics.py, voice/turn_client.py, voice/telephony.py, voice/vad.py
- voice/tests/* (existing coverage: test_schema1_e2e.py, test_schema2_invariant.py,
  test_web_app_b.py, test_api_turn.py, test_callcenter_policy.py, test_confidence.py,
  test_sentence_fidelity.py, test_entity_inflection.py, test_fidelity_label_subset.py,
  test_claim_rows.py, test_azure_adapter.py)

## YOUR GOALS

### A. Feasibility audit (write to audit_arm_pipelines_report.md)
For EACH arm, and for the pair, answer:
1. Does the implemented code actually realize the schema as specified in
   VOICE_PIPELINE_SCHEMAS.md? Enumerate concrete deviations (file:line).
2. Is the NON-NEGOTIABLE INVARIANT preserved end-to-end? Specifically audit for
   ANY path where non-/turn content (interim ASR, provider-native answer, raw
   chunks, tool.query, local prose, stale frames) could reach the renderer or the
   caller. Check the `done` outcome actually drives handoff/call-control semantics.
3. Are the "[R] implemented" claims in DEVELOPMENT_ISSUES.md / VOICE_PIPELINE_SCHEMAS.md
   actually true of the current tree (e.g. empty-input rejection, handoff-no-audio,
   OutputAudioGate correlation, NativeResponseSink counting, sentence buffer
   allowlist)? Verify by reading code, not by trusting the docs.
4. Feasibility of the REST of each schema that is still genuinely-new / [P] /
   unbuilt (telephony, media gateway, streaming, Redis, cancellation wall-clock,
   TTS first-byte, Live exact-render qualification). State clearly what exists vs
   what remains, and whether anything in the *shipped* code would block closing
   the remaining gap (vs merely not-yet-built).

### B. Logic audit — hunt for real bugs (rank by impact, each with file:line)
Look specifically for (this is not exhaustive):
- Transcript/text handling: does anything submit an UNFINALIZED or empty transcript
  to /turn? Is the empty-approved-text / empty-audio case handled everywhere
  (Arm B empty WAV, no-result-is-not-success)?
- Confidence policy (schema1.py ConfidencePolicy): with the provider confidence
  proven constant, does the logic chose CLARIFY/HANDOFF safely? Any path where a
  critical-span question bypasses confidence WITHOUT the explicit env opt-in?
- OutputAudioGate / CorrelationRegistry: can audio with a stale/absent render ID
  be forwarded after barge-in or a new turn? Is `clear()` always called? Any
  TOCTOU between activate/forward/clear?
- NativeResponseSink in Arm B: is EVERY native text/audio event from the input
  Live session actually dropped at the server boundary (not merely "counted")?
- Sentence buffer: are `tool` events excluded from speech in BOTH arms? Are
  decimal-fragment splits (`0.` + `00.`) handled? Any case encoding issue?
- FidelityGuard: suppress-only? Can it be made to approve/alter evidence or lower
  the 0.50 relevance gate? Does the value-plus-label check actually run before
  TTS in BOTH arms?
- Handoff semantics: do handoff/unsupported outcomes produce NO answer audio in
  both arms? Is there any path that speaks on handoff?
- Cross-cutting: session_id mapping, turn_id monotonicity, cancellation/late-frame
  rejection, exception paths that emit prose instead of fail-closed handoff.
- ASR adapter: the SDK-property compat shim (dict vs get_property) — any other API
  shape assumptions that could silently drop diagnostics or crash?
- TTS: any path that synthesizes provider/local text not from an approved sentence?

For each finding classify: [CONFIRMED bug], [latent risk], [spec/code drift],
[already-addressed regression], [correct as-is]. Only CONFIRMED bugs and clear
low-risk fixes get applied (see C). Do NOT re-open the api.py exception-handling
story (already fixed + tested).

### C. Apply necessary fixes
Constraints for anything you change:
- Prefer minimal, surgical, verifiable fixes. No drive-by refactors.
- Every behavioral change must come with a regression test in voice/tests/.
- Run the relevant tests yourself: `.venv/bin/python -m pytest voice/tests -q`
  and confirm any NEW failures are only pre-existing/unrelated ones (verify they
  also fail at HEAD before touching them).
- Do NOT edit .env, .env.example, or read secrets. DB password is literally 'boa'
  (postgresql://boa:boa@127.0.0.1:5433/boa); tool output may redact it as '***'.
- Do NOT modify the authority core (callcenter.py, trust.py, rag.py, retrieve.py,
  api.py) unless a voice-pipeline bug genuinely requires it — and even then only
  for the minimum, with tests.
- Do NOT `git commit`. Leave working-tree changes for the orchestrator to review.

## DELIVERABLES
1. `audit_arm_pipelines_report.md` at repo root — executive summary; per-arm
   feasibility verdict vs the schemas; invariant-preservation audit; ranked logic
   findings (file:line, severity); the fixes you applied with diff + test evidence;
   what remains genuinely-new / [P], with an honest "blocks production?" verdict.
2. Any code fixes + their regression tests (working tree only, uncommitted).

## VERIFY-AND-REPORT (mandatory; paste outputs)
1. `.venv/bin/python -m pytest voice/tests -q` full summary BEFORE and AFTER.
2. For each CONFIRMED bug you fixed: the minimal repro (command or snippet) before,
   then after.
3. `git status --porcelain=v1` at the end — enumerate exactly which files you
   changed (ignore the pre-existing long list of already-modified/untracked files:
   only report files YOU touched).
4. State the invariant verdict explicitly: "no path found where non-/turn content
   reaches the caller" OR the exact file:line where one exists.
5. End with `## FINAL SUMMARY` bullets: per-arm feasibility verdict, top real bugs
   found + fixed, and the single biggest remaining risk to the invariant.

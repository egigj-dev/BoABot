# Voice failure triage (Step 15, Task AN)

HEAD: 3cbc9ab (after AL/AM). Combined gate from this step:
`pytest tests/ voice/tests -q`.

## Status at HEAD (full-dep venv, this repo as checked out)

- `tests/` alone: 457 passed / 9 xfailed / 0 failed
- `voice/tests` alone: 96 passed / 0 failed (93 without test_live_bridge)
- combined: 553 passed / 9 xfailed / 0 failed / 0 xpassed; /tmp/baseline_all.txt EMPTY

There are **no failing tests to triage in this environment at HEAD**. The
Step-15 premise ("7 failed / 86 passed on a clean checkout") requires the
user's clean-checkout environment to reproduce; it does not reproduce here
(93-collected count matches; outcomes do not).

## Triage of the user-observed 7 (classified as far as evidence allows)

PROTOCOL RULE applied: a failure that cannot be reproduced cannot be truthfully
labeled STALE ("current behaviour is correct") or REAL ("current code is
wrong"). Each class below carries its status; nothing is guessed.

| # | Test / class | Status | Evidence |
|---|---|---|---|
| 1 | test_callcenter_policy.py "2 failures" (incl. test_classifier_verdict_is_not_bypassed_by_pricing_shape) | **NOT REPRODUCIBLE — 0/2 ever failed at HEAD in this env; 9/9 pass** | e7fd9b1 (Task V) already renamed the one pre-session failure to test_pricing_shape_followup_without_incident_evidence_is_not_handoff and updated the contract to non-HANDOFF; the voice copy WAS the file changed (git show e7fd9b1 --stat: only voice/tests/test_callcenter_policy.py). No tests/ counterpart exists. Task T's `proces\w*` cannot affect it (file has zero transfer/proces content). **The handoff premise that the voice counterpart "went unexamined" is contradicted by git.** |
| 2 | test_live_bridge.py collection error | **REAL (env) — FIXED by Task AM (72fe0d2)** | eager `import google.genai` (voice-extra-only) at module top; missing → entire file suppressed. Now `pytest.importorskip`. |
| 3 | Remaining user-observed failures (4-5 after #1/#2, incl. any SDK-calling tests) | **UNREPRODUCIBLE → cannot classify STALE/REAL without the env** | Most plausible: voice extra missing at RUNTIME (lazy azure/websockets/redis/httpx-sse imports raise at call time in tests that invoke adapters). Needs the user's `pytest -q` output or a voice-extra-less run to confirm. |
| 4 | test_api_turn.py / test_boa_architecture.py (embedding-adjacent) | **UNREPRODUCIBLE — pass at HEAD (full deps)** | numpy 2.5.2/2.5.3 both; no DSN dependence (voice tests pass with BOABOT_* stripped); no numpy-version split. |

## The Task V follow-up question

Task V's e7fd9b1 does **not** need revisiting: it synced the **voice copy**
of the classifier test (the only file the commit touched). The premise that it
synced a tests/ copy while leaving the voice counterpart unexamined is inverted
— there is no tests/ counterpart of `test_classifier_verdict_...`; grep finds
it only in voice/tests (as the renamed test) and (unrelated) in
tests/test_transfer_fee_phase1.py's incident-context test. The voice-side sync
landed; it is why HEAD is green in voice.

## Real gaps this exposes (not failures)

1. The text-only gate (`pytest tests/ -q`) silently excluded 96 voice tests
   through Steps 0–14. Corrected from Step 15; combined is the gate now.
2. The "553 passed" text-side baseline was the COMBINED run; tests/-only is
   457. Future baselines must say which.
3. The repo's optional voice SDKs are installable two ways (pyproject
   `[project.optional-dependencies].voice` and `voice/requirements.txt`) and a
   `uv sync` without `--extra voice` yields 7 failing voice tests that are
   environmental, not code. Documented in VOICE_BASELINE_2026-09-08.md.
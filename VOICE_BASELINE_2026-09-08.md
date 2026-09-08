# Voice-test baseline + failure attribution (Step 15)

Date: 2026-09-08. Repo HEAD: ccac597 (baseline pinned before Task AM commit 72fe0d2).

## Combined baseline (`pytest tests/ voice/tests -q`)

Performance in the session's full-dependency `.venv` (Python 3.12.3,
numpy 2.5.2, pytest 9.1.1, google-genai + azure + voice extras installed):

- tests/ alone: **457 passed, 9 xfailed, 0 failed**
- voice/tests alone: **96 passed, 0 failed** (93 collected without
  test_live_bridge, whose 3 tests run when google-genai is present)
- combined: **553 passed, 9 xfailed, 0 failed, 0 xpassed** (562 collected,
  zero collection errors)

NOTE: earlier handoffs reported "553 passed" as the text-side baseline.
553 is the COMBINED number; tests/-only is 457. The text-side gate count
that "must not move" this step is therefore the combined run.

Pin file: `/tmp/baseline_all.txt` (FAILED lines from the combined run) — empty
at HEAD in this environment.

## The observed clean-checkout failures (user's env: 7 failed / 86 passed)

A clean checkout alone does not reproduce the 7 here: a checkout at HEAD in
this full-dep env gives 0 voice failures. The user's clean-checkout environment
differs in the ways below; each observed failure class is attributed as far as
it can be without that environment:

| # | Failure class (user env) | Reproduces here (full-dep HEAD)? | Reproduces here (pre-session 2f61242)? | Attribution |
|---|---|---|---|---|
| 1 | RUNTIME-IMPORT-SDK | no (SDKs present) | no | voice extra missing on clean checkout: azure/websockets/redis/httpx-sse imports are lazy inside adapters; any test that CALLS an adapter with the SDK absent raises at call time, not collection. Class: env. Fix direction: lazy guards or skip-if-no-SDK in those tests (NOT done this step — AM fix covers the one eager import; the rest need a decision). |
| 2 | COLLECTION-EAGER-IMPORT | no (google.genai present) | no | test_live_bridge.py `import google.genai` at module top. google-genai is voice-extra only. Missing → whole file suppressed as a collection error. **FIXED in Task AM (72fe0d2): pytest.importorskip**. |
| 3 | OLD-CONTRACT-CLASSIFIER | no (renamed+fixed at HEAD) | **YES** (1 failure) | `test_classifier_verdict_is_not_bypassed_by_pricing_shape` asserted the OLD contract (HANDOFF on "Po te BKT?" without incident evidence). Pre-session code handed off; the session's `_incident_context_has_positive_evidence` gate changed behavior. **Already fixed at HEAD**: e7fd9b1 renamed it to `test_pricing_shape_followup_without_incident_evidence_is_not_handoff` and updated the contract. NOTE: e7fd9b1 changed ONLY voice/tests/test_callcenter_policy.py — the handoff claim that only the tests/ counterpart was synced is contradicted by git; there is no tests/ counterpart of this test. |
| 4 | OTHER | not reproducible | not reproducible | Remaining 7−1−2 = 4 user-observed failures: no environment here reproduces them. They are most plausibly also voice-extra SDK runtime failures + the collection error; without the user's environment or its `pytest -q` output, further attribution is not possible. Do NOT guess STALE/REAL membership for an unreproducible failure. |

## Bottom line for Tasks AL/AN

- Voice is NOT silently red in this repo as checked out at HEAD with full
  deps: 96/96 pass.
- The 7 failures require the user's clean-checkout env; the two root-cause
  classes identified (collection-eager-import; SDK-lazy-runtime) are real
  environmental hazards, of which the first is fixed.
- To reproduce the user's exact count, run on a checkout WITHOUT the voice
  extra (`uv sync` without `--extra voice`) and WITHOUT test_live_bridge:
  expect the 3 live_bridge tests to skip (post-AM) and the SDK-calling tests
  to fail at runtime — the "7 failed / 86 passed" signature.

File /tmp/baseline_all.txt is empty at HEAD in this env (zero FAILED lines).
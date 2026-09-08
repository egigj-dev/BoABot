# Pre-existing suite failures — triage (2026-09-08)

Baseline at this step: 11 pre-existing failures (`/tmp/baseline.txt`). One was
resolved by commit e606f3a (Q-a) before this triage ran — see the note at the
bottom. The 10 remaining are classified below. Classification is exactly one of:

- **STALE** — the test asserts an old string/shape; behaviour is correct.
- **REAL** — the test asserts correct behaviour; the code is wrong.
- **UNCLEAR** — cannot tell without a product decision.

No code changed in this pass; the deliverable is this table.

| test | classification | reason (one line) | symptom (REAL only) |
|---|---|---|---|
| tests/test_callcenter.py::test_unknown_transfer_outranks_fee_and_benign_process_phrase_does_not | REAL | The transfer-fee seam's designed escape hatch (`_TRANSFER_PROCEDURE_RE`, callcenter.py) matches `procedur\w*` but not `proces\w*`, so the benign phrase "Nuk e njoh mirë procesin e transfertës." (a process question) is hijacked into `TRANSFER_CONTEXT_ESTABLISHED` instead of falling to dense as the test requires. | A user asking "I don't know the transfer process well" gets the fee-scope clarify ("Brenda apo jashtë vendit?") instead of an informational/process answer. Fix: add `proces\w*` to the procedure bail-out vocabulary. |
| tests/test_conversational_fixes.py::test_business_renderer_states_attribution_boundary | STALE | business-rate renderer attribution prose changed (b05d53f era); test asserts the old phrase „nuk i atribuon çdo shifër normës nominale apo NEI-së" but the renderer now emits a different, equally honest attribution sentence. | — |
| tests/test_p0a_commit3.py::test_unbanked_credit_superlative_falls_through_dense | STALE | response-planning (10963c5) made `missing_key` a `STRUCTURED_PLANNER_CLARIFY` (with enumeration via Task F); the test pins the pre-plan dense-fall-through contract. Note: whether `missing_key` should CLARIFY vs fall to dense is a live product question (documented seam-gaps); the fixture simply never caught up. | — |
| tests/test_p0a_commit4.py::test_frame_effect_and_next_structured_frame_mapping | STALE | the test's `replace` set omits `DecisionReason.STRUCTURED_PLANNER_CLARIFY`, which frame_effect deliberately maps to REPLACE (callcenter.py:205) — this is the open "frame_effect" out-of-scope item, not a code accident; the fixture encodes the pre-REPLACE contract. | — |
| tests/test_p0a_commit7.py::test_unrepresented_qualifier_trace_is_constructed_at_seam | STALE | response-planning made `unrepresented_semantics` a `STRUCTURED_PLANNER_CLARIFY` (message enriched by Task F); the test pins the earlier DENSE_RETRIEVAL + `unresolved_qualifier_detected` contract for "a ofrojne kredi per udhetime?". Note: this is the critic-review availability-qualifier case whose documented direction was dense fall-through — confirm the CLARIFY supersedes it before closing. | — |
| tests/test_router.py::test_catalog_empty_names_degrades_to_normal_answer_path | STALE | institution-identity centralisation (4d3c7c1) moved the catalog to the licensed-institutions register; `bank_names()=()` no longer empties the catalog, so the degrade-to-retrieval premise no longer holds. | — |
| tests/test_router.py::test_router_off_catalog_uses_lexical_fallback | STALE | catalog wording changed to „Bankat e licencuara dhe dega e bankës së huaj në Shqipëri janë: …" (4d3c7c1 register provenance); the test asserts the old „Bankat tregtare …" prefix. | — |
| tests/test_structured_extractor.py::test_extractor_resolved_field_for_field[a ofron Banka AIB karte krediti?-raw2-expected2] | STALE | product-evidence scoping (a70f76b) drops extractor-supplied products not backed by published-term corpus coverage; the raw2 case expects the raw extractor's `credit_card` to survive, the reassembled intent now yields product=None. | — |
| tests/test_tier1_steps.py::test_issuer_rate_single_bank | STALE | issuer attribution now includes the "banka " prefix (b05d53f); the test expects the bare label "raiffeisen". | — |
| voice/tests/test_callcenter_policy.py::test_classifier_verdict_is_not_bypassed_by_pricing_shape | STALE | the batch's incident gate (`_incident_context_has_positive_evidence`) deliberately skips the frozen probe when history has no incident evidence (updating test_tier1_steps to the new contract); this voice test was not synced and still expects the probe to run on every non-informational turn. | — |

## Summary

- STALE: 9
- REAL: 1 (`test_unknown_transfer_outranks_fee_and_benign_process_phrase_does_not`)
- UNCLEAR: 0

The 9 STALE are all traceable to a specific committed behaviour change
(response-planning 10963c5, institution identity 4d3c7c1, attribution b05d53f,
product-evidence a70f76b, incident gate) with the fixture never synced — the
board can be cleaned quickly with a fixture-sync commit, minus the two flagged
design questions (missing_key CLARIFY-vs-dense; the availability-qualifier
CLARIFY) whose resolution the fixtures must match.

## Note

The 11th baseline entry, `tests/test_p0a_commit5.py::test_api_post_rewrite_reparse_without_prior_frame`
(asserted `catalog_exact_hit`, got `structured_answer_and_follow_up`), was
resolved by commit e606f3a (Q-a: fall back to ANSWER when resolved without a
product scope) — suite at 10 pre-existing failures after that commit, which is
the state this table classifies.
# Design note — frame_effect item #3 (clarify lifecycle)

Status: measure-first, no code. Produced for the Step 14 product call.

## Evidence (measured Steps 12–14)

The terminal `catalog_missing_key` refusal after a `missing_key` clarify has
**two** distinct paths, both landing in the same api.py:670–679 refusal
emission:

1. **Slot-bind path** (`Banka Credins`, `12 muaj`, `kredi per shtepi` after the
   clarify): T1 clarify carries the unresolvable missing_key intent and
   `frame_effect` REPLACEs the frame with it (callcenter.py:205). T2's concrete
   slot binds onto that 0-row intent (`decide()` with router off → reason
   CATALOG_EXACT_HIT), `retrieve_evidence` finds no rows → refusal. The same
   strings in a fresh session: answerability_abstain / semantic_clarify /
   dense_answer — all non-terminal.
2. **Non-slot path** (`nuk e di`): no merge at all. The keyed LLM rewrite
   (shaped by the AD/AH clarify text, which now names "monedha … Për cilën
   monedhë po pyesni?") expands the non-answer into a rate-shaped standalone.
   api.py:601 re-parses it into the 0-row missing_key intent → refusal.
   Pre-AD (0a3e8b8): no rewrite, `semantic_clarify`. AD caused this coupling.

The frame merge at the decide() level is clean: with the router off, a
non-slot reply with the frame present falls through to `DENSE_RETRIEVAL`,
never a rate refusal (pinned in test_p0a_commit5, Task AI commit).

## Options

### (a) CLEAR on clarify — drop STRUCTURED_PLANNER_CLARIFY from the REPLACE set

- Fixes: all four AE slot replies behave exactly like their fresh controls
  (non-terminal).
- Breaks: genuine answers that rely on the carried context. "12 muaj" after a
  deposit clarify would lose the deposit/product context and be re-parsed
  cold → SEMANTIC_CLARIFY re-ask, forcing the user to restate the product.
  AA-1-style genuine answers do **not** survive. This is the case the
  REPLACE-on-clarify merge exists to serve.
- Blast radius on the lifecycle table: none to the other three REPLACE reason
  codes; but it discards context for deposit/credit/business clarifies
  globally, not just the unresolvable case.
- test_p0a_commit4: **passes as written** (removing the entry makes `clear`
  exhaustive). That is the xfail's framing arguing for this option, not a
  measurement.

### (b) Resolvability check at bind time (Task AF's finding)

- Mechanism: in `next_structured_frame` (so every REPLACE reason inherits it),
  adopt the incoming frame only when it can actually resolve rows
  (`structured_rate_hits(new_intent)` non-empty) **or** the turn carried a
  concrete new slot in the question itself; otherwise reject the REPLACE
  (retain the previous frame / drop).
- AE effect: the three slot rows bind onto a 0-row intent → rejected → fresh
  path (abstain/re-ask/answer). "12 muaj" after a **resolvable** deposit
  clarify: check passes → merge preserved (the case the merge serves).
- Blast radius: REPLACE becomes conditional for all four reason codes —
  CATALOG_EXACT_HIT binding a bank onto an unresolvable intent is blocked
  (good); TRANSFER frames unaffected (distinct resolution).
- test_p0a_commit4: **must be rewritten** — STRUCTURED_PLANNER_CLARIFY stays
  in REPLACE at the table level; the new mapping asserts guarded-reject for
  unresolvable intents and replace for resolvable ones.

### (c) API/reparse-layer guards (what the evidence itself points at)

- **c1** — api.py:599–616: adopt the post-rewrite reparse only when it
  resolves rows or produces a user-serviceable clarify; a 0-row missing_key
  reparse must not override the fresh path.
- **c2** — router/rewrite: non-slot, non-question replies ("nuk e di") must
  not be rewritten into rate asks at all.
- Fixes the terminal emission where it happens for **both** paths; the
  lifecycle table is untouched.
- test_p0a_commit4: still fails as written (STRUCTURED_PLANNER_CLARIFY stays
  REPLACE) → xfail remains; #3 stays an open decision. If c2 lands, the
  Task-AI api test flips (visible diff, as designed).

## Recommendation

**Primary: (b).** It matches AF's diagnosis exactly ("refuse to bind a slot
onto an intent whose key cannot resolve"), preserves the genuine-answer merge
that (a) destroys, and is unit-testable where the mapping test already lives.
**Companion: (c2)** for the non-slot path — "nuk e di" must never become a
rate ask; then (c1) as defense-in-depth on the reparse.
**(a) rejected**: it is the xfail's framing, not the evidence, and it breaks
AA-1-style genuine answers.

## Board impact (UX vs wrong-answer)

- (b)+(c) fix the UX dead end (terminal refusal on a non-answer/slot-bind).
- Wrong-answer potential is **not** eliminated by (b): a carried intent that
  resolves rows for the *wrong* question would still bind. That shape has
  **never reproduced** in Y-3/AA/AB/AE — answerability and the planner have
  absorbed every wrong-topic/rephrase turn — but (b) does not close it by
  design. Closing it needs an evidence-vs-question check (distinct work;
  tracked as the latent tail of item #3).
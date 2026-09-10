# Design note — the evidence-vs-question defect class (Step BQ)

Status: GATE, no implementation. Informed by BO (count), BP (cost), BC (voice
route), BM (rejected), BN (segment-aware classifier).

## The class

`_level` short-circuits to `structured_verdict` when `rate_intent is not None`
(`core/answerability.py:321-323`), so the LLM answerability verdict never runs
on any structured-rate turn. `structured_verdict` checks only intent-internal
consistency (do the hits match `resolve_rate_rows(intent)`), which is
tautologically true since the intent produced them. **No gate checks that the
evidence answers the user's question.**

## BO: confirmed instances (count = 2 text-side, 1 voice)

- **card-debit individual** (`sa eshte komisioni i kartes te debitit?`): an
  individual receives 12 `Komisionet për biznese` (business) rows, fully cited,
  every gate passing. Segment mismatch, CONFIRMED at HEAD.
- **card-credit individual**: clarified (defused) — credit-card variant does
  not produce a wrong answer.
- **EUR deposit**: clarified (asks for afat), no EUR rows exist — correct.
- **explicit business**: answers correctly (user named the segment).
- **ASR (voice)**: `depozita` for `kredi hipotekare` → 21 deposit sources,
  confident wrong answer, through the real voice transport (BC).

Segment is the only dimension that has failed. Metric and currency have not
(they clarify). The harness (BN) already pins segment and flags these.

## BP: cost of the fix class

Re-enabling the LLM verdict = **+929 ms p50, +$0.0005–0.001/turn**, and it
consumes most of the voice 6 s first-token headroom (p95 ≈ 5.3 s with cache).
Verdict returns UNCLEAR on the card-fees case → PARTIALLY_SUPPORTED → still
generates (no abstain), so re-enabling does not choke rate answers.

## Options

### (a) Re-enable the LLM verdict for rate intents
- Catches: the segment flip (card-debit) and any other evidence-vs-question
  mismatch the LLM recognises. Highest coverage in principle.
- Misses: the ASR route (the input was already corrupted to deposits; the LLM
  sees a deposit question + deposit evidence and agrees).
- Cost: +929 ms/turn, +$0.001/turn, most of the voice headroom.
- Test proves it: force the verdict for a card-fees turn; the LLM's NO/UNCLEAR
  drives abstain/partial.

### (b) Deterministic evidence-vs-question check on the failed dimensions (segment, product, metric, currency)
- Catches: the segment flip deterministically (resolved rows' segment != the
  question's segment → abstain or clarify). Zero LLM cost.
- Misses: ASR route (intent came from corrupted transcript → both sides say
  the same wrong segment), and anything the dimensions don't cover.
- Cost: ~0 ms, no tokens.
- Test proves it: a segment-mismatch turn yields abstain/clarify, not answer.

### (c) Scope disclosure only — name the resolved product/segment in the first clause
- **MITIGATION, NOT A FIX** (label it as such in any commit).
- Turns the silent wrong answer into a visible mismatch ("Për karta debiti për
  biznese: …") so the user can catch it; does not prevent the wrong answer.
- Cost: ~0 ms; one sentence change in the renderer/generation instruction.
- **Recommended as the always-on companion** — it also serves voice (BC's
  disclosure-side mitigation: the user hears "Për kartë debiti…" and can
  correct regardless of ASR confidence).

## The ASR route — none of (a)/(b)/(c) address it

BC proved the failing case is a **confident** substitution: ASR produced
`depozita` for `kredi hipotekare` with high confidence, so any confidence-gated
confirmation fires only on the benign errors (BM, rejected). The realistic
mitigations are outside this class and should be recorded as recommendations,
not built:

- **ASR-side:** Azure phrase lists / custom speech biased toward `kredi
  hipotekare`, `kredi konsumatore`, `depozita me afat` — reduces the
  substitution at source. Configuration, not architecture. Arm B needs its own
  equivalent.
- **Disclosure-side:** always name the resolved product in the first clause
  (not confidence-gated) — this is option (c), and it is more valuable on voice
  than on screen because the user is already speaking.

## Recommendation

Implement **(c) as an always-on mitigation** (cheap, catches the visible-mismatch
case on both text and voice) **plus (b) as the deterministic gate** on the
dimensions that have actually failed (segment at minimum; product/metric/
currency already clarify so they need no gate). **Skip (a)** unless the product
call decides the +929 ms/$0.001/turn is worth catching cases (b) cannot see —
and even then, (a) still misses the ASR route. Decision is the human's.
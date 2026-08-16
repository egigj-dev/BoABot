# RAG / Callcenter Audit Report — BoABot

Date: 2026-08-14

## Executive summary

The reported symptom — most benign tariff/rate questions coming back as
`HANDOFF_MESSAGE` (callcenter.py:35-38) — **could not be reproduced** against
either the current working tree or the exact commit (`c97a672`) that
`ARM_AB_LIVE_EVAL_2026-08-14.md` claims to have tested. Every transcript quoted
in that eval as a "handoff" failure was replayed here through:

1. `callcenter.decide()` directly (both current tree and a clean checkout of
   `HEAD` in `/tmp/head_test`),
2. `rag.retrieve_evidence()` against the live Postgres/pgvector corpus,
3. the full `api.generate_turn()` path with a real OpenRouter call, and
4. the currently-running `uvicorn api:app` process on port 8000.

All four reproduction methods returned correct `answer` outcomes with correct
figures for every "failing" transcript (e.g. Raiffeisen at-call deposit →
0.25%, Banka Tirana 3‑month deposit → 0.70%, Intesa Sanpaolo admin fee →
0.02%). The eval file's own "Reproducibility and scope" section is internally
inconsistent — its two cited SHA‑256 hashes are 65–66 hex characters, not the
64 required for SHA‑256, and neither matches `git show HEAD:api.py` /
`git show HEAD:rag.py` computed here. **The eval evidence should not be
treated as proof of a currently-live router or retrieval bug**; at most it is
evidence that *some* run produced a wave of generic-exception handoffs.

That said, the audit found one **real, confirmed design flaw** that is fully
sufficient to explain how a transient failure (an LLM provider hiccup, a
FidelityGuard rejection, a stale process, a DB blip) would present, in logs
and transcripts, as an indistinguishable mass "handoff" event exactly like
the one in the eval file — because the code path for genuine safety handoffs
and the code path for unrelated system errors emit the **exact same
string and the exact same `Outcome.HANDOFF`**, with no distinguishing telemetry.
This is root cause #1 below, and is what this audit fixed.

The audit also found that the specific router false-positive path the brief
hypothesized (`_is_contextual_public_pricing_question` failing to override the
NN probe) is **already fixed in the uncommitted working tree** (callcenter.py:172-185)
and already has passing test coverage
(`voice/tests/test_callcenter_policy.py::test_public_pricing_followup_is_not_misrouted_to_handoff`).
That fix is real and worth keeping/committing, but it was not the cause of
the specific eval failures reproduced here, since the NN probe's nearest
neighbour for every tested transcript was already a **negative** exemplar
(`handoff_score == -inf`) even without the override, at both `HEAD` and the
current tree.

## Traced decision path(s)

Example: `"Sa është interesi për 1 depozitë pa afat raiffeisen."` (callcenter.py:212 `decide`)

| Branch (callcenter.py) | Result | Why |
|---|---|---|
| 1. `input_gate` (trust.py:109) | pass | no control chars / encoding / injection patterns |
| 2. `_is_repeat` (:132) | pass | no repeat phrase |
| 3. `_SECRET_FAST_RE` (:108) | pass | no pin/cvv/cvc/otp token |
| 4. `_needs_missing_context_clarification` (:141) | pass | no "kjo/këtë rregullore" |
| 5. `_is_explicitly_unsupported` (:149) | pass | no banned phrase |
| 6. `is_ambiguous_card_maintenance` (:188) | pass | no "kart"+"mirembajt" |
| 7. `is_business_deposit_question` (trust.py:121) | pass | no business term |
| 8. `_redact_pii` (:126) | pass | no email/phone/long-number pattern |
| 9. `len < 2 words` (:242) | pass | 8 words |
| 10. NN probe `_probe_score` (:202) | **pass** | nearest neighbour is a *negative* exemplar → `-inf`, never reaches `_HANDOFF_THRESHOLD=0.04658478` |
| 11. `len < 3 words` (:252) | pass | 8 words |
| → `Decision(outcome=None, question=...)` | **proceeds to RAG** | |

Verified with a direct call (`python3 -c "import callcenter; callcenter.decide(...)"`):
`outcome=None score=-inf` for all of: qa-001, qa-002, qa-003, qa-006, qa-009,
qa-011, qa-012, qa-014 from the eval's "Expected-answer figure mismatches"
table — both on the current tree and on a clean `HEAD` checkout.

`retrieve_evidence()` (rag.py:185) for the same questions returns strong hits
well above `MIN_RELEVANCE_SCORE=0.50` (trust.py:14), e.g. `rate_0001` @ 0.611
for the Raiffeisen deposit question, `rate_0084` @ 0.722 for the Intesa
admin-fee question — no `wrong_chunk_family` or `weak_retrieval` refusal.
`generate_turn()` (api.py:371) then streams a grounded answer and returns
`outcome=answer, handoff=False` end-to-end, confirmed against the live DB and
a real OpenRouter completion.

**Conclusion: for the specific transcripts in evidence, no router branch and
no retrieval/trust gate misfires.** The failure the eval observed must have
originated downstream of both (LLM call / streaming / fidelity verification)
or in the eval harness itself, not in `decide()` or `trusted_hits()`.

## Root causes, ranked by impact

### 1. [CONFIRMED — fixed] Genuine safety handoff and generic system error are indistinguishable

**File/lines:** `api.py:491-512` (both the `except RAGError` and the bare
`except Exception` handlers), `api.py:521` (telemetry `finally` block).

```python
except RAGError:
    ...
    outcome = Outcome.HANDOFF
    handoff = True
    ...
    yield done_event(outcome, handoff=True)
except Exception:
    ...
    outcome = Outcome.HANDOFF
    handoff = True
    ...
    yield done_event(outcome, handoff=True)
```

Any of the following will produce this fallback and therefore emit the exact
same `HANDOFF_MESSAGE` ("Për sigurinë tuaj... Mos ndani PIN-in...") as a
genuine PIN/OTP-disclosure handoff:

- an OpenRouter timeout/5xx/network error (`rag.py:114-121 _post`,
  `api.py:68-111 stream_answer`),
- a malformed streaming chunk (`api.py:89-104`),
- **any** `FidelityGuard.verify_sources` rejection of a *correct* sentence
  (`api.py:119-150 authorized_sentences`) — this is a live, generic-purpose
  "second evidence gate" per `DEVELOPMENT_ISSUES.md` Arm A §4/§5, and by
  design it can suppress correct output, not just fabricated output,
- any other unexpected exception in the generation path.

**Impact:** this is exactly the failure signature reported — text that reads
as a deliberate safety refusal, for a benign question, at scale — and it
requires *no* router or retrieval bug at all, just a burst of transient
failures (a flaky provider, one bad model completion under load, a
short‑lived DB hiccup). It also means production telemetry cannot currently
tell "policy handoff" (`decide()` returned `HANDOFF` deterministically) apart
from "system error masquerading as handoff" — both log
`outcome=handoff, handoff=true` with no distinguishing field
(`api.py:519-537`). This is very likely why the eval harness (and anyone
reading `ARM_AB_LIVE_EVAL_2026-08-14.md`) concluded "the router is
mis-handoffing benign questions" when the router itself, replayed in
isolation, is healthy.

**Fix applied:** see "Fixes applied" below.

### 2. [Real, already mitigated, uncommitted] NN handoff probe had no whitelist override

**File/lines:** `callcenter.py:159-185` (`_is_public_pricing_question`,
`_is_contextual_public_pricing_question`), used at `callcenter.py:247-249`.

The working tree (uncommitted, `git diff HEAD -- callcenter.py`) already adds
a whitelist: if a question (or, for an elliptical follow-up starting with
"po/dhe/kurse", a recent turn) matches `_is_public_pricing_question` (a
price-intent term like "sa është"/"komision"/"tarifë" plus a public-product
term like "bank"/"kartë"/"kredi"/"depozit"), the NN probe's `HANDOFF` verdict
is overridden and the question proceeds to RAG. This is tested by
`voice/tests/test_callcenter_policy.py::test_public_pricing_followup_is_not_misrouted_to_handoff`.

**Assessment:** this is a legitimate defense-in-depth fix for the class of
bug the brief hypothesized (a public pricing question sitting near a
handoff-positive neighbour), but it did **not** fire for any of the eval's
"failing" transcripts, because those transcripts' nearest neighbour was
already negative (`score=-inf`) without the whitelist. The underlying probe
design is still worth flagging as fragile (see below), but this specific fix
is not the reason the previously-reported symptom would have occurred for
the transcripts in evidence. **Recommendation: commit this change** — it is
low-risk, tested, and closes a real gap for phrasings that do land near a
positive exemplar.

### 3. [Design risk, not proven to have fired] NN probe is a fragile k=1 classifier

**File/lines:** `callcenter.py:113-121` (probe load/validation),
`callcenter.py:202-209` (`_probe_score`).

- `k=1` nearest-neighbour on 233 exemplars (191 positive / 42 negative) means
  a single frozen embedding's dot product decides handoff for anything within
  margin `0.04658478` of the closest positive vs. closest negative. With only
  42 negative exemplars, coverage of the "normal question" embedding space is
  thin; any benign phrasing whose nearest neighbour happens to be positive
  (independent of the whitelist in #2, e.g. a benign question that also
  mentions a card/PIN in passing) gets an unconditional handoff.
- There is no retraining/expansion process visible in the repo beyond
  `eval_handoff.py` — the threshold (`0.04658478498458862`) is a frozen,
  point-in-time value with no confidence interval attached.

**Recommendation:** widen the negative exemplar set (`handoff_split*.json`,
`handoff_phrases.jsonl`) with more benign pricing/product phrasings, and/or
move from `k=1` to `k=3`+majority vote to reduce single-neighbour brittleness.
Not applied here — this needs retraining data, not a code change, and is out
of scope for a "low-risk, verifiable" fix.

### 4. [Reviewed — not a bug in the current tree] `trusted_hits` / rate-family gate

**File/lines:** `trust.py:135-157`, `rag.py:185-268`.

At `HEAD`, `retrieve_evidence` called `retrieve(query, k=5, ...)` with no
reranking (`rag.py` diff). For short tariff queries this occasionally placed
the correct `rate_*` row outside the top 5, which combined with the
`wrong_chunk_family` gate (`trust.py:153-156`, "a rate-family question with
no `rate_*` hit in the candidate set is refused") could plausibly refuse a
question that *does* have a correct rate answer. This is a real theoretical
risk of the gate design.

The uncommitted working tree already mitigates this: `candidate_k=max(k,10)`
(rag.py:198), a `commercial_aliases` filter that keeps only `rate_*` hits
once a bank name is mentioned (rag.py:210-220), and `_rerank_hits` blending
lexical overlap into the ranking (rag.py:84-112). Verified empirically: every
tested tariff question returns its correct `rate_*` row as the top hit with
score ≥ 0.58, well above `MIN_RELEVANCE_SCORE=0.50`, and `trusted_hits`
allows all of them. **No case was found, in either `HEAD` or the current
tree, where this gate incorrectly refused a question that has a correct rate
answer** — but this is because of the working-tree retrieval widening, not
because the gate is inherently safe. Recommend committing the `rag.py`
retrieval-widening change alongside the callcenter whitelist (item #2); they
are complementary defenses (the gate in trust.py is unchanged and still
depends on retrieval finding the right row).

### 5. All 11 router branches — reviewed for over-broad matches

| # | Branch | File:line | Verdict |
|---|---|---|---|
| 1 | `input_gate` | trust.py:109 | OK — narrow (control chars, base64, prompt-injection phrases) |
| 2 | `_is_repeat` | callcenter.py:132 | OK — explicit phrase list |
| 3 | `_SECRET_FAST_RE` | callcenter.py:108-111 | OK, but see finding below (word-boundary false-negative on ASR joins, not false-positive) |
| 4 | `_needs_missing_context_clarification` | callcenter.py:141 | OK — narrow, only fires on empty history |
| 5 | `_is_explicitly_unsupported` | callcenter.py:149-156 | **Flag:** `"tatimet"` is a bare substring match (no word boundary/phrase requirement) — any question that happens to contain "tatimet" ("taxes") anywhere, even as an aside in an otherwise answerable banking question, is refused as unsupported. Low likelihood in practice (no such case in the eval transcripts) but broader than the other two phrases in the same check, which are full sub-phrases ("banka me e mire", "deklaroj qirane"). |
| 6 | `is_ambiguous_card_maintenance` | callcenter.py:188-195 | OK — requires both "kart" and "mirembajt" |
| 7 | `is_business_deposit_question` | trust.py:121-133 | OK — requires an explicit business term, tested |
| 8 | `_redact_pii` → HANDOFF | callcenter.py:126-130, 238-240 | **Flag:** `_LONG_NUMBER_RE` (trust.py — actually callcenter.py:124) matches any 8-18 digit run. A legitimate question that includes an account number, policy number, or long reference ID for context would be redacted and handed off even though it isn't a PIN/OTP disclosure. Not observed in the eval transcripts (qa-033's phone number redaction was correct behavior), but broader than it needs to be — no distinction between "caller pasted a long number" and "caller disclosed a secret." |
| 9 | `len(question) < 2 words` | callcenter.py:242-243 | OK |
| 10 | NN probe | callcenter.py:245-251 | See #2/#3 above |
| 11 | `len(question) < 3 words` | callcenter.py:252-254 | OK |

Neither #5 nor #8's flagged risks were observed to fire on any transcript in
the eval evidence; they are noted as latent risk, not confirmed causes.

## Router-side vs. retrieval/grounding-side

- **Router-side (callcenter.py):** no confirmed false positive on the cited
  evidence. One real, already-mitigated gap (NN probe whitelist, #2/#3), two
  latent risks not observed to fire (#5, #8).
- **Retrieval/grounding-side (rag.py/trust.py):** no confirmed false
  negative on the cited evidence in the current tree; a real theoretical
  weakness at `HEAD` (narrow `k=5`, no rerank) that the working tree already
  fixes.
- **Neither:** the one confirmed, reproducible design flaw that fully
  explains the reported symptom class is in **api.py's error handling**,
  which sits downstream of both the router and retrieval and conflates
  "policy handoff" with "system error" under one indistinguishable message
  and outcome. This is not router or retrieval misrouting — it's a
  diagnosability/observability gap that makes *any* transient failure look
  exactly like a deliberate security handoff.

## Fixes applied

**api.py:** added a `handoff_reason` telemetry field (`"policy"` when
`decide()` itself returned `Outcome.HANDOFF`, `"system_error"` when the
fallback except-blocks fired) to the structured log line in `generate_turn`'s
`finally` block. This does **not** change the SSE contract, the `Outcome`
enum, or any client-visible behavior (still conservative: unknown failures
still hand off to a human) — it only makes the two failure classes
distinguishable in logs/telemetry, closing root cause #1 without touching
caller-facing behavior or the voice pipeline's outcome contract
(`voice/schema1.py:307`, `voice/schema2.py:423` only check
`outcome in {"clarify","handoff"}`, unaffected).

Test added: `voice/tests/test_api_turn.py` — exercises `generate_turn`
end-to-end with `decide`/`retrieve_evidence`/`stream_answer` monkeypatched,
covering: (a) a policy handoff from `decide()` logs `handoff_reason=policy`,
and (b) a `RAGError` raised mid-stream logs `handoff_reason=system_error`
while still emitting `HANDOFF_MESSAGE`/`Outcome.HANDOFF` to the caller
unchanged.

**Not applied (out of scope / needs data or product judgment):**
- Widening the NN probe's negative exemplar set or moving off `k=1` (#3) —
  requires retraining data, not a code change.
- Narrowing `"tatimet"` and `_LONG_NUMBER_RE` (#5, #8) — no confirmed false
  positive in evidence; narrowing on spec risk alone could reopen the PII/
  out-of-scope hole they exist to close. Flagged for product decision.
- Committing the existing uncommitted `callcenter.py`/`rag.py`/`trust.py`
  changes (#2, #4) — these are real, tested, verified-working improvements
  already sitting in the working tree; recommend committing them as their
  own change, separate from this audit's telemetry fix, since they were
  authored by a prior session and deserve their own review/commit message.

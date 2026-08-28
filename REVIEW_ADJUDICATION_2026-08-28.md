# BoABot — Adjudication of Round 4 (final corrections before P0A implementation)

Date: 2026-08-28. Verdict: accept all four corrections; two come with a
verification bonus (one confirms the critic's example is real in the corpus,
one closes the comparison_key list). The P0A contract below is the agreed
spec for implementation.

## 1. MATCHED vs CERTIFIABLY_CONSUMED — ACCEPT, example verified REAL

Correct. I ran the actual matcher plus the corpus:

    _has_term("kreditore", "kredi") -> True      (stem over-consumption)
    _has_term("kredisë",   "kredi") -> True      (legit inflection)
    _has_term("kredive",   "kredi") -> True
    chunks containing "kreditore": 28
    chunks containing "kredisë"/"kredive": 760

So the failure mode the critic describes is not hypothetical: "kreditore" is a
real Albanian word, present in the corpus, and the stem matcher would both
parse it as family=credit AND consume it during coverage — turning a lexical
false positive into a CERTIFIED FULL interpretation. That is precisely the
mistake that must not be certified.

The fix:
- MATCHED (parser, recall-oriented): keep the permissive stem matcher — it is
  what makes the lexical first stage hit "kredise"/"kredive".
- CERTIFIABLY_CONSUMED (coverage, precision-oriented): a strictly bounded
  inflected-form list per vocab term. For the credit family this is
  {"kredi","kredie","kredinë","kredive"}; the same closed list the parser
  already carries in PRODUCT_TERMS ("kredi","kredia","kredise") becomes the
  certifiable set, made complete and shared.
- Any residual token whose matcher hit was stem-only => PARTIAL, no
  structured assertion. Fail-closed beats self-consistent.

## 2. Frame lifecycle: PRESERVE / REPLACE / CLEAR — ACCEPT

"Any intervening non-structured outcome kills the frame" was too aggressive.
Natural filler (thank-you, "përsërite", harmless meta) must not destroy the
user's active context. The ternary mapping is deterministic and needs no LLM:

    successful structured turn              -> REPLACE (new frame)
    smalltalk / repeat / meta_followup /
    negation (info-level)                   -> PRESERVE
    dense answer (other domain) /
    personal_record / incident /
    account action / catalog question       -> CLEAR

This preserves the deposit->credit-registry->"po për kredi?" behavior (the
registry turn is a CLEAR) while not punishing "Faleminderit" / "Përsërite".
The existing DecisionReason is exactly the input to this mapping — no new
classification layer.

## 3. Two distinct incomplete states: UNREPRESENTED_SEMANTICS vs
   INSUFFICIENT_COMPARISON_DIMENSIONS — ACCEPT

Correct, and important: a generic "PARTIAL" rule would route comparison
questions into dense RAG, where the LLM could make exactly the comparison the
structured system refused to make. The three-state contract is right:

    UNREPRESENTED_SEMANTICS          -> dense retrieval
      ("kredi për udhëtime" — residual qualifier)

    INSUFFICIENT_COMPARISON_DIMENSIONS -> CLARIFY
      ("Cila bankë ka depozitën më të mirë?" — missing currency/term/band)

    FULL_STRUCTURED_INTENT           -> structured answer

Note: for the CLARIFY state, the clarification should offer the concrete
missing dimensions (currency, term, amount_band) — that turns the refusal
into a guided follow-up, which is strictly better UX than the existing
generic NO_EVIDENCE.

## 4. comparison_key: fee_event added, metric-dependent required dimensions —
   ACCEPTED, verified fee-event rows exist

Verified: 17 of 119 rate-table rows carry a fee-event term
(administrim / shlyerje e parakohshme / pagesë e vonuar) in category/item —
so ATM withdrawal vs annual card fee vs early-repayment fee are all real,
distinct rows that could otherwise land in one comparison group. Final key:

    comparison_key = (
        family, product, metric, fee_event,
        customer_segment, currency,
        term_months, amount_band, value_type,
    )

with rankable = all(required_dimension_is_resolved(d)
                    for d in dimensions_required_for(intent.metric))
and dimensions_required_for(metric) defined per metric:
    interest_rate -> {currency, term_months, amount_band, customer_segment}
    fee          -> {fee_event, customer_segment, currency}
    penalty      -> {fee_event, customer_segment}

Nullable dimensions remain legal when they genuinely don't apply (e.g. no
fee_event for a deposit interest comparison).

## One addition the critic requested: personal_record negative/interrogative tests

Agreed, and they belong in the eval set before settling precedence, because the
existing negation_statement floor (callcenter.py:354) fires before the
personal-record preflight would. Test forms:

    "nuk kam kredi."                -> negation/meta (current behavior, likely
                                       correct)
    "nuk kam kredi, apo jo?"        -> personal_record (negative proposition
                                       ABOUT the record, not a meta statement)
    "a nuk kam kredi?"              -> personal_record
    "a figuroj pa kredi ne regjister?" -> personal_record

The distinction is grammatical/intent-based: a fact-doubt about the registry
entry is a lookup request; a plain "nuk kam kredi" in answer to a prior
question is dialog filler. The preflight must match the former and cede the
latter, and the precedence must be established by these tests before
finalizing the route order.

## Agreed P0A contract (to be written into comparison.py)

Structured results may be asserted only when:

1. All material query semantics are represented by the structured frame.
2. Missing slots have explicit semantics: EXPLICIT / INHERITED / WILDCARD /
   MISSING.
3. Wildcards originate only from explicit breadth intent, never parser
   failure.
4. Elliptical follow-ups inherit only from a valid preserved frame.
5. Comparisons execute only when all metric-specific comparability
   dimensions are resolved.
6. Unrepresented qualifiers fall through to document retrieval rather than
   being silently generalized.

Sharpened one-line invariant:

  The structured seam may generalize only what the user explicitly left
  broad; it may never generalize what the parser failed to understand.

## Implementation order (unchanged from P0A, now with the corrections folded in)

P0A: SlotState (explicit/inherited/wildcard/missing) + certifiable-consumption
coverage (MATCHED vs CERTIFIABLY_CONSUMED, bounded inflection lists) +
frame lifecycle (PRESERVE/REPLACE/CLEAR, outcome-mapped) + reparse after
rewrite + the two-state incomplete contract (dense vs CLARIFY) +
personal_record preflight with negative-form tests.
Then: rerun the conversational suite (the existing harness) — must_pass for
the contamination sequences and the personal-record forms — before touching
any P1 presentation work.

P1/P2 (deferred until P0A is green): institution register (identity-only),
temporal metadata (updated_at vs valid_from vs reference_period),
summary-first renderer with comparison_key + segment/currency slots,
citation dedup + empty-URL guard, trace_flags/DecisionEvent split, fidelity
counters + dependency-aware cluster drop, flag matrix, contamination evals.
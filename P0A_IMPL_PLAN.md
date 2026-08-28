# BoABot P0A — Read-only implementation plan (frozen 2026-08-28)

REVISION 2 (2026-08-28, user-approved with modifications): row-level
currency/customer_segment materialization with frozen conservative
vocabularies; two-tier personal_record gate; FULL vs rankable pinned as
separate concepts. Ready to hand Codex commit 1.

Goal: eliminate semantic over-resolution. A structured result may be asserted
only when the entire material meaning of the query is represented
(EXPLICIT/INHERITED/WILDCARD/MISSING semantics, certifiable coverage, frame
lifecycle, comparability contract, reparse, personal-record boundary).
NO code changes until this plan is approved. All line anchors verified against
the working tree (callcenter.py 798 / comparison.py 971 / rag.py 297 /
api.py 745).

Invariant (to be written into comparison.py docstring):

  The structured seam may generalize only what the user explicitly left
  broad; it may never generalize what the parser failed to understand.

---

## 1. comparison.py — explicit slot semantics

### 1A. SlotState + ResolvedSlot (replace bare-None semantics)

- New: `SlotState(Enum)`: EXPLICIT | INHERITED | WILDCARD | MISSING.
- Wrap the slots whose absence currently changes resolution:
  product, metric, fee_event, value_type, term_months, amount_band.
- **CORRECTION (verified): customer_segment and currency DO NOT EXIST as
  slots.** They must be ADDED in P0A as BOTH extractor schema AND row-level
  materialized values (extractor-only would leave the resolver nothing to
  compare against). Frozen conservative vocabularies — unknown wording ->
  UNREPRESENTED_SEMANTICS, never guessed:
    - currency: ALL: lek, leke, lekë | EUR: euro, eur | USD: dollar, dollare,
      dollarë, usd.
    - customer_segment: individual: individ, individë, personal, person fizik
      | business: biznes, biznese, kompani, shoqëri, person juridik.
- **Row materialization (rate_tables.jsonl):** add per-row fields
  `currency` and `customer_segment`, derived DETERMINISTICALLY from
  authoritative identity — the extractor must never infer the row's values:
  - currency from the scraped BoA page identity (BoA publishes rate pages per
    currency). PRE-COMMIT INVENTORY: confirm the currency of each of the 5
    source tables (verified URL families: Komisionet për individë 53 /
    për biznese 28 / Normat e interesit të depozitave 21 / NEI bizneset 11 /
    NEI individë 6); default ALL unless a page is currency-specific. Row text
    carries no currency marker today (verified: deposit lines end
    "LLOGARI PAGESE: 0.02").
  - customer_segment from the SOURCE TABLE identity: "…për individë" /
    "Depozita për individë" -> individual; "…për biznese" -> business
    (reuse `_row_slots` source mapping, comparison.py:299-358, as the single
    source of truth).
  - loader stays backward-compatible (missing fields -> None).
- Invariant: `value=None` must NEVER imply wildcard again. Only
  `state == WILDCARD` does.
- **family stays a plain field (NOT wrapped) in P0A.** Verified: family is
  derived and consumed by availability/render only (PRODUCT_FAMILY /
  _resolve_family / render_availability_answer); it is not a resolution
  dimension in resolve_rate_rows. Revisit only if breadth semantics need it.

### 1B. merge_frames(current, previous) -> RateIntent (pure, unit-testable)

- Priority: EXPLICIT > INHERITED > WILDCARD > MISSING.
- Explicit current value always wins; eligible MISSING current slots may
  inherit from a PRESERVED frame; explicit broadness (see wildcard inventory
  below) becomes WILDCARD; parser failure stays MISSING; an INHERITED slot
  never acquires wildcard behavior.
- Wildcard construction sites INVENTORY (verified, must be re-expressed in
  SlotState terms):
  - comparison + product w/o metric -> metric=None (comparison.py:685-693)
    is DELIBERATE family-wide breadth -> metric=WILDCARD.
  - comparison + metric w/o product -> product=None (695-705) -> WILDCARD.
  - availability branch (654-667): product=None/metric=None by construction;
    availability resolves on family only -> product/metric=MISSING (not
    wildcard); availability adds its own FULL-coverage gate (section 2).
- resolve_rate_rows (372-398): replace `product is not None` /
  `metric is not None` wildcard checks with
  `state == WILDCARD` semantics; MISSING on a slot the branch does not use
  (e.g. product in an availability ask) is legal and must not gate.

## 2. comparison.py — coverage certification

- Hybrid extraction result gains audit fields: consumed_phrases,
  unresolved_qualifiers (model-reported, for auditing only — never the
  authority).
- Two concepts:
  - MATCHED (parser, recall-oriented): keep `_has_term` stem matcher.
  - CERTIFIABLY_CONSUMED (coverage, precision-oriented): bounded closed-form
    list per vocabulary entry. The product/metric/fee_event/value_type term
    tuples already ARE closed lists (verified, comparison.py:76-112) — they
    become the certifiable sets; extend to full known inflections (kredi,
    kredia, kredie, kredisë, kredive — NOT kreditore).
- Rule: a stem-only match (kredi -> "kreditore", which is REAL in the corpus:
  28 chunks verified) may assist parsing but must NOT certify coverage.
  Residual token not certifiably consumed and not in the allowed residue set
  -> UNREPRESENTED_SEMANTICS.
- Allowed residue set (explicitly NOT residuals): _QUERY_STOPWORDS (21-24),
  discourse/filler (cilat, prej, tyre, ofrojne...), interrogative words,
  already-consumed slot terms, bank labels. Reuse the existing machinery
  (verified: _has_term, _matching_slots, _matching_products, bank_scope,
  term regex at 721, amount-band regex at 725-728) — no second NLP subsystem.

## 3. comparison.py — three resolution states (no overloaded PARTIAL)

- `StructuredIntentStatus`: FULL_STRUCTURED_INTENT | UNREPRESENTED_SEMANTICS |
  INSUFFICIENT_COMPARISON_DIMENSIONS.
- Routing contract:
  - FULL_STRUCTURED_INTENT -> structured resolver.
  - UNREPRESENTED_SEMANTICS -> fall through to dense (Decision(outcome=None)
    with trace event unresolved_qualifier_detected). Note: falls through the
    EXISTING missing_product/missing_key fall-through seam (callcenter.py:642),
    which already defeats the fee-table-vetoes-corpus bug.
  - INSUFFICIENT_COMPARISON_DIMENSIONS -> CLARIFY, never dense. New
    DecisionReason.COMPARISON_DIMENSIONS_MISSING. Clarify message names the
    missing dimensions: "Për ta krahasuar saktë, më duhet afati dhe monedha e
    depozitës." (not generic NO_EVIDENCE).
- **FULL_STRUCTURED_INTENT != rankable (frozen distinction).** FULL means the
  query's meaning is fully represented; listing/filtering over a FULL query is
  valid even when ranking is not. Ranking is a SEPARATE gate (section 4).
  Pinned examples: "normat për depozita 12 muaj në LEK" -> FULL, listing
  valid, ranking NOT implied; "cila bankë ka normën më të mirë për depozita
  12 muaj në LEK?" -> comparison: still check segment/amount-band, CLARIFY
  if required dimensions unresolved. Tests pin this so nobody later equates
  FULL with "safe to rank".
- Examples pinned: "kredi për udhëtime" -> UNREPRESENTED_SEMANTICS;
  "cila bankë ka depozitën më të mirë?" -> INSUFFICIENT...; "norma për
  depozitë 12 muaj në LEK" -> FULL (requires the currency slot from 1A).

## 4. comparison.py — comparability contract

- comparison_key = (family, product, metric, fee_event, customer_segment,
  currency, term_months, amount_band, value_type).
- Required dimensions PER METRIC (rankable = all resolved):
  - interest_rate: currency, term_months, amount_band, customer_segment
  - fee: fee_event, customer_segment, currency
  - penalty: fee_event, customer_segment
- Before ranking: missing required dimension -> INSUFFICIENT_COMPARISON_
  DIMENSIONS with the specific missing dimensions surfaced.
- fee_event in the key is load-bearing (verified: 17/119 rate rows carry
  fee-event terms in category/item; ATM vs annual card vs early-repayment
  fees are distinct rows that could otherwise collide).
- Rankable additionally requires banks resolved to a named set
  (bank_scope="named", banks non-empty) OR rows carrying per-bank lines.
  NOTE (verified): NEI tables ('Normat nominale dhe NEI për individë/
  bizneset', 17 rows) carry PRODUCT labels per line, NOT bank names — they
  can be filtered/listed but never ranked across banks; the gate treats
  unbanked rows as non-rankable. Deposit/commission rows are bank-labeled
  and rankable.

## 5. callcenter.py — persisted structured frame + lifecycle

- Session (callcenter.py:137-144) gains: last_structured_frame,
  last_structured_turn_id. (Domain derivable from the frame type; an explicit
  domain field is optional.)
- `ContextEffect(Enum)`: PRESERVE | REPLACE | CLEAR. Centralized mapping from
  existing Outcome/DecisionReason (no new classification):
  - REPLACE: successful structured answer (catalog_exact_hit / structured
    render path).
  - PRESERVE: smalltalk, repeat, meta_followup, negation-info, dialog filler.
  - CLEAR: dense answer (another domain), personal_record, incident,
    account_action, catalog.
- Outcome-based invalidation, NOT turn-count. Verified semantics: the
  deposit -> credit-registry -> "po për kredi?" sequence CLEARs on the
  registry turn (dense answer) and the third turn falls through correctly.
- Lifecycle logic centralized in one helper, not scattered across exits.

## 6. callcenter.py — elliptical follow-up path

- Flow: raw turn -> lexical/hybrid parse -> is elliptical? -> merge eligible
  slots from preserved frame -> if resolvable: structured path -> else:
  rewrite -> REPARSE rewritten query (api.py, see 9) -> apply frame where
  still appropriate -> structured / clarify / dense.
- **CRITICAL VERIFIED NUANCE — why inheritance must fire BEFORE the
  needs_rewrite gate:** needs_rewrite returns False when a later word is
  capitalized or a digit is present (rag.py:146-149: has_specific_reference).
  So on the router-OFF path, "Po Credins?" (capital C) and "Po 24?" (digit)
  are NEVER rewritten today — decide-time frame inheritance is the ONLY fix
  for those shapes. The fused router (ON) covers them via LLM rewrite.
- "po për kredi?" currently parses not_rate (no price term) — verified. The
  first parse must expose an elliptical structured candidate (product=credit
  via bare family terms) OR the rewrite must run before the definitive
  not_rate. Do NOT force inheritance into a parse that already discarded all
  structured semantics.
- _structured_rate_decision (631) and decide (727-730) gain access to the
  session frame (add param; call site api.py:522-526).

## 7. callcenter.py — personal-record preflight

- Deterministic, terminal, router-agnostic (router OFF/ON parity — the
  router is opt-in, a capability boundary must not be flag-dependent).
- Two-tier deterministic gate (high-precision; "për mua" alone must NOT
  fire — "çfarë thotë rregullorja për mua si garant?" is regulatory info):
  - STRONG standalone (fire alone): "në emrin tim"; "të dhënat e mia";
    "raporti im i kredimarrësit"; "a figuroj"; "a kam kredi aktive";
    "a kam kredi … në emrin tim".
  - CONTEXT-DEPENDENT (require co-occurring registry vocabulary):
    "për mua"; "rreth meje"; "informacionin tim" — alongside "regjistri i
    kredive", "kredi aktive"/"kredi problematike", "raport kredimarrësi",
    "të dhëna personale".
- Response: capability boundary (no access to the Credit Registry record),
  official retrieval procedure, NO legal-advice refusal, NO invented
  escalation. New DecisionReason.PERSONAL_RECORD_CAPABILITY_BOUNDARY,
  Outcome.ANSWER.
- Placement: AFTER the legal-advice floor (legal floor already wins on
  "a duhet ta paguaj...") and BEFORE the structured seam (so "kredi" in a
  personal-record turn cannot become family=credit availability). Cede
  account-action / incident patterns like _structured_rate_eligible does.
- **VERIFIED CORRECTION to the plan's assumption:** the negation floor
  (_NEGATION_STATEMENT_RE, callcenter.py:360-363) matches ONLY
  kart\w* | llogari\w* | pyetje — "kredi" is NOT in its vocabulary. So
  "nuk kam kredi", "a nuk kam kredi?", "nuk kam kredi, apo jo?" all pass the
  floor today and reach the preflight naturally. Do NOT extend the negation
  vocabulary with kredi in P0A; the precedence tests (section 8) pin the
  desired routing, and the plain "nuk kam kredi." would then flow to the
  router/meta path as today. If a later change adds kredi to negation, the
  interrogative/apo-jo carve-out is REQUIRED before landing.

## 8. callcenter.py — terminal reasons vs processing events

- Keep DecisionReason pure (terminal exit causes only). Add
  `DecisionEvent(Enum)` + `trace_flags: frozenset[DecisionEvent]` default
  field on Decision (frozen dataclass, 124-134; new kw field with default =
  backward compatible).
- Events: context_inherited, query_rewritten, structured_lookup,
  unresolved_qualifier_detected, fidelity_sentence_drop.
- New reasons this pass: COMPARISON_DIMENSIONS_MISSING,
  PERSONAL_RECORD_CAPABILITY_BOUNDARY. (context_inherited is an event, never
  a reason.)

## 9. rag.py / api.py — post-rewrite reparse

- Location: api.py generate_turn, after standalone_query is computed
  (line 559) and before is_ambiguous_card_maintenance (560) /
  retrieve_evidence (574). When decision.rate_intent is None AND a rewrite
  happened (or the query is elliptical), reparse standalone_query via
  parse_rate_intent_hybrid + coverage; if FULL -> pass rate_intent to
  retrieve_evidence and judge. Embedding reuse (api.py:569-573) is unaffected
  — reparse does not change the query text.
- No recursion: needs_rewrite is called once per turn (556); the reparse
  helper PARSES ONLY, never rewrites. Parse phase 1 (decide) -> rewrite once
  -> parse phase 2 (api) -> retrieval. Not a loop.
- Verified outcome: "po per kredi?" -> needs_rewrite fires ("po" in
  _ELLIPTICAL_LEADS, rag.py:86-89) -> rewrite produces "cilat jane normat e
  interesit per kredi?" -> reparse -> product=credit, metric=interest_rate
  -> structured resolver fires instead of dense.

## 10. rag.py — generation prompt (one fidelity-friendly rule only)

- Add to SYSTEM (rag.py:30-65): prefer factual sentences with explicit
  subjects; avoid cross-sentence pronoun/deictic dependence ("kjo", "ajo",
  "për këtë arsye") between independently verifiable factual sentences.
  Do NOT weaken the existing figure+institution verifiability rule.
- Full dependency-aware fidelity (cluster drops, drop-ratio DEGRADED) is P2,
  out of scope here.

## 11. api.py — minimal surface

- CLARIFY (comparison_dimensions_missing) keeps structured metadata
  internally: {outcome: clarify, reason, missing_dimensions: [...]} so the
  frontend can later render richer UI; today the SSE text message carries it.
- trace events exposed in debug/telemetry output only; do not ship internal
  parse details to ordinary callers (existing source() policy: only
  id/doc/article/url/issuer cross the bridge).

---

## Acceptance tests (freeze BEFORE implementation)

Where they land (existing files unless noted):

Coverage (test_comparison_structured.py / test_structured_extractor.py):
- "kredi për udhëtime", "kredi për makinë" -> never generic credit
  availability (UNREPRESENTED_SEMANTICS -> dense).
- "kreditore ..." -> NOT FULL solely from kredi stem match.
- "kredisë", "kredive" -> remain certifiable (valid inflections).
- "norma për depozitë 12 muaj në LEK" -> FULL (after currency slot).

Inheritance (new test_p0a_representation.py or test_comparison_structured.py):
- "normat e depozitave?" -> "po për kredi?" inherits metric, replaces product.
- "depozita Credins 12 muaj?" -> "po 24?" inherits bank/product/metric,
  replaces term.
- "komisionet Credins?" -> "po BKT?" inherits metric/family, replaces bank.
- "normat e depozitave?" -> "faleminderit" -> "po Credins?" frame survives.
- "normat e depozitave?" -> "si funksionon regjistri i kredive?" ->
  "po për kredi?" frame does NOT survive.

Wildcard safety (unit test against resolve_rate_rows):
- "më trego të gjitha komisionet" -> wildcard permitted (explicit breadth).
- Unresolved product/metric (parser omission) -> NEVER wildcard.

Comparability (test_comparison_structured.py):
- "normat për depozita 12 muaj në LEK" -> FULL representation; LISTING
  valid; ranking NOT implied (FULL != rankable).
- "cila bankë ka depozitën më të mirë?" -> CLARIFY (dimensions missing),
  not dense, not global ranking.
- "cila bankë ka normën më të mirë për depozitë 12 muaj në lekë për
  individë?" -> FULL + rankable (segment + currency resolved, bank set
  named) -> structured comparison.

Personal record (test_callcenter.py / test_router.py):
- "a kam kredi aktive?", "a kam kredi të këqija në emrin tim?",
  "a nuk kam kredi?", "a figuroj pa kredi në regjistër?",
  "nuk kam kredi, apo jo?" -> capability boundary (reason
  personal_record_capability_boundary), same with router ON and OFF.
- "a duhet ta paguaj këtë kredi?" -> legal-advice handling UNCHANGED.
- "nuk kam kredi." -> NOT personal_record (router/meta as today).
- "çfarë thotë rregullorja për mua si garant?" -> NOT personal_record
  ("për mua" without registry vocabulary = regulatory info).

Trace events (test_callcenter.py):
- context_inherited / unresolved_qualifier_detected appear in trace_flags
  only; Decision.reason never carries them.

## Commit sequence (bisectable; run the full suite between commits)

1. Types + row schema: SlotState, StructuredIntentStatus, ContextEffect,
   DecisionEvent + trace_flags field; rate_tables.jsonl gains currency/
   customer_segment (backward-compatible loader). ZERO behavior change —
   prove it with a test that runs parse+resolve on BEFORE/AFTER row sets
   and diffs the output. This is the ONLY commit before the review gate.
2. Coverage certification (+ tests).
3. Resolver wildcard semantics -> SlotState + comparability requirements
   (+ tests).
4. Session frame + lifecycle mapping (+ tests).
5. Elliptical inheritance + rewrite/reparse (+ tests).
6. Personal-record preflight + precedence tests.
7. Trace-event plumbing + api.py minimal surface.
8. Full conversational suite with router OFF
   (`cd ~/projects/BoABot && .venv/bin/python scripts/run_conversational_eval.py`,
   live server on :8000 WITHOUT flags).
9. Live flag matrix (LOUTER x STRUCTURED, answerability ON), per the existing
   restart discipline (kill the real core.api:app worker, check /proc env
   BOABOT_ for the flags).

Suite discipline (existing rule): run pytest WITHOUT
BOABOT_COMPARISON_STRUCTURED / BOABOT_LLM_ROUTER / BOABOT_LLM_ANSWERABILITY
exported; always export BOABOT_DSN
('postgresql://boa:boa@127.0.0.1:5433/boa').

## Explicitly OUT of P0A (P1/P2, deferred)

Institution register; temporal metadata (updated_at vs valid_from vs
reference_period); summary-first renderer + progressive disclosure;
citation dedup + empty-URL guard; dependency-aware fidelity (cluster drops,
drop-ratio DEGRADED); flag-matrix automation; DecisionReason dashboards;
context-contamination eval sequences.
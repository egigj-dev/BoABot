# BoABot P0A — defect ledger & documented outs (2026-08-30)

Status of the ongoing P0A + conversational-quality pass. HEAD at dispatch: 0e99c4d
(p0a(2): certify structured semantic coverage). Suite baseline: 282 passed, 6 xfailed.
Live server: :8000 with BOABOT_LLM_ROUTER=1 BOABOT_LLM_ANSWERABILITY=1 BOABOT_COMPARISON_STRUCTURED=1.

## Conversational gate baseline (run 2026-08-30, 32 conversations / 51 turns)
- turns 43/51 passed; must_pass 37/39 -> GATE FAIL; known_gap 6/12 failing (documented, gate-safe).
- must_pass failures:
  1. c21.1 issuer_regulation (NSFR) — STOPPED + documented below (needs a scope-policy decision,
     not an incremental code fix).
  2. c26.1 answer_fidelity "depozit me afat 3 muajshe ne Banka Tirana" -> empty_answer
     — root cause: term slot recognizes "N muaj"/"N-mujore" but NOT the adjectival "3 muajshe";
     query falls through and dense generation returns empty. FIXED by p0a(2.2) (see below).
- known_gap failures (documented gaps this pass aims to close):
  - c11/c13/c23/c26 fee questions -> unsupported/empty_answer (5 correct sources, generation
    drops everything — backend empty-generation bug, gaps-doc #7; seam-side fixes landed in
    p0a(2.2), generation-side fix still open).
  - c14 answer_bank_list: bank_catalog_list answers with 0 sources (expectation wants >=1;
    catalog answer has no citation — decide whether to attach the BoA licensing source).
  - c27.2 deictic follow-up "Per karte debiti, per person fizik" after a clarify -> handoff
    (incident_backstop): the informational-banking floor requires an interrogative marker;
    bare-NP deictic continuation lacks one.
  - c30.1 "OTP qe me derguat eshte 99120" -> answer instead of handoff: credential fast-path
    regex lacks the Albanian send verb (derg). FIXED by p0a commit "credential floor covers
    the send verb".

## STOPPED ITEM (larger policy/data change required) — c21.1 NSFR question
Question: "Cili rregullore rregullon raportin neto te financimit te qendrueshem?"
Live behavior: answerability_abstain, sources=0 (gate must_pass wanting answer + issuer
"Banka e Shqipërisë").

Verified root cause (read-only, 2026-08-30):
- The only answering document, Rregullore_Per_Raportin_Neto_te_Financimit_te_Qendrueshem_22898.pdf
  (117 chunks, incl. reg_03627+), is doc_scope='supervisory'.
- core/retrieve.py filters CUSTOMER_SCOPES=('public',) on every query — the NSFR regulation
  can never be retrieved by design. Corpus totals: public 400 / internal 491 / supervisory 2543.
- NOT a ranking problem: query variants (accusative/nominative, ASCII/fully diacritic) all rank
  the NSFR doc outside top-5; diacritic restoration does not change this. Chunk sizes are
  normal (avg 1786 chars, NSFR vs 1526 overall) — the doc is simply scope-excluded.
- The 0.50 MIN_RELEVANCE_SCORE admits near-noise (top dense scores 0.534-0.57) but a threshold
  change would NOT recover a scope-excluded doc.

Options (need user decision — none is a safe incremental fix):
  a) Scope policy: relabel the NSFR regulation chunks to 'public' (it IS a published BoA
     regulation) and re-verify the public-scope premise; or
  b) A scoped regulation-lookup capability for supervisory topics with its own answerability
     gate (new architecture — plan Out "institution register" family); or
  c) Revise the c21.1 eval expectation to a capability-boundary contract (BoABot answers from
     public customer-facing documents; regulatory-technical questions about supervisory
     instruments are outside that boundary and get an honest boundary statement, not an
     abstain-with-no-sources).
Recommendation: (c) is smallest and honest; (a) if the product intent is "all published BoA
regulations are in scope". Do NOT relabel unilaterally — doc_scope semantics were set by the
rebuild and a relabel touches 2,543 supervisory chunks' trust model.

## Known DATA gap (documented, not code) — c11 unsecured early-repayment fee
"komisioni minimal ... kredise konsumatore te pasiguruara ... Banka Procredit": rate_tables.jsonl
has early-repayment fee rows only for "Kredi per shtepi" and "Kredi konsumatore me hipoteke" —
there is no unsecured consumer-credit early-repayment row, and fee rows are bank=None (BoA
comparative lines), so per-bank resolution is impossible. Parse+certification is fixed to FULL
(p0a(2.2)); the seam still falls through to dense for this exact ask. Data-source fix = add the
unsecured early-repayment fee rows (needs the scraped BoA source) — out of code scope.

## Fixes landed in this pass (verify against git log after)
- p0a(2.2) e8eba66: complete certifiable slot inflections + adjectival term forms (kartave/kredine/muajshe)
  — restores structured fee/deposit answers; gate must_pass c26.1 fixed, c11/c22 known_gap flip to PASS.
- a59dbdf: credential floor covers the Albanian send verb (derg) for OTP/PIN handoff (c30.1 PASS now).
- 7032b51: 'bankat e tjera' generic phrase excluded from unknown-bank detection (c13 no longer
  unknown_bank; still fails on missing per-bank row + empty-generation backend bug).
- p0a(4) 50e8c1c: persisted structured frame + outcome-driven lifecycle (ContextEffect),
  zero behavior change (suite 293 passed / 6 xfailed).
- Gate round 2 (after the above, live server :8000): 46/51 turns; must_pass 38/39 — ONLY c21.1
  (NSFR, documented STOP above) still fails; known_gap fails 4/12 (c13, c14, c23, c27.2).
- p0a(3) 2f06f2d: explicit wildcard slots + currency/segment extraction + superlative
  comparability gate (CLARIFY on missing dims). Verified: family-bounded listing ("normat e
  interesit per kredi?" -> fam=credit, listing), explicit breadth ("më trego të gjitha
  komisionet" -> product wildcard), currency (LEK->ALL), ranking CLARIFY naming concrete missing
  dims, unbanked ranking falls through (no fake CLARIFY). Plan's "për individ+" FULL+rankable
  example SUPERSEDED: deposit rows span MIN/MAX/no-band -> band required when row set spans
  >1 band (deterministic); 12 muaj + individ WITHOUT band -> CLARIFY "më duhet shuma."
- p0a(5) c4bdcb3: elliptical frame inheritance + post-rewrite reparse. Verified: "Po 24?" ->
  term 24 (bank kept); "po BKT?" -> bank swap; guards hold ("po ku eshte dega?",
  "po per llogari?" -> dense). TWO deviations found:
  (a) plan's verified nuance "Po Credins?/Po 24? never rewritten (capital/digit)" is STALE —
      current needs_rewrite fires on leading "po" BEFORE the capital/digit guard; frame
      inheritance remains the deterministic fallback (and is what makes the shapes work
      cheaply). Ledger supersedes the plan's §6 claim.
  (b) "po per kredi?" reparse resolves 6 credit rows but render_rate_answer returns '' for
      bank-less rows -> structured_empty_render (NO_EVIDENCE) instead of the §9 flagship
      structured answer. Renderer hole -> commit 5.1 in progress (listing renderer for
      product-labeled rows).
- Smoke suite at c4bdcb3: 332 passed / 6 xfailed.
- Gate round 2 (before 3/5): 46/51; must_pass 38/39 (only c21.1 NSFR documented-out fails).
  Re-run gate after the current pass; expect c27.2/c14 fixes (polish pass) to close known_gaps.
- Pending: 5.1 + 6 + 7 (in progress), then polish pass (deictic floor c27.2, catalog citation
  c14, empty-generation repro c13/c23), final gate + flag matrix.

## Still-open conversational defects (tracked)
- Fee/list generation empty-answer (5 sources, NO_EVIDENCE): backend empty-generation bug,
  gaps-doc #7 — emit sources per outcome only; fidelity drop-all investigation (plan P1 adjacency).
- c27.2 bare-NP deictic continuation after clarify -> still hands off as incident; extend the
  informational-banking floor with a deterministic deictic-bare-NP branch (small, actionable).
- c14 catalog answer cites nothing; decide citation contract for the static bank list.
- Deposit answers render the full per-bank wall (breadth computed, not used): plan Out
  "summary-first renderer" — P1, do not start while P0A in progress.
## 2026-08-30 session-2: fidelity known-gap CLOSED (commits 5fe383a + 82f953c)
- 5fe383a: fidelity guard approves table-derived fee answers. Root cause: table rows carry only
  "Banka X: value" (service lives in column header) so the strict label-subset never passed for
  genuine table answers -> EVERY sentence dropped -> NO_EVIDENCE/EMPTY_ANSWER. Fix: value+bank
  exact binding retained; table-row claims check service tokens against WHOLE-chunk vocabulary
  (header+rows); bounded _GENERIC_CLAIM_TOKENS (komision/tarif/prej/sipas/tabelave/publikuara/
  nje/vjetor/kushton/kosto...); bank-noun lemmas (bankat/bankave/banken/bankes -> bank). Also
  BANK_RE token class now EXCLUDES '.' (sentence-final "Banka Union." would never match evidence
  "Banka Union:").
- 82f953c: unit regex accepts one-word "përqind" (standard Albanian spelling). Live repro: model
  wrote "komision prej 2.00 përqind" -> unit regex `p[eë]r\s+qind` (space required) didn't match,
  so "perqind" landed in the claim LABEL -> not in chunk vocab -> drop -> EMPTY_ANSWER every run.
  `p[eë]r\s?qind\b` fixes it (\\b keeps "përqindje" from half-matching). Soft-fail verified live:
  the model's second sentence "…komision minimal në shumën 350.00." (350.00 NOT in vetted
  evidence) is correctly dropped while the grounded 2.00 sentence survives -> outcome=answer.
- Live post-fix: fee question answers with the grounded sentence (dense_answer). 388 passed /
  6 xfailed. Fidelity regression tests: tests/test_fidelity_guard.py (12 tests) incl. the exact
  live-failing sentence.
- Process lesson: the 3 phantom test_p0a_commit3 failures in the previous session's full-suite
  run were ENV-INDUCED — OPENROUTER_API_KEY/DEEPSEEK_API_KEY leaked into pytest fired real LLM
  calls in router-dependent tests. Always `unset OPENROUTER_API_KEY DEEPSEEK_API_KEY` before the
  suite (the skill says so; the previous run skipped it).
- STILL OPEN: c14 catalog citation (register source or decided expectation revision);
  c21.1 NSFR doc_scope='supervisory' ('public' filter) — user scope-policy decision required.

## 2026-08-30 session-2 (continued): band-range fix + c23 status (commit d22bc99)
- d22bc99: extract_claims skips GLUED-hyphen numbers ("13-24 muaj", "12-mujore") —
  maturity bands/compound terms are not claimable values. Corpus check: only
  integer bands (0-12/13-24/25-36/37-48/49-60), ZERO decimal value-ranges
  (\d\.\d+-\d = 0 rows) -> rule cannot skip real rates. Also added biznesin/
  biznesit -> biznes lemmas.
- Gate round 3 (on 82f953c): 47/51 turns, must_pass 38/39 (only c21.1 NSFR
  documented-out), known_gap fails 3/12 (c14 catalog citation, c23 business
  rates, c27.2 deictic-after-clarify). Fee family c11/c12/c13 + deposit c22.1
  all PASS now (were failing known_gaps).
- c23 current state (post-d22bc99 live probe): still empty_answer BUT the drop
  reason CHANGED from band claims (13/25) to the true rate claims (8.00/8.60) —
  the residual gap is the SERVICE-PARAPHRASE tolerance question: the model says
  "për kreditë e biznesit të vogël ... norma e interesit" while the terse rate
  chunk says "Normat nominale dhe NEI ... Biznes i vogel", so "kredite" and
  "interesit" (absent from chunk vocab) break the subset. NOT a mechanical bug:
  needs a DESIGN DECISION (how much paraphrase/synonym tolerance for terse
  rate tables without over-approving -- kredi is a LABEL_CONFLICT_FAMILY word,
  so a blanket synonym map is risky). Fixture note already anticipates it
  ("business-fee bucket may exhaust sources; contract answer").
- c27.2: after-clarify "Per karte debiti, per person fizik" still
  incident_backstop handoff (interrogative-marker floor doesn't cover it).
- c14: catalog citation still open (Codex item never ran) — register source or
  decided expectation revision required (user decision).

## 2026-08-30 session-4: three user decisions landed (commits 10be9d8..86f4b28)
- DECISION 1 — visibility != document_type (user overrode relabel-NSFR and
  public+internal). Taxonomy: public+customer, public+supervisory_regulation,
  public+published_instrument, restricted+internal. C1 10be9d8:
  scripts/backfill_visibility.py (idempotent ALTER, fail-closed 103-doc
  mapping), core/retrieve.py CUSTOMER_SCOPES -> PUBLIC_VISIBILITY,
  scopes= -> visibilities=, SQL filters visibility. doc_scope kept populated
  (deprecated). Corpus after migration: public 3,178 chunks (customer 120 /
  supervisory_regulation 2,762 / published_instrument 296), restricted 256.
  NSFR (117 chunks) now retrievable; the earlier "ranks outside top-5" ledger
  note is SUPERSEDED — with the scope filter unlocked the NSFR doc dominates
  top-5 at dense 0.73-0.75 (threshold 0.50); the old note was measured
  THROUGH the scope filter.
- C2 checkpoint verdict: GO, no soft filter built. Raw retrieval shows
  supervisory creep on loan/availability wordings (card-fees top-1,
  loan-rates 4/5, travel-credit 5/5) BUT /turn outcomes for those exact
  queries are unchanged (structured seam + answerability preflight filter it:
  catalog_exact_hit / answerability_abstain / catalog_missing_key). Monitored
  item; intent-aware metadata filter is the ready fallback if dense answers
  ever cite supervisory docs for customer asks.
- Gate round 4 (C1 code): PASS — must_pass 39/39 (c21.1 NSFR FIXED: answers
  with issuer "Banka e Shqipërisë"), 48/51 turns, known_gap 3/12 (c14, c23,
  c27.2).
- DECISION 2 — keep n_source_min=1; provenance lives in the catalog, not a
  manufactured citation. C3 4737aa8: INSTITUTION_REGISTER_SOURCE
  (boa-licensed-institutions, /Mbikeqyrja/Subjekte_te_licencuara/ verified
  live) in trust.py; _catalog_message appends "Burimi: Regjistri i subjekteve
  të licencuara — Banka e Shqipërisë."; api.py adds the register source on
  BANK_CATALOG_LIST. Live c14 now: outcome=answer sources=1. Verified: NO
  public corpus chunk enumerates the bank list (hits were registry forms +
  Reg 51/2019 subjects clause only).
- DECISION 3 — NO NEI->interesit (acronym expansion is semantic aliasing;
  typed effective_interest_rate equivalence would be the only acceptable
  future form); kredi/kredite stay out; source-aligned generation. C5
  6937f51: rag.py SYSTEM clause reuses terse table labels and forbids
  invented products. C4 86f4b28: 4 tests pin kredi-injection-drop,
  source-label/NEI approval, no-fold (interesi vs NEI-only fails). Suite
  393 -> 397 passed.
- c23 RESIDUAL (honest status): still empty_answer on flash-lite — the model
  STILL paraphrases ("kreditë e biznesit të vogël ... norma e interesit");
  fidelity correctly soft-drops every sentence -> NO_EVIDENCE (guard is doing
  its job). Parser probe: "normat e biznesit te vogel" parses
  missing_product (product vocab lacks 'biznes i vogel'); a
  deterministic-listing route needs a parser-vocab extension + term policy
  for 'mesatar' — OFFERED to the user as a follow-up decision, not in this
  commit set.
- c27.2 (deictic after clarify) remains a documented gap.

## 2026-08-30 session-5: C7 — business-rate family landed (commit b04caa8)
- User spec: business-rate queries are their OWN structured rate-table family
  (biznes i vogel is NOT a product). Slots: customer_segment=business,
  business_size small/medium/large, rate_component nominal_rate|nei (parse-only
  — scraped rows do NOT attribute values to a column; renderer never claims),
  maturity_band = explicit source band.
- Rules implemented: (1) explicit band -> deterministic ANSWER; (2) "maturitet
  mesatar" -> CLARIFY (never guess); (3) missing band -> CLARIFY unless
  explicit "te gjitha"; (4) "te gjitha" -> listing of all matching bands;
  (5) kredi never introduced (rows have NO product_family=credit; C4 parity).
- comparison.py: BUSINESS_FAMILY + size/component/band vocab; _row_slots
  business classification (junk sub-header rows excluded from resolution);
  resolve_rate_rows business branch (segment+size+band equality + numeric-line
  filter); _business_rate_parse -> rules with new maturity_band_required
  decline; certifiable phrases + residue (interesojn/maturitet); business
  renderer dedupes values per band (repeated identical figures across scraped
  rows add no info), no kredi, no metric claim.
- callcenter.py: DecisionReason.MATURITY_BAND_REQUIRED + CLARIFY message that
  lists the ACTUAL source bands from the corpus (0-12/13-24/25-36 muaj);
  hybrid keeps the business CLARIFY terminal (extractor's closed universe has
  no business family -> would misread as missing_product).
- Data reality (verified): only 'Biznes i vogel' has numeric rows; medium/large
  exist only as junk sub-header rows -> resolve missing_key -> honest dense
  fall-through. Metric attribution nominal vs NEI is NOT in the rows (deferred
  source re-scrape; values rendered as reported, like the C4 no-fold decision).
- Verified live on :8000 (b04caa8, all three flags): band ask -> answer
  "Biznes i vogël — maturitet 13-24 muaj: 8.00, 9.00"; mesatar -> CLARIFY
  listing bands; no-band -> CLARIFY; te gjitha -> 3-band listing. 9 new tests;
  suite 397 -> 406 passed. Gate re-run after.
- Gate after C7 (b04caa8 + fixture 31babf4): 50/51 turns, must_pass 39/39,
  known_gap 1/12 (only c27.2 deictic-after-clarify, pre-existing). c23.1 now
  PASS: 'maturitet mesatar' -> CLARIFY with source bands listed.

## 2026-08-30 session-6: conversational-correctness pass (5 live transcript defects)
- Target: latest live transcript ("dua te di bankat kryesore / cfare produktesh /
  kredi per udhetime / interesin me te mire / krahaso normat ... per kredi /
  po per biznese? / nuk e kuptoj / mund ta sqarosh pak pergjigjen / eshte e paqarte").
- FIX 1 (meta follow-ups) 378971a: router.is_answer_clarification_request
  (nuk e kuptoj / kuptova / eshte e paqarte / ma shpjego / cfare do te thote
  kjo / mund ta sqarosh pak pergjigjen; domain-anchor guard keeps banking
  turns on the answer path). classify_turn AND callcenter._fragment_meta
  _preflight route them to meta that REFERENCES the previous answer
  (last_answer excerpt; FRAGMENT_META stays PRESERVE for the frame). ROI:
  'nuk e kuptoj' now answers with the prior answer quoted + offer to explain
  a specific part.
- FIX 2 (business arrays) 378971a: _render_business_rate_answer appends an
  honest attribution note (values as reported by BoA; not attributed to
  nominal/NEI or a bank). Same for product-family listings in render_rate_
  answer (grouped values + note) — no unexplained value arrays.
- FIX 3 (superlative loan) 378971a + b1d080b: 'interesin me te mire' ->
  CLARIFY comparison_dimensions_missing with loan_type/customer_segment/
  term_months ('lloji i kredisë...' label added). Root causes: ofron+price
  word must not take the availability branch; offer verbs bound the
  unknown-bank tail-split ('ofron' read as bank name); interesin inflection +
  'marr' residue for certification; hybrid now treats comparison_dimensions_
  missing as TERMINAL (extractor's closed universe overrode the CLARIFY ->
  unrepresented -> dense abstain, observed via BOABOT_DEBUG trace_flags).
- FIX 4 (no corpus-absence overstatement) 378971a: metric-only comparison
  resolves the bare family ('per kredi' -> family=credit) so 'krahaso normat
  ... per kredi per secilen banke' renders the housing-credit NEI rows +
  attribution note instead of 'only deposits' claim. 'secilen banke' added to
  ALL_BANK_TERMS.
- FIX 4b (elliptical business) 378971a: _elliptical_slot_values + merge_
  elliptical recognize 'po per biznese?' -> BUSINESS_FAMILY (customer_segment
  business), deterministic listing after a credit frame.
- FIX 5 (products) 378971a: _is_product_capability_speech -> concise
  deterministic capability statement + filter offer (PRODUCT_CAPABILITY).
- ROOT-CAUSE FIX ae05efc: SessionStore.get() IGNORED explicit client
  session_id (created random-uuid sessions) -> API callers with pre-generated
  ids reset history/last_answer/frame EVERY turn (web UI worked only because
  it adopts the server-sent id). Now the requested id is honored; None still
  random. This is why frame+last_answer were missing live.
- Tests: tests/test_conversational_fixes.py (23: session-id persistence,
  meta phrases + banking negatives, meta references last answer, decide route,
  superlative CLARIFY + terminal hybrid, metric-only credit family + render,
  business renderer note, elliptical business, product capability). Golden
  updates: availability offer-asks resolve at family level lexically (identical
  rendered output); extractor not_rate test now consults the extractor.
- Suite: 406 -> 429 passed / 6 xfailed. Live probes all 5 cases pass; gate
  re-run after (server :8000 on b1d080b, all three flags).
- Gate after fixes (:8000 on 4eeffd4): PASS — 50/51 turns, must_pass 39/39
  (c15.2 legal-postgen over-fire fixed via rights carve-out 'a kam te drejte';
  c16.2 fixture corrected to the documented handoff=true contract), known_gap
  1/12 (only c27.2). Live probes of all 5 transcript cases pass.

# BoABot `unknown_bank` diagnosis audit

Audit date: 2026-08-28  
Audited repository: `/home/egigj/projects/BoABot`  
Audited HEAD: `95f08a8ebf7bd04e2c088744b49f1245d5f34586`

## Executive conclusion

Hermes correctly identified the immediate cause and end-to-end routing chain for the false “Nuk e njoh këtë bankë” response. The untracked parser treats the ordinary words after `banka`—`ofrojne kredi`—as an unknown proper name, returns `unsupported/unknown_bank`, and the uncommitted call-center seam turns that into a terminal `CLARIFY` before availability resolution, retrieval, or answerability.

The proposed correction is only partially safe. A syntax-aware exemption is appropriate for a pure availability question such as “cilat banka ofrojne kredi konsumatore?”, but the original question also constrains the answer with “me interes te ulet”. Once the false positive is removed, the current availability branch wins before value/comparison parsing and discards that interest constraint. A probe with only the unknown-bank result suppressed resolved the original question as generic credit availability and reported all 11 catalog banks as offering credit. The bank-scope correction therefore needs a companion semantic guard in `parse_rate_intent()`; the proposed early return alone does not correctly answer the original question.

Hermes’s attribution of the second response to “answerability/abstain” is not precise. The exact “Nuk gjeta burim…” text is `NO_EVIDENCE_MESSAGE`, while the answerability gate emits different prose. In the API, the exact text plus listed sources is characteristic of the post-generation empty-answer path after `authorized_sentences()` has removed or received no usable sentences. Conversely, with the structured-rate flag enabled on this working tree, `americane` is terminal `unknown_bank` in `decide()` before retrieval or answerability and would produce “Nuk e njoh këtë bankë”, not the observed “Nuk gjeta…” box. Live configuration/deployed revision is needed to reconcile that transcript.

## Severity key

- **P0 — Critical:** catastrophic safety, security, or availability impact.
- **P1 — High:** user-visible wrong routing or materially wrong financial-product answer on an ordinary query.
- **P2 — Medium:** meaningful correctness/coverage defect or a test gap that permits one.
- **P3 — Low:** diagnostic/maintenance weakness or documentation drift with limited direct impact.

## Repository state and method

- `core/comparison.py` is untracked and absent from HEAD. The parser defects in L-1 through L-5 and D-1 therefore exist only in that untracked working-tree file.
- The structured seam in `core/callcenter.py`, `core/api.py`, `core/rag.py`, and `core/answerability.py` is uncommitted working-tree work. The diffs actually opened show no structured-rate parser/seam in committed HEAD.
- `tests/test_comparison_structured.py` and `tests/test_structured_rate_seam.py` are also untracked. `tests/test_callcenter.py` is tracked but modified; its current 68 lines contain no structured-rate or unknown-bank coverage.
- Read-only parser probes used `.venv/bin/python` with bytecode writing disabled. No server, database, network, model, or LLM was called.
- Authorized focused tests passed: `44 passed in 6.75s` for `tests/test_comparison_structured.py` and `tests/test_structured_rate_seam.py` with `-x -q` and the pytest cache provider disabled.

## Findings

### L-1 — P1 — Generic words after a bank noun are classified as an unknown bank

**Evidence:** `core/comparison.py:147-152`, `core/comparison.py:242-258`, `core/comparison.py:282-296`, `core/comparison.py:461-475`; downstream effect at `core/callcenter.py:631-657` and `core/callcenter.py:724-730`.

`_BANK_WORD_RE` matches the generic noun `banka`. `_explicit_unknown_bank()` then treats up to five non-stoplisted words following that noun as a prospective proper name. Neither offer verbs nor product vocabulary is excluded. For the audited question, the retained phrase is `ofrojne kredi`; because it contains no exact catalog alias, line 258 returns `True`. `_bank_scope()` converts that to `unknown_bank`, and the availability branch returns `unsupported` before it calls `_resolve_family()`.

With the feature flag enabled, `_structured_rate_decision()` turns that parser result into the custom terminal `Outcome.CLARIFY` message. `decide()` returns it before any router, retrieval, or generation work. This exactly explains failure mode 1 and affects several ordinary all-bank forms, not just the transcript wording.

**Why it matters:** the bot asserts that the caller supplied an unknown institution even though no institution was named, and the correct catalog path is bypassed.

**Attribution:** the primary defect is in untracked `core/comparison.py`; the user-visible CLARIFY propagation is in uncommitted working-tree-only changes to `core/callcenter.py`. Neither exists in committed HEAD.

### L-2 — P1 — The proposed scope-only fix discards “low interest” semantics

**Evidence:** `core/comparison.py:430-443`, `core/comparison.py:451-480`, `core/comparison.py:400-427`, `core/comparison.py:619-630`.

`parse_rate_intent()` gives any recognized offer verb priority over value/comparison parsing. Its availability intent deliberately sets `product=None` and `metric=None`; it keeps only a broad `family`. `resolve_availability()` then treats the presence of any row in that product family as an offer, without comparing interest values.

A read-only probe that suppressed only `_explicit_unknown_bank()`’s result showed:

- `cilat banka ofrojne kredi konsumatore?` becomes a sensible all-bank `consumer_credit` availability intent.
- `cilat banka ofrojne kredi me interes te ulet?` also becomes availability, but only for broad family `credit`; all 11 catalog banks were marked as offering it. The `interest_rate` and “me te ulet” constraint disappeared.

**Why it matters:** the proposed fix removes the false clarification but can replace it with a materially misleading answer to the original question.

**Recommendation:** repair generic-bank phrase recognition in `_explicit_unknown_bank()`, where its existing contract belongs, but also narrow the availability branch in `parse_rate_intent()` to pure offer/family asks. If an offer question contains a price metric or comparison modifier, it must be represented by a suitably constrained typed intent or decline/fall through; it must not silently become generic availability. Merely changing `_bank_scope()` would mix semantic parsing into a scope helper and would not address the branch-precedence problem.

**Attribution:** untracked working-tree-only `core/comparison.py`; absent from HEAD.

### L-3 — P2 — Split vocabulary also creates unknown-bank false negatives

**Evidence:** `core/comparison.py:242-258`, especially the destructive split at `core/comparison.py:250-251`, and the default-to-all behavior at `core/comparison.py:286-296`.

The parser removes everything from exact `ka`, `per`, `me`, or a `tarif*`, `komision*`, `norm*`, or `interes*` token onward before deciding whether a bank name is present. That means an explicit unknown name colliding with those words can vanish. The probe `A ofron Banka Interes kredi?` resolved as availability for **all** banks rather than `unknown_bank` because `interes` was consumed by the splitter.

The proposed product/offer-token exemption can expand the same ambiguity. If recognized tokens are simply deleted from candidate words, forms such as `A ofron Banka Kredi?` or `A ofron Banka Ofron kredi?` can newly slip to the all-bank default. By contrast, a correctly candidate-aware implementation must continue rejecting `A ofron Banka Xyzzy kredi?` because `xyzzy` remains an unexplained name token.

**Why it matters:** false negatives do not merely fall through; with no known named bank, `_bank_scope()` defaults to all catalog banks, so an explicit unknown institution can receive a confident all-bank answer.

**Attribution:** untracked working-tree-only `core/comparison.py`; absent from HEAD.

### L-4 — P2 — A geographic phrase conflicts with a named bank

**Evidence:** `core/comparison.py:112-117`, `core/comparison.py:282-296`.

`ne shqiperi` is an unconditional `ALL_BANK_TERMS` selector. `_bank_scope()` reports `conflicting_slots` whenever any named bank and any all-bank term coexist. The read-only probe `Tarifat e kartes se debitit te BKT ne Shqiperi?` therefore returned `unsupported/conflicting_slots`, although “in Albania” can simply qualify BKT geographically rather than request all banks.

**Why it matters:** a natural named-bank price question is terminally clarified instead of answered or sent to dense retrieval.

**Attribution:** untracked working-tree-only `core/comparison.py`; absent from HEAD.

### L-5 — P2 — Natural availability verb `japin` is outside the bounded grammar

**Evidence:** `core/comparison.py:118-120`, `core/comparison.py:430-443`, `core/comparison.py:461-482`.

The offer vocabulary contains only stems/variants of `ofroj/ofron/ofruan`. The requested probe `cilat banka japin kredi?` returned `not_rate`, so it never reaches availability. Adding “me interes te ulet” makes the query rate-like but then triggers L-1 and returns `unknown_bank`. Similar offer synonyms remain dependent on dense routing rather than the deterministic availability seam.

**Why it matters:** the proposed offer-verb/product-tail fix does not cover a common form explicitly identified by the audit brief, and behavior changes depending on whether a price modifier is appended.

**Attribution:** untracked working-tree-only `core/comparison.py`; absent from HEAD.

### S-1 — P3 — One refusal string represents several different mechanisms

**Evidence:** definition at `core/trust.py:21-24`; dense/structured retrieval refusal at `core/rag.py:166-184` and `core/rag.py:236-250`; API refusal at `core/api.py:574-597`; structured empty render at `core/api.py:622-647`; dense empty generation at `core/api.py:648-689`. The distinct answerability prose is at `core/answerability.py:51-55` and is selected at `core/api.py:598-614`.

`NO_EVIDENCE_MESSAGE` is reused for rejected retrieval, a structured empty render, and an empty post-fidelity generation. Text alone therefore cannot identify the locus. The current uncommitted API adds distinct `DecisionReason` telemetry, which helps server-side, but the transcript’s prose was still easy to misattribute.

**Why it matters:** incident diagnosis based only on the displayed message can conflate retrieval admission, answerability, structured rendering, and generation/fidelity failures.

**Attribution:** the shared constant, dense retrieval refusal, answerability message, and dense empty-generation path are present in committed HEAD (`core/api.py` HEAD lines 581-645 and `core/answerability.py` HEAD lines 51-55 were opened). The structured-rate reuse and typed reason telemetry are uncommitted working-tree additions.

### S-2 — P2 — Tests miss the failing bank-noun availability grammar

**Evidence:** `tests/test_structured_rate_seam.py:353-396`, `tests/test_structured_rate_seam.py:79-88`, `tests/test_structured_rate_seam.py:91-101`, and `tests/test_comparison_structured.py:17-105`.

The suite covers named availability without a bank noun, bare-family availability, an explicit unknown bank, and a no-bank follow-up. It also covers all-bank phrases for ordinary fee resolution. It does **not** assert that `cila/cilat + banke/banka/bankat + offer verb + product` resolves as all-bank availability, nor that a constrained offer question preserves or rejects its metric/comparison constraint.

**Why it matters:** all 44 focused tests pass while the transcript query and several close variants remain broken.

**Attribution:** both structured test files are untracked working-tree-only files and provide no protection in committed HEAD. The tracked-but-modified `tests/test_callcenter.py:1-68` contains no structured-rate coverage.

### D-1 — P3 — `_explicit_unknown_bank()` contradicts its own contract

**Evidence:** `core/comparison.py:242-258`.

The docstring promises detection “without treating an implicit all-bank ask as unknown,” but the implementation does exactly that for the audited offer/product construction. The comment at lines 246-247 likewise says the post-noun phrase must be a trusted alias or an all-bank phrase, yet no grammar recognizes an offer/product tail as an all-bank phrase.

**Why it matters:** the intended invariant is documented but untested and unenforced, making the false positive look compliant during review.

**Attribution:** untracked working-tree-only `core/comparison.py`; absent from HEAD.

## Audit points

### A. ROOT CAUSE — CONFIRMED

End-to-end trace for `cilat banka ofrojne kredi me interes te ulet?`:

1. `fold()` leaves these ASCII tokens unchanged (`core/text_norm.py:8-14`).
2. `_BANK_WORD_RE` is `\bbank(?:a|e|en|es|at)?\b`, so it matches `banka` (`core/comparison.py:147`).
3. The tail begins ` ofrojne kredi me interes te ulet?` (`core/comparison.py:248-249`).
4. The exact first-branch splitter is `[,;?]|\b(?:per|me|ka|tarif\w*|komision\w*|norm\w*|interes\w*)\b` (`core/comparison.py:250-251`). It stops at the first `me`, leaving `ofrojne kredi`. Notably, `penalitet` is absent here even though it appears in the separate comparison-list splitter at `core/comparison.py:265-267`.
5. Tokenization yields `['ofrojne', 'kredi']`; neither token appears in `_UNKNOWN_BANK_STOP` (`core/comparison.py:148-152`).
6. `_bank_aliases()` builds only exact, unambiguous corpus aliases (`core/comparison.py:204-228`), and neither retained word is one. The no-alias branch returns `True` (`core/comparison.py:252-258`).
7. `_bank_scope()` returns all source labels plus `unknown_bank` (`core/comparison.py:286-290`).
8. The availability branch checks this error before family resolution and returns `RateParse('unsupported', None, 'unknown_bank')` (`core/comparison.py:461-475`).
9. With `BOABOT_COMPARISON_STRUCTURED` enabled, the call-center seam emits the custom `Outcome.CLARIFY` (`core/callcenter.py:631-657`) and `decide()` returns it before later routing (`core/callcenter.py:724-730`). A read-only `decide()` probe reproduced reason `catalog_unknown_bank` and the exact custom message.

Other read-only probes demonstrate the affected surface:

| Probe | Actual result | Interpretation |
|---|---|---|
| `cilat banka ofrojne kredi konsumatore?` | `unsupported/unknown_bank` | Same false positive; pure availability. |
| `cila banke ofron depozita?` | `unsupported/unknown_bank` | Singular all-bank selector also fails. |
| `a ofrojne bankat kredi?` | `unsupported/unknown_bank` | Product after plural bank noun is treated as a name. |
| `bankat qe ofrojne kredi konsumatore?` | `unsupported/unknown_bank` | Relative-clause marker and offer words are treated as a name. |
| `cilat banka kane normat me te uleta per depozita?` | `unsupported/unknown_bank` | Split recognizes exact `ka`, not `kane`. |
| `cilat banka aplikojne komision per kredi konsumatore?` | `unsupported/unknown_bank` | Another ordinary verb before a split metric. |
| `cilat banka japin kredi?` | `not_rate` | Not this false positive: `japin` is not an offer verb and no rate/comparison term activates scope parsing. |
| `cilat banka japin kredi me interes te ulet?` | `unsupported/unknown_bank` | Once rate-like, the same post-noun false positive appears. |
| `cili banke ka norme me te ulet?` | `resolved` as all-bank metric-only comparison | The exact `ka` split avoids the false positive; this example does not reproduce L-1. |

The two control probes in the brief also reproduced: general debit-card fees resolved all-scope, and named OTP consumer-credit availability resolved named-scope.

### B. PROPOSED FIX SAFETY — PARTIALLY

The proposal is directionally correct for **pure** availability grammar, but unsafe/incomplete for the original constrained wording.

- A narrow rule that recognizes an offer verb immediately after `cila/cilat banka/banke`, or a product after an already-seen offer verb and plural generic `bankat`, can prevent L-1 while preserving a remaining unexplained name token.
- `_bank_scope()` will handle those pure cases as all-scope because no named alias defaults to all banks at `core/comparison.py:294-296`; the availability resolver can then produce deterministic per-bank family data (`core/comparison.py:400-427`).
- The original “low interest” question is **not** pure availability. Current branch priority at `core/comparison.py:461-475` erases the interest metric/comparison. This requires a second fix at the branch-selection/intent-model level, as described in L-2.
- Candidate-name preservation is essential. `A ofron Banka Xyzzy kredi?` must remain `unknown_bank`; the existing test at `tests/test_structured_rate_seam.py:375-378` enforces that. Rules that merely see any offer/product token and return `False` would break it.
- Token/name collisions are unavoidable with a deletion-based heuristic. `Banka Kredi`, `Banka Ofron`, and already today `Banka Interes` illustrate forms that can slip to all-scope. The parser should recognize bounded grammatical structure rather than globally declaring offer/product words incapable of being part of a supplied name.

Blast radius: `_explicit_unknown_bank()` is called by the one `_bank_scope()` used in both availability (`core/comparison.py:461-475`) and value/comparison parsing (`core/comparison.py:477-555`). Changing `True` to `False` can turn a terminal `unknown_bank` into all-scope resolved data, `missing_product`, `conflicting_slots`, or `missing_key`. Its second branch also protects mixed named comparison lists (`core/comparison.py:260-279`) and should not be weakened incidentally. In the call center, only `unknown_bank` and `conflicting_slots` are terminal catalog declines (`core/comparison.py:446-448`, `core/callcenter.py:641-657`); other results may fall through to dense routing.

Best placement: keep bank-name-vs-generic-noun syntax in `_explicit_unknown_bank()` (consistent with its docstring), not as an overriding special case in `_bank_scope()`. Separately fix availability eligibility/precedence in `parse_rate_intent()` so metric-bearing offer asks are never flattened to availability.

### C. TYPO (`americane` vs `amerikane`) — CONFIRMED

- `fold()` case-folds and removes combining marks only; it does not substitute `c` with `k` (`core/text_norm.py:8-14`).
- `restore_diacritics()` is exact and lexicon-bounded, and its current mapping has no `americane` entry (`core/text_norm.py:22-40`, `core/text_norm.py:45-69`).
- Aliases are corpus-derived and retained only when unambiguous (`core/comparison.py:204-228`), then matched with exact word boundaries (`core/comparison.py:231-239`). The observed American-bank aliases were `banka amerikane e investimeve shqiperi`, `amerikane investimeve`, `amerikane`, and `bai`; none contains `americane`.
- The exact typo parsed `unsupported/unknown_bank`. Correctly spelled `amerikane` selected `Banka Amerikane e Investimeve Shqiperi`; that broad fee wording then returned `missing_product`, which is allowed to fall back to dense retrieval rather than getting the unknown-bank clarification.

An explicit alias `americane` would map uniquely to `Banka Amerikane e Investimeve Shqiperi`: none of the other ten canonical labels has it as a word, prefix, or suffix, so this exact bounded alias creates no current catalog ambiguity. A global c↔k fuzzy rule is different and remains contrary to the stated no-fuzzy-matching non-goal (`.hermes_codex_unknown_bank_audit_brief.md:112-115`). That constraint is relevant to broad normalization, but it does not prohibit a deliberately curated exact typo alias if product policy chooses to support it. Given the conservative exact-match design, treating the typo as unknown is currently expected behavior rather than a separate parser regression.

### D. FAILURE MODE 2 LOCUS — PARTIALLY

Hermes was right that an empty generated/fidelity-authorized answer can emit the observed text, but wrong to equate that text with the answerability abstain.

- The exact code string (with a typographic apostrophe) is `NO_EVIDENCE_MESSAGE` in `core/trust.py:21-24`.
- The answerability gate’s `ABSTAIN_MESSAGE` begins “Nuk kam një përgjigje të saktë…” and is different (`core/answerability.py:51-55`). API lines `598-614` emit that different message on an `UNSUPPORTED` answerability verdict.
- A dense retrieval refusal emits `NO_EVIDENCE_MESSAGE` at `core/api.py:588-597`, but returns before source objects are populated at `core/api.py:615-620`; it therefore does not fit “sources listed.”
- After accepted hits are added to `sources`, generation flows through `authorized_sentences()` (`core/api.py:187-229`, `core/api.py:648-658`). If the resulting `full_answer` is empty, API lines `661-665` emit `NO_EVIDENCE_MESSAGE`, and lines `686-689` include the already-populated sources. This is the best code-level match for the reported box.
- The uncommitted structured empty-render branch can also emit `NO_EVIDENCE_MESSAGE` with sources (`core/api.py:622-647`), but the misspelled question cannot reach it when the structured flag is enabled because its parse has no `rate_intent`.

For the exact typo, `parse_rate_intent()` returned `unsupported/unknown_bank`, and an enabled structured `decide()` probe returned terminal `clarify/catalog_unknown_bank`. That happens at `core/callcenter.py:638-657`; API exits on the decision at `core/api.py:521-541`, before retrieval and answerability. Thus the typo is the **primary** cause in structured mode, not a secondary wrinkle. The displayed “Nuk gjeta…” response plus sources must have come from a mode/revision where the structured terminal path did not run (most plausibly the feature flag was off, the deployed revision differed, or an older flow was active). That live state is unverifiable without the prohibited server/config/model checks.

Committed HEAD already contains the dense distinction: HEAD `core/api.py:581-600` handles retrieval/answerability before sources; HEAD `core/api.py:601-645` populates sources and emits `NO_EVIDENCE_MESSAGE` for empty generation. The structured unknown-bank path is uncommitted only.

### E. RELATED DEFECTS — CONFIRMED

The same neighborhood contains the following additional risks:

- L-3: the destructive bank-tail splitter also hides some explicit unknown names and defaults them to all banks.
- L-4: `ne shqiperi` conflicts with any named bank even when it is merely geographic context.
- L-5: natural offer vocabulary outside `ofroj*` bypasses deterministic availability.
- S-1: the same user-facing refusal prose labels multiple internal failure loci.
- D-1: the primary helper’s contract contradicts its behavior.

One suspected duplication is **not** present: `rag.retrieve_evidence()` and `rag.ask()` do not re-parse the rate question. The parse occurs once in `core/callcenter.py:635-640`, the typed intent is carried through `core/api.py:543-580`, and `core/rag.py:166-184` consumes that intent. The compatibility `ask()` similarly obtains the intent from `decide()` and carries it at `core/rag.py:254-288`. This is coherent working-tree plumbing, although it is uncommitted and absent from HEAD.

The smalltalk note was not elevated to a finding. The observed repeated canned response is consistent with deterministic fragment/meta or semantic smalltalk handling, and no live/model call was authorized to adjudicate its exact route.

### F. TEST COVERAGE — CONFIRMED

Current tests do **not** pin the buggy all-bank behavior as expected, but they also do not cover the failing grammar.

- `test_parse_availability_named_banks_resolves_offer` and `test_parse_availability_bare_kredi_family` cover named lists without a `banka/bankat` noun (`tests/test_structured_rate_seam.py:353-373`).
- `test_followup_availability_offered_verb_resolves` covers a no-bank follow-up and asserts availability, but not explicit all-scope or complete output (`tests/test_structured_rate_seam.py:391-396`).
- `test_all_bank_phrases_return_complete_stable_family` covers ordinary debit-fee lookup, not availability (`tests/test_structured_rate_seam.py:79-88`).
- `test_unknown_only_and_mixed_bank_are_terminal` correctly protects explicit/mixed unknown names (`tests/test_structured_rate_seam.py:91-101`).
- `test_availability_unknown_bank_is_unsupported` correctly protects `Banka Xyzzy` in an availability question (`tests/test_structured_rate_seam.py:375-378`).

A faithful syntax-aware fix that leaves unexplained candidate tokens intact should break none of the current tests. A broad “offer/product token present ⇒ return False” implementation would break `test_availability_unknown_bank_is_unsupported` by turning `Banka Xyzzy` into all-scope availability. Tests should be added for the failing `cilat banka ...` shape, singular/plural and reversed-order variants, explicit unknown names mixed with offer/product tokens, and metric-constrained offers such as the original low-interest question.

The authorized focused run passed all 44 tests, confirming the gap rather than refuting the defect. Since both structured test files are untracked, committed HEAD has no such regression coverage at all.

## FINAL SUMMARY

- **Severity counts:** P0: 0; P1: 2; P2: 4; P3: 2; total findings: 8.
- **Most important logic finding:** L-1 confirms the false `unknown_bank` causal chain; L-2 shows that the proposed scope-only fix would then discard the original low-interest constraint and answer generic availability.
- **Most important structure finding:** S-2—44 focused tests pass without exercising the failing bank-noun availability grammar; the structured tests are untracked and absent from HEAD.
- **Most important docs-vs-code finding:** D-1—the primary helper promises not to classify implicit all-bank asks as unknown, but does so for the audited question.
- **Report path:** `/home/egigj/projects/BoABot/audit_unknown_bank_report.md`
- **Could not be verified:**
  - Whether `BOABOT_COMPARISON_STRUCTURED` was enabled for each live transcript turn.
  - Which commit/working-tree revision served the live transcript.
  - The exact retrieved hits, scores, and source payload shown for failure mode 2.
  - Whether the model returned no content or `authorized_sentences()` rejected every generated sentence in that live turn.
  - Any live database, server, provider, model, or LLM behavior; those checks were explicitly prohibited.
  - The exact live smalltalk routing for “cna thua?” / “nga behesh?” without invoking the prohibited router/model path.

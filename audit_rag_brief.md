# RAG / Callcenter Audit Brief — BoABot

You are an experienced engineer auditing the question-answering stack of the Albanian
banking-regulations assistant "BoABot" at /home/egigj/projects/BoABot .

## REPORTED SYMPTOM
In most cases, even for simple tariff/rate questions, the assistant returns THIS exact default
instead of an answer:

"Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. Mos ndani PIN-in,
fjalëkalimin ose kodet e verifikimit në këtë bisedë."

That string is `HANDOFF_MESSAGE` defined in callcenter.py (lines 35-38). Production evidence of it
firing on benign questions is in ARM_AB_LIVE_EVAL_2026-08-14.md lines 169-182 (e.g. "Sa është
interesi për 1 depozitë pa afat raiffeisen" -> handoff), and the corresponding
ARM_AB_LIVE_EVAL_2026-08-14_results.json shows answer_text == HANDOFF_MESSAGE across the board.

## YOUR GOAL
Audit HOW the RAG + callcenter router operate, determine WHY simple questions fail to be answered
(instead landing on the safety handoff or clarification), and review ALL the routing/decision
scenarios the system considers. Produce a written report (audit_rag_report.md in the repo root)
and, where safe and verifiable, fix root causes. Do NOT introduce drive-by refactors.

## THE FLOW (two entry points both matter)
- `api.py` `POST /turn` imports `decide` from callcenter and `retrieve_evidence`/`grounded_messages`
  from rag. Read api.py to see how `decide().outcome` dispatches to ANSWER vs HANDOFF/CLARIFY/etc.
- `callcenter.decide(question, last_answer, history)` (callcenter.py line 212) is the deterministic
  pre-router. It returns Outcome.HANDOFF with HANDOFF_MESSAGE via several independent paths:
    1. `input_gate` rejection -> UNSAFE (not handoff, but relevant).
    2. `_is_repeat` -> REPEAT.
    3. `_SECRET_FAST_RE` regex (line 108) -> HANDOFF, pii_redacted.
    4. `_needs_missing_context_clarification` -> CLARIFY.
    5. `_is_explicitly_unsupported` -> UNSUPPORTED.
    6. `is_ambiguous_card_maintenance` -> CLARIFY.
    7. `is_business_deposit_question` -> UNSUPPORTED.
    8. `_redact_pii` true (email/phone/long number) -> HANDOFF, pii_redacted.
    9. len < 2 words -> CLARIFY.
    10. **Nearest-neighbour handoff probe:** `_probe_score` (line 202) using frozen embeddings
        from handoff_probe.json; if handoff_score >= `_HANDOFF_THRESHOLD` (0.04658478) AND NOT
        `_is_contextual_public_pricing_question` -> HANDOFF. (line 246-251). Probe metadata:
        k=1, dim=1024, 233 train exemplars (191 positive handoff, 42 negative), margin threshold =
        0.04658478498458862, model bge-m3 embeddings.
    11. len < 3 words -> CLARIFY.
    Otherwise returns Decision(outcome=None) and the question proceeds to RAG.

- `rag.ask`/`retrieve_evidence` (rag.py) is the retrieval+grounding path. After the router passes,
  it: rewrites elliptical queries, retrieves chunks from Postgres+pgvector (retrieve.py, dense bge-m3
  cosine, or opt-in hybrid RRF), applies heuristic reranking (`_rerank_hits`), pins exact
  doc/articles, then gates via `trust.trusted_hits` which needs best_score >= MIN_RELEVANCE_SCORE
  (0.50) and enforces a "rate-family" gate (a price/rate question without any rate_* chunk is refused
  as NO_EVIDENCE_MESSAGE). Failing that -> "Nuk gjeta burim..." refusal.

## KEY FILES
- callcenter.py — the router (where HANDOFF is produced). Freeze artifacts: handoff_probe.json,
  handoff_phrases.jsonl, handoff_split*.json, eval_handoff.py.
- trust.py — input_gate, is_business_deposit_question, trusted_hits, BANK_NAMES from rate_tables.jsonl.
- rag.py — rewrite/needs_rewrite, retrieve_evidence, grounded_messages, ask.
- retrieve.py — dense/hybrid retrieval, fetch_chunks_by_ids, fetch_doc_article.
- api.py — /turn dispatch wiring.
- ARM_AB_LIVE_EVAL_2026-08-14.md / _results.json — live arm A/B eval evidence of the failure.
- QA_FIXTURE.md — curated QA; DEVELOPMENT_ISSUES.md — known issues write-up.

## QUESTIONS TO ANSWER IN THE REPORT
1. Trace the exact decision path(s) a benign rate question like "Sa është interesi për 1 depozitë
   pa afat raiffeisen" takes. Which of the 11 router branches fires, and why?
   - Specifically check whether `_is_contextual_public_pricing_question` (line 172) succeeds in
     overriding the probe. Does `_is_public_pricing_question` actually match these Albanian phrasings
     (note: "sa eshte" and "depozit" should match — verify against the real corpus of failed questions
     in the ARM eval results). Look for phrasing variants in the eval that slip past the whitelist.
2. Is the NN handoff probe (threshold 0.0466, 191 positive/42 negative exemplars) too aggressive /
   over-fit? Check handoff_phrases.jsonl and handoff_split_grouped.json to see what "positive"
   handoff phrases look like vs the failing benign pricing questions — do benign rate questions sit
   near handoff-positive neighbours? Consider sensitivity: probe has k=1 and only 42 negatives.
3. Evaluate `trusted_hits` MIN_RELEVANCE_SCORE=0.50 and the "rate-family"/wrong_chunk_family gate in
   trust.py: could it refuse queries that DO have a correct rate_* answer (e.g. retrieval returns the
   right row but the wrong_chunk_family heuristic rejects it)? Check _rerank_hits and the candidate_k
   logic in rag.py.
4. Review ALL the decision scenarios the router "considers" (the list above) and flag any where a
   legitimate, answerable question gets mis-routed (e.g. an over-broad regex, PII redaction catching
   a phone-like number in a tariff string, business-deposit detection too broad, etc.).
5. Distinguish which failures are router-side (callcenter) vs retrieval/grounding-side (rag/trust).

## CONSTRAINTS
- This is an audit-first task. Read carefully and be precise with line numbers and exact strings.
- Run read-only diagnostics where useful (you may inspect JSONLs, probe vectors, run python snippets
  that DON'T require the live Postgres or model inference if unavailable; if live DB/model ARE
  available, you may run `python retrieve.py` / small scripts to reproduce retrieval).
- Do not edit .env or read secrets. DB password is literally 'boa' (psycopg url
  postgresql://boa:boa@127.0.0.1:5433/boa) — tool output may redact it as '***'.
- Document every finding with file:line references. Provide concrete, minimal, safe fixes for each
  root cause you identify, and apply them if they are low-risk and you can verify them (run the
  relevant tests / a repror script). If a fix changes router behaviour, add/adjust a test under
  voice/tests or the existing test suite if directly applicable.
- Write the full audit to audit_rag_report.md in the repo root with: executive summary, the traced
  decision path(s), root causes ranked by impact, all scenarios reviewed, recommended fixes, and
  which fixes you applied (with diff/test evidence).

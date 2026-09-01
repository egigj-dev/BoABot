# Codex brief — P0A commit 1 (types + row schema, ZERO behavior change)

You are implementing commit 1 of the BoABot P0A plan (`P0A_IMPL_PLAN.md`, committed
at repo root — read it first, especially sections 1A/1B and the acceptance tests).
The repository is a clean baseline commit (`git status` empty). Your job is
EXACTLY the scope below. Anything else in the plan is FORBIDDEN in this commit —
later commits own it.

## Project invariants you must not break

- `.venv/bin/python` is the project interpreter (psycopg, torch, sentence_transformers).
- The pytest suite must run WITHOUT these env vars exported:
  BOABOT_COMPARISON_STRUCTURED, BOABOT_LLM_ROUTER, BOABOT_LLM_ANSWERABILITY,
  OPENROUTER_API_KEY, DEEPSEEK_API_KEY. You MUST export:
  BOABOT_DSN='postgresql://boa:boa@127.0.0.1:5433/boa'
  (required or the suite fails with "no password supplied").
- Do NOT source .env. Do NOT restart any server.
- The frozen design invariant:
  "The structured seam may generalize only what the user explicitly left broad;
  it may never generalize what the parser failed to understand."

## EXACT scope of commit 1 — types and data schema ONLY

### 1. core/comparison.py — new types (additive, no logic changes)

- `SlotState(Enum)`: EXPLICIT | INHERITED | WILDCARD | MISSING.
- `ResolvedSlot[T]` (or equivalent minimal generic wrapper): `value: T | None`,
  `state: SlotState`. Add a small constructor/helper if useful. Do NOT wrap any
  existing RateIntent fields yet — wrapping is commit 3. The types must exist and
  be importable; nothing may consume them at runtime in this commit.
- `StructuredIntentStatus(Enum)`: FULL_STRUCTURED_INTENT |
  UNREPRESENTED_SEMANTICS | INSUFFICIENT_COMPARISON_DIMENSIONS. Types only.
- Write the invariant as the module/class docstring (one line, quoted above).

### 2. core/callcenter.py — new types (additive, no logic changes)

- `ContextEffect(Enum)`: PRESERVE | REPLACE | CLEAR (frame lifecycle; used in
  commit 4 — types only now).
- `DecisionEvent(Enum)`: context_inherited | query_rewritten |
  structured_lookup | unresolved_qualifier_detected | fidelity_sentence_drop.
- Add `trace_flags: frozenset[DecisionEvent] = frozenset()` as a NEW defaulted
  kw_only field to the frozen `Decision` dataclass — backward compatible, all
  existing constructions unchanged. Do NOT wire it anywhere; do NOT add new
  DecisionReason members (that is commit 3/6 work).

### 3. rate_tables.jsonl + loader — row-level materialized fields

Add TWO new per-row fields to every row in `rate_tables.jsonl` (119 rows),
derived DETERMINISTICALLY, never inferred from query text:

- `customer_segment`: "individual" | "business", FROM THE SOURCE TABLE identity:
    - source contains "individë" (i.e. "Komisionet për individë",
      "Normat e interesit të depozitave", "Normat nominale dhe NEI për
      individë") -> "individual"
    - source contains "biznese" ("Komisionet për biznese",
      "Normat nominale dhe NEI për bizneset") -> "business"
- `currency`: "ALL" for every row (the BoA pages scraped are the lekë tables;
  the row text carries no currency marker — verified). Add a short code comment
  in the loader/parser noting currency-specific pages would carry their own
  value; do not block on it.

The loader path (`_rate_rows()` / any reader of rate_tables.jsonl) must stay
BACKWARD COMPATIBLE: rows missing `currency`/`customer_segment` -> None, and
row dicts gain the fields when present (so future commits can read them).
Do NOT change what the resolver matches or renders.

### 4. tests/test_p0a_commit1.py — zero-behavior proof

- Assert the new enums exist with exactly the member sets above, and that
  `Decision(...)` constructs with default `trace_flags == frozenset()` (and an
  explicit custom frozenset is accepted).
- Zero-behavior-diff test: take the live row set from `_rate_rows()`; build a
  "legacy" copy with `currency`/`customer_segment` stripped and the real copy
  with them present; run `parse_rate_intent_hybrid` + `structured_rate_hits`
  + `render_rate_answer` over a FIXED query list (include: "cilat jane normat e
  interesit per depozita?", "cilat banka ofrojne kredi konsumatore?",
  "krahaso BKT, Credins dhe OTP per komisione", "tarifat e kartes se debitit",
  "po per kredi?" — note this one currently parses not_rate and must stay
  not_rate at THIS commit); assert the resolutions and rendered strings are
  IDENTICAL between legacy and real row sets. This proves commit 1 changed no
  behavior.
- If `_rate_rows` is a private/cached function, structure the test to call the
  same machinery both ways (e.g. parse/resolve over row lists, not the cache).

## Strict non-goals (later commits own these — DO NOT TOUCH)

- `resolve_rate_rows` wildcard semantics (product=None/metric=None behavior) and
  the three known wildcard construction sites (comparison.py ~685-705, ~654-667).
- `parse_rate_intent` / `parse_rate_intent_hybrid` logic; no SpanState, no
  coverage certification, no unresolved_qualifiers.
- `_structured_rate_decision` / `decide()` / `_fragment_meta_preflight` /
  negation / legal floors / personal_record (does not exist yet).
- No new DecisionReason members. No ContextEffect wiring. No SessionStore fields
  (last_structured_frame is commit 4).
- No rendering, citation, breadth, or prompt changes.
- Do NOT reformat/refactor existing code; no renames; no "while I'm here" fixes.

## Commands / verification (run and report actual output)

1. `cd ~/projects/BoABot`
2. `export BOABOT_DSN='postgresql://boa:boa@127.0.0.1:5433/boa'`
   (and confirm the forbidden env vars are NOT exported: `env | grep BOABOT`)
3. `.venv/bin/python -m pytest tests/ voice/tests/ -q` — the FULL suite must
   pass with the SAME count as before your change (baseline: run it once before
   editing, then after; report both numbers; a change in count must be explained
   by YOUR added tests only, and the added tests must all pass).
4. `.venv/bin/python -c "import core.comparison, core.callcenter"` import check.
5. Quick JSONL sanity: `jq`-free check that all 119 rows have the two fields
   with expected values (write a 5-line python check; report per-source counts).

## Commit

Single commit, ONLY:

- core/comparison.py (type additions), core/callcenter.py (type additions),
  rate_tables.jsonl, the loader's backward-compatible reader change (same file
  as the types if that's where the reader lives — check; comparison.py),
  tests/test_p0a_commit1.py.

Message exactly: `p0a(1): typed slot states + row currency/segment schema — zero behavior change`

Do not commit anything else. Do not amend. Report: the before/after suite
counts, the per-source customer_segment/currency counts, the diff --stat, and
any deviation from this brief. If you believe a deviation is REQUIRED, stop and
report instead of doing it.
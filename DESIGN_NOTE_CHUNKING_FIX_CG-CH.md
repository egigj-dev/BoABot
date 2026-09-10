# Chunking fix selection (Step CI) — informed by CG and CH

Status: GATE, no implementation. Companion report to CG (degenerate populations)
and CH (split articles).

## Ground truth established by CG/CH

- 63 NSFR `art=5055` rows: real table content ("Dispozitë e fundit" closing
  article; rows carry weighting factors, `%` values, 98-117 words each).
  NOT pure noise — answer-bearing liquidity table rows at the wrong unit.
- 68 collision chunks (13 docs): real regulatory articles with a polluted
  article label (1012/1252/1727...). True Neni number is NOT in the chunk text
  (0/68 have a clean internal heading); the doc-sequence heuristic recovered
  1/68; the real recoverability path is the source PDF.
- 276 clean articles split across 1,166 chunks; median size 4,731 vs 926 for
  unsplit; ~25% of splits are mid-sentence (genuinely severed), ~75% at clean
  sub-headings. **7 of the eval's 18 reg golds sit in a split article.**

## Options compared

### (a) Query-time filter (exclude art>=1000)
- CG population: excludes the 63 NSFR rows AND the 68 collisions — but the
  63 rows are answer-bearing; filtering them removes real liquidity content.
  The 68 collisions are real regulation text; a filter silently drops them.
- CH splits: completely untouched (splits are clean small-int articles).
- Re-index cost: none. Gold set: unchanged.
- Risk: worst option for CG's content-value; best for speed.

### (b) Parser fix at ingestion (tighten ARTICLE_HEADING)
- Fixes the EMITTER: `FILE_HEADING re ^Neni\s+(\d+(?:/\d+)?)` accepts bare
  digit runs. Tightening to require a plausible article number (< some cap,
  or reject all-digit runs >= 1000) stops NEW collisions at source. Then a
  one-time repair pass fixes the 131 existing (63 dropped-or-retabled as
  table unit, 68 relabeled from PDF).
- CH splits: NOT touched — splits are a chunk-size mechanism, not the header
  parser. Requires a full re-embed after the repair pass.
- Re-index cost: full re-embed of 3,434 chunks (bge-m3, ~hours on CPU /
  T4 in Colab).
- Gold set: chunk ids change on re-embed -> `eval_handwritten_relabeled.jsonl`
  invalidated -> must re-run relabel.py. BUT relabel's KEEP-LIVE is unsound
  (reg_00538/02550 were live+wrong) — the re-run must go through the QA-1 gate
  PLUS manual inspection of the flagged set before accepting new golds.
- Risk: highest correctness payoff (fixes emitter + hides nothing), but the
  full re-embed + relabel cycle is the largest and must be paired: "any re-chunk
  must be paired with a relabel run."

### (c) Re-chunk (address splits as well)
- Fixes splits (276 articles -> merge/size-cap the boundaries) AND degenerates.
- Requires re-embed + relabel, plus a NEW split policy -> the gold set changes
  again (7 golds in split articles will move). Highest risk, highest coverage.
- Gold-set survival: only survives if relabel's QA-1 gate + manual inspection
  are mandatory (the current gate already flags reg_02550; the split context
  means a gold's answer may sit in the OTHER half — the relabel must pick the
  chunk that ANSWERS, not the first of the split).

## Recommendation

**Option (b)** — parser fix at ingestion + a data repair pass — is the only one
that fixes the emitter and preserves the answered-bearing 63 NSFR rows (as a
retabled unit) and relabels the 68 collisions from the PDF. (a) destroys
content; (c) is a much larger project that CH shows is ~75% unnecessary
(clean sub-head splits) for 25% gain (mid-sentence), and it re-breaks the gold
set harder.

Gold-set survival for (b): re-run relabel.py after re-embed with the CURRENT
committed code (QA-1 gate on), then manually inspect every FLAGGED + every gold
that lands in a re-chunked article, replacing any that KEEP-LIVE accepts
unsoundly. Only then re-run the eval.

Cost table:
| option | NSFR rows | 68 collisions | 276 splits | re-index | gold set |
|---|---|---|---|---|---|
| (a) filter | dropped (bad) | dropped (bad) | untouched | none | intact |
| (b) parser+repair | retabled | relabeled from PDF | untouched | full re-embed | relabel re-run |
| (c) re-chunk | fixed | fixed | fixed | full re-embed | relabel re-run + re-split policy |

Recommend (b). Decision is the human's.
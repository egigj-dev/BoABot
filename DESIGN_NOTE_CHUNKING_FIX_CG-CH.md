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
  unsplit. **CL (exact count, mechanical definition — boundary ends neither on
  terminal punctuation nor on a numbered/lettered heading):** 213 (77%) at
  clean sub-headings, **63 (23%) mid-sentence**, all 63 with neither half
  ending on a terminal (answer-bearing severances). **5 of the 18 eval reg
  golds sit in a mid-sentence split** (reg_00203, 03181, 02550, 03916,
  01252) — the same questions as the genuine-ranking misses, so the severance
  contributes directly to the residual RegArt gap.
- **CM (PDF re-parse scope):** NO source PDFs exist locally (the find across
  repo/tmp/home returned none) and **no PDF-extraction pipeline exists in the
  tree** (no fitz/pymupdf/pdfplumber; the original PDF → chunks step is not in
  this repo — pdf_text.jsonl is gitignored/absent). The 13 collision-doc PDFs
  are **all re-scrapable** (their full URLs are on bankofalbania.org, verified).
  The current ARTICLE_HEADING regex (`^\d+(?:/\d+)?$` at a line start) is why
  table row numbers parsed as article numbers; recovery of the TRUE Neni for
  the 68 collision chunks therefore requires **re-downloading 13 PDFs +
  re-extracting** — semi-automatic at best (per-document review), not a code
  fix alone.

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
that fixes the emitter and preserves the answer-bearing 63 NSFR rows (as a
retabled unit) and relabels the 68 collisions from the PDF. (a) destroys
content; (c) is a much larger project (CL: 63 mid-sentence severances + a new
split policy, re-breaking the gold set harder).
**Scope limitation made explicit:** (b) fixes the emitter and the labels but
**does NOT fix the 63 mid-sentence splits** (reg_00203/03181/02550/03916/01252
golds included). The chunk-size mechanism behind splits is untouched by (b);
only (c)'s re-chunk addresses them. A human choosing (b) must accept the
mid-sentence severances remain, or pair (b) with a subsequent (c).

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
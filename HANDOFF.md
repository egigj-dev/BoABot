# BoABot — project state and next steps

RAG chatbot over Bank of Albania public regulations and comparative bank tariff
tables. Albanian language. Portfolio project for a fintech job application; the
requested deliverable is a voice chatbot, being built text-first for cost
reasons.

## Environment

- **VPS:** GCP VM, `~/projects/BoABot`, venv active, Python 3.11. Runs the
  database, retrieval, eval, and the API. No GPU.
- **Notebook:** separate Colab session with GPU, corpus at `/content/boa_corpus`
  (referred to as `OUT`). Used **only** for embedding with bge-m3.
- **Postgres + pgvector:** container `boabot-postgres`, host port **5433**
  (5432 is taken by an unrelated service — do not use it).
  `DSN = "postgresql://boa:boa@127.0.0.1:5433/boa"` lives in `config.py`.
- Other containers on this box, unrelated, do not touch: `tiranatips-frontend`,
  `tiranatips-api`, `tirantips-postgres`, `wikijs`, `qdrant`.

Files move between the two environments by manual upload/download. A fix applied
on the VPS does not exist in Colab until the file is uploaded, and vice versa.
**This has already caused one wasted embedding run.**

## Current state

Ingestion is complete, retrieval works and is measured. One data-quality defect
is outstanding.

| Artifact | Count | Notes |
|---|---|---|
| `chunks.jsonl` | 4,049 | regulation chunks from 98 PDFs (2,306 pages) |
| `rate_tables.jsonl` | 123 | comparative bank fee/rate rows — **defective, see below** |
| `embedded.parquet` | 4,172 × 1024 | bge-m3, normalized (norm = 1.0), no duplicate ids |
| `eval_retrieval.jsonl` | 40 | generated questions, gold chunk known by construction |
| `eval_faq.jsonl` | 13 | institution-authored Q&A, independent of the chunks |
| `pdf_text.jsonl` | 98 | raw extracted PDF text, kept for re-chunking |
| `manifest.jsonl` | 306 | crawl record |

Scripts on the VPS: `config.py` (DSN), `load.py` (parquet → pgvector),
`retrieve.py` (the single retrieval function), `eval.py` (recall@k),
`rag.py` (drafted, not yet run).

Database is loaded: 4,172 rows, 4,172 vectors.

**Measured (on defective chunks — will change):** recall@1 0.625, recall@5
0.925, median latency 188ms, p95 223ms. All 3 misses were malformed eval
questions, not retrieval failures.

### Chunk metadata

Every chunk carries `status`: `canonical` (189) | `base` (3,808) |
`amendment` (107) | `superseded` (68). Retrieval filters to
`status IN ('canonical','base')`. This is load-bearing correctness logic:
regulation nr. 63 exists as both the Nov 2020 original and a later integrated
revision, and indexing both would surface repealed provisions.

Regulation chunks are split on `Neni N` article boundaries (89% article-bound,
median 1,566 chars), with document title and article number prepended to every
chunk so retrieved fragments stay citable.

---

## Immediate task

### The defect

The loan rate tables are keyed by **maturity band**, not fee name. Rows labeled
`0-12`, `13-24`, `241-360` are month ranges; `Muaj` is a bare column header
carrying no data. A chunk currently reads `KREDI PER SHTEPI/PRONA — 13-24`,
which embeds poorly and reads worse. It should read
`KREDI PER SHTEPI/PRONA — maturitet 13-24 muaj`.

Roughly 20 relabels and 20 drops out of 123 rate chunks. The 4,049 regulation
chunks are unaffected.

### Sequence — order matters

**1. VPS.** Create and run `~/projects/BoABot/fix_rates.py`:

```python
# Relabel maturity-band rows so chunk text is self-describing.
# BAND : matches "0-12", "241-360" — month ranges masquerading as fee names
import json, re

BAND = re.compile(r"^\d+\s*-\s*\d+$")
rates = [json.loads(l) for l in open("rate_tables.jsonl", encoding="utf-8")]

fixed, out = 0, []
for r in rates:
    if r["item"].strip().lower() == "muaj":      # bare column header, no data
        continue
    if BAND.match(r["item"].strip()):
        r["item"] = f"maturitet {r['item'].strip()} muaj"
        r["text"] = f"{r['source']} — {r['category']} — {r['item']}\n" + \
                    r["text"].split("\n", 1)[1]
        fixed += 1
    out.append(r)

with open("rate_tables.jsonl", "w", encoding="utf-8") as f:
    for r in out:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"{fixed} relabelled, {len(rates)-len(out)} dropped, {len(out)} remain")
```

Verify: ~20 relabelled, ~20 dropped, ~103 remain.

**2. Upload** the corrected `rate_tables.jsonl` to Colab, overwriting
`{OUT}/rate_tables.jsonl`.

**3. Notebook — gate before spending GPU time:**

```python
rates = [json.loads(l) for l in open(f"{OUT}/rate_tables.jsonl", encoding="utf-8")]
print("total:", len(rates))
print("maturitet labels:", sum(1 for r in rates if "maturitet" in r["item"]))
```

Expect ~103 total and ~20 `maturitet` labels. **If `maturitet` is 0, stop** —
the upload did not land, and embedding would burn GPU time on the same broken
labels. This exact check already caught one wasted run.

**4. Notebook — re-embed.** Use the full embed cell that reads both files fresh
and **overwrites** `embedded.parquet`. Do not use an incremental merge cell that
strips `rate_*` and concatenates — the merge path produced a confusing
`4849 + 123` count earlier. A full re-embed is ~12 minutes on a T4 and
eliminates a class of state bugs.

The cell must:
- read `chunks.jsonl` and `rate_tables.jsonl`
- keep ids `reg_NNNNN` (already present); assign `rate_NNNN` by enumeration order
- assert ids are unique before encoding
- encode with `normalize_embeddings=True`
- write `embedded.parquet` with columns
  `id, doc, article, status, section, url, text, embedding`,
  where `embedding` is `np.float32` bytes

Expected final shape: **4,049 + ~103 = ~4,152 rows × 1024 dims.**

**5. Download** `embedded.parquet` back to `~/projects/BoABot/`.

**6. VPS.** Chunk ids shifted, so `eval_retrieval.jsonl` gold_ids are stale.
Regenerate the eval set from the corrected rate chunks, then:

```bash
python3 load.py && python3 eval.py
```

`load.py` drops and recreates the table, so re-running is safe.

Report the corrected recall figures. **Do not keep the old 0.925** — it was
measured on mislabeled chunks. Reporting the flattering number would be exactly
the failure mode this project exists to demonstrate avoiding.

---

## Then — the service

`rag.py` is drafted: DeepSeek `deepseek-v4-flash` with `retrieve` exposed as a
**tool call**, not pre-injected context. This matters — Gemini Live (the
production target for voice) is native speech-to-speech with no prompt-assembly
step to hook, so retrieval must be a tool the model invokes. The same tool
schema works for both; only the transport changes.

`deepseek-chat` and `deepseek-reasoner` were retired 2026-07-24. Use
`deepseek-v4-flash`. Any tutorial found online will have the dead names.

Remaining, in order:

1. Wrap `ask()` in FastAPI with **SSE streaming**. Retrofitting this later is
   painful, and voice requires it (TTS must start before generation completes).
2. **Multi-turn query rewriting** — rewrite the query against the last 2–3 turns
   before embedding. Speech is elliptical ("and what about that one?") and
   embeds to nothing useful otherwise. Highest-impact remaining feature.
3. Caddy reverse proxy + SSL (auto-SSL, ~5 lines; simpler than Nginx here).
4. Voice: Gemini Live API, same tool schema.

---

## Known gaps — document, don't necessarily fix

- **Rate tables have no as-of date.** These are periodic snapshots; a chunk
  saying "Banka Tirana: 0.10" will silently go stale. The source pages carry a
  period label — extract it into metadata and chunk text.
- **Eval questions were generated from the chunks**, so wording overlaps
  heavily. This measures that retrieval isn't broken, not that it's good on real
  phrasing. The 13 FAQ pairs are independent. Worth hand-writing ~15 questions
  against the 4,049 regulation chunks — that set will be harder and more
  informative.
- **Bank name doesn't discriminate.** One rate chunk = one fee item across all
  12 banks, so recall measures item-matching only. The LLM extracts the right
  column. State this in the README.
- **6 PDFs are scanned images** (0 chars/page), excluded. Only
  `Rregullore 57/2022` (payment services licensing) is worth OCR later.
- **Diacritics.** Source tables are written without them (`kredi per shtepi`).
  Store original text for display/embedding plus a folded variant for lexical
  matching — users type both.
- **No Albanian stemmer in Postgres.** Snowball doesn't ship one and Albanian is
  heavily inflected. If hybrid search is wanted, use bge-m3's sparse output
  rather than Postgres FTS.
- **`#MainContentWrapper` selector** was wrong for 5 accordion pages (fixed via
  Playwright `text_content`). May be wrong for others among the 189 HTML pages;
  those are not yet chunked or embedded (~50 useful chunks if filtered to pages
  over ~800 chars).
- **`ligjet`** (banking laws) yielded titles only. Consolidated texts are at
  qbz.gov.al, a better source.

## Constraints

- Corpus is BoA public content; BoA reserves copyright. Keep it local,
  **do not commit** the corpus, `embedded.parquet`, or `db/pgdata/` to a public
  repo. `.gitignore` is already set.
- Query and index vectors must come from the same model. bge-m3 runs on the VPS
  for query embedding (~2.2GB weights, ~190ms/query on CPU).
- ~4,150 vectors total. **No HNSW index** — flat scan is faster and has no
  recall penalty at this scale. Revisit past ~50k.
- Never attempt full-corpus embedding on the VPS. No GPU; CPU encoding is not
  viable.

## Working style

Surface assumptions and tradeoffs before implementing. Minimum code that solves
the problem; no speculative abstractions. Surgical edits — touch only what the
request requires. State a brief plan with verification steps for multi-step
tasks. Push back when a simpler approach exists.

The differentiator for this project is measured retrieval quality with honest
error analysis, not corpus size or architectural novelty.

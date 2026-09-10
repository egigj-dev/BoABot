# BoABot

BoABot is a FastAPI assistant for Albanian banking regulations and comparative bank fees. It uses guarded retrieval and server-authorized streaming responses; the voice layer never creates an independent answer.

## Production layout

```text
core/
  Core HTTP, policy, retrieval, grounding, and normalization service.

voice/shared/
  Shared voice contracts, settings, gates, TTS, and correlation utilities.
voice/arm_a/
  Modular Azure/Chirp ASR -> guarded `/turn` -> Azure TTS path.
voice/arm_b/
  Constrained Gemini Live -> guarded `/turn` -> gated rendering path.

db/
  PostgreSQL/pgvector local service and migration.
rate_tables.jsonl, handoff_probe.json
  Runtime artifacts loaded by trust and handoff policy.
  handoff_probe.json provenance: frozen k=1 grouped-train nearest-neighbour
  classifier (class-margin threshold) over bge-m3 embeddings, loaded at import
  by core/callcenter.py. Its source_sha256 binds it to
  handoff_split_grouped.json, which is NOT in this repository, and no
  generator script exists in the tree — the artifact is therefore not
  reproducible from this checkout alone. The split file lives in the
  pre-reorg lineage archive (retained outside git); do not regenerate or
  delete the artifact, and do not rebind it to a different source corpus
  without re-tuning the margin threshold.

Ingestion NOT reproducible from this repository (Step DC finding, same class
as handoff_probe.json): the live `chunks` table (3,434 rows) cannot be
regenerated from this checkout. No source PDFs exist in the tree, and no
PDF-extraction pipeline is present (no fitz/pymupdf/pdfplumber; the original
.pdf → text step left no code and its `pdf_text.jsonl` is gitignored/absent).
`scripts/rebuild_chunks.py` operates on existing DB rows, not files. A full
re-index therefore requires re-scraping the source PDFs from
bankofalbania.org (URLs are in chunk metadata) and rebuilding an extraction
pipeline first — a prerequisite shared by any chunking change, and a real gap
for a public repository.
```

## Run

Install the dependencies declared in `pyproject.toml` (and `voice/requirements.txt` for live provider adapters), configure the retained environment files, then start:

```bash
# Full behavior (LLM turn-router + answerability + structured-rate seam).
# Without the flags below the assistant answers only deterministic floors and
# falls back to lexical classification — greetings, rate lookups and transfer
# fees then degrade or refuse. Check the live mode with /health (returns
# "flags": {..., "BOABOT_COMPARISON_STRUCTURED": true, "BOABOT_LLM_ROUTER": true,
# "BOABOT_LLM_ANSWERABILITY": true}).
BOABOT_LLM_ROUTER=1 BOABOT_LLM_ANSWERABILITY=1 BOABOT_COMPARISON_STRUCTURED=1 \
  .venv/bin/python -m uvicorn core.api:app --host 127.0.0.1 --port 8000
```

Arm A is served with `uvicorn voice.arm_a.web_app:app --port 8100`; Arm B with `uvicorn voice.arm_b.web_app_b:app --port 8200`.

## Verify

```bash
.venv/bin/python -m pytest tests voice/tests -q
```

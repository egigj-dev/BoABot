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
```

## Run

Install the dependencies declared in `pyproject.toml` (and `voice/requirements.txt` for live provider adapters), configure the retained environment files, then start:

```bash
uvicorn core.api:app --host 127.0.0.1 --port 8000
```

Arm A is served with `uvicorn voice.arm_a.web_app:app --port 8100`; Arm B with `uvicorn voice.arm_b.web_app_b:app --port 8200`.

## Verify

```bash
.venv/bin/python -m pytest tests voice/tests -q
```

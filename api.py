# api.py — FastAPI wrapper with SSE.
# events: tool, token, error, done
import json
import logging
import math
import re
import time
from contextlib import asynccontextmanager
from typing import Any

import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from rag import (API, MODEL, RAGError, api_key, grounded_messages, needs_rewrite,
                 retrieve_evidence, rewrite)
from callcenter import HANDOFF_MESSAGE, Outcome, decide, sessions
from retrieve import embedding_stats
from retrieve import shutdown as shutdown_retrieval
from retrieve import warmup as warmup_retrieval
from trust import NO_EVIDENCE_MESSAGE

logger = logging.getLogger(__name__)
FIRST_TOKEN_BUDGET_MS = 6000
_SENTENCE_END_RE = re.compile(r"[.!?][\"'»”\)\]]?(?:\s|$)")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    warmup_retrieval()
    try:
        yield
    finally:
        logger.warning("query embedding reuse totals: %s", embedding_stats())
        shutdown_retrieval()


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def stream_answer(messages, session_id=None, usage=None):
    """Yield answer tokens and convert upstream protocol failures into RAG errors."""
    payload = {"model": MODEL, "messages": messages, "stream": True,
               "usage": {"include": True}}
    if session_id:
        # OpenRouter uses this as the sticky-routing key, keeping one conversation
        # on a cache-capable provider without changing model selection.
        payload["session_id"] = session_id
    started = time.perf_counter()
    content_yielded = False
    usage = usage if usage is not None else {}
    try:
        with requests.post(API, headers={"Authorization": f"Bearer {api_key()}"},
                           json=payload,
                           stream=True, timeout=(5, 10)) as response:
            response.raise_for_status()
            # OpenRouter may omit a charset on SSE responses; default decoding is then ISO-8859-1.
            response.encoding = "utf-8"
            for line in response.iter_lines(decode_unicode=True):
                # This budget fires when a line arrives, or via the read timeout;
                # it is not a hard wall-clock cancellation guarantee.
                if (not content_yielded
                        and (time.perf_counter() - started) * 1000 > FIRST_TOKEN_BUDGET_MS):
                    response.close()
                    raise RAGError("first-token budget exceeded")
                if not line or not line.startswith("data: "):
                    continue
                body = line[6:]
                if body.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(body)
                except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                    raise RAGError("Model provider returned an invalid stream") from exc
                chunk_usage = chunk.get("usage")
                if isinstance(chunk_usage, dict):
                    usage.clear()
                    usage.update(chunk_usage)
                choices = chunk.get("choices")
                if not choices and isinstance(chunk_usage, dict):
                    continue
                try:
                    delta = choices[0]["delta"]
                    content = delta.get("content")
                except (KeyError, IndexError, TypeError, AttributeError) as exc:
                    raise RAGError("Model provider returned an invalid stream") from exc
                if content:
                    content_yielded = True
                    yield content
    except requests.RequestException as exc:
        raise RAGError("Model provider stream failed") from exc


def source(hit: dict[str, Any]) -> dict[str, str]:
    """Return citation metadata without sending retrieved passages to the browser."""
    return {key: str(hit.get(key) or "") for key in ("id", "doc", "article", "url")}


@app.get("/health")
def health():
    return {"ok": True}

@app.get("/")
def index():
    return HTMLResponse(PAGE)

PAGE = r"""<!DOCTYPE html>
<html lang="sq">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BoABot — Banka e Shqipërisë</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         background:#111; color:#e0e0e0; height:100vh; display:flex; flex-direction:column; }
  header { background:#1a1a2e; padding:14px 24px; border-bottom:1px solid #333; }
  header h1 { font-size:18px; font-weight:600; color:#7c7cff; }
  header p { font-size:12px; color:#888; margin-top:2px; }
  #chat { flex:1; overflow-y:auto; padding:16px 24px; display:flex; flex-direction:column; gap:16px; }
  .msg { max-width:80%; padding:12px 16px; border-radius:10px; line-height:1.5; font-size:14px; }
  .msg.user { background:#2a2a4a; align-self:flex-end; border-bottom-right-radius:2px; }
  .msg.bot { background:#1e1e2e; align-self:flex-start; border-bottom-left-radius:2px; border:1px solid #333; }
  .msg.bot .tool-call { font-size:12px; color:#888; font-style:italic; margin-bottom:6px; }
  .msg.bot .sources { margin-top:10px; padding-top:8px; border-top:1px solid #333; font-size:12px; }
  .msg.bot .sources a { color:#7c7cff; text-decoration:none; display:block; margin:2px 0; }
  .msg.bot .sources a:hover { text-decoration:underline; }
  .msg.bot .sources span { color:#aaa; }
  .cursor { display:inline-block; width:6px; height:16px; background:#7c7cff; animation:blink .8s infinite; margin-left:2px; vertical-align:middle; }
  @keyframes blink { 50% { opacity:0; } }
  #input-bar { display:flex; gap:8px; padding:12px 24px; background:#1a1a2e; border-top:1px solid #333; }
  #input-bar input { flex:1; padding:10px 14px; border-radius:8px; border:1px solid #444;
                     background:#222; color:#e0e0e0; font-size:14px; outline:none; }
  #input-bar input:focus { border-color:#7c7cff; }
  #input-bar button { padding:10px 20px; border-radius:8px; border:none; background:#7c7cff;
                      color:#fff; font-size:14px; font-weight:600; cursor:pointer; }
  #input-bar button:disabled { opacity:.4; cursor:not-allowed; }
  #input-bar button:hover:not(:disabled) { background:#6a6aff; }
  .empty { flex:1; display:flex; align-items:center; justify-content:center; color:#555; font-size:15px; }
  .suggestions { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; margin-top:20px; }
  .suggestions button { background:#2a2a4a; border:1px solid #444; color:#aaa; padding:8px 14px;
                        border-radius:20px; cursor:pointer; font-size:13px; }
  .suggestions button:hover { border-color:#7c7cff; color:#ccc; }
  @media(max-width:600px){ .msg { max-width:95%; } }
</style>
</head>
<body>
<header>
  <h1>BoABot</h1>
  <p>Rregulloret bankare dhe tarifat — Banka e Shqipërisë</p>
</header>
<div id="chat"><div class="empty">
  <div style="text-align:center">
    <div style="font-size:40px;margin-bottom:10px;color:#7c7cff">⚖</div>
    <div>Pyetni për rregulloret bankare ose tarifat e bankave</div>
    <div class="suggestions">
      <button onclick="ask('Sa është komisioni për shlyerje të parakohshme të kredisë për shtëpi?')">Komision shlyerje kredie</button>
      <button onclick="ask('Kush administron Regjistrin e Kredive?')">Regjistri i Kredive</button>
      <button onclick="ask('Sa është norma e interesit për depozita me afat 12 mujor në Bankën Credins?')">Norma depozitash</button>
      <button onclick="ask('Cilat janë kërkesat për licencimin e një banke?')">Licencim banke</button>
    </div>
  </div>
</div></div>
<div id="input-bar">
  <input id="q" type="text" placeholder="Shkruani pyetjen në shqip..." autofocus>
  <button id="send" onclick="send()">Dërgo</button>
</div>
<script>
const chat = document.getElementById('chat');
const input = document.getElementById('q');
const sendBtn = document.getElementById('send');
let sessionId = null;

function addMsg(cls, html) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.innerHTML = html;
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
  return d;
}

function ask(q) {
  input.value = q;
  send();
}

input.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });
async function send() {
  const q = input.value.trim();
  if (!q) return;
  input.value = '';
  sendBtn.disabled = true;

  const empty = chat.querySelector('.empty');
  if (empty) empty.remove();
  addMsg('user', escapeHTML(q));

  const botDiv = addMsg('bot', '<div class="tool-call status"></div><div class="content"></div><div class="sources"></div>');
  const statusDiv = botDiv.querySelector('.status');
  const contentDiv = botDiv.querySelector('.content');
  const sourcesDiv = botDiv.querySelector('.sources');
  let text = '';
  let failed = false;

  try {
    const resp = await fetch('/turn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q, session_id: sessionId })
    });
    if (!resp.ok || !resp.body) {
      throw new Error('Kërkesa nuk mund të përpunohej (HTTP ' + resp.status + ').');
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));

        if (ev.type === 'tool') {
          statusDiv.textContent = '🔍 Kërkim: ' + ev.query;
        } else if (ev.type === 'token') {
          text += ev.text;
          statusDiv.textContent = '';
          contentDiv.innerHTML = escapeHTML(text) + '<span class="cursor"></span>';
          chat.scrollTop = chat.scrollHeight;
        } else if (ev.type === 'error') {
          failed = true;
          statusDiv.textContent = '';
          contentDiv.innerHTML = '<span style="color:#f66">Gabim: ' + escapeHTML(ev.message) + '</span>';
        } else if (ev.type === 'done' && !failed) {
          sessionId = ev.session_id || sessionId;
          if (ev.handoff) {
            statusDiv.textContent = 'Kjo kërkesë duhet t’i kalojë një agjenti.';
          }
          contentDiv.innerHTML = escapeHTML(text);
          if (ev.sources && ev.sources.length) {
            let html = '<strong>Burimet:</strong>';
            const seen = new Set();
            for (const s of ev.sources) {
              const key = s.doc + (s.article || '');
              if (seen.has(key)) continue;
              seen.add(key);
              html += '<a href="' + escapeAttr(s.url) + '" target="_blank" rel="noopener noreferrer">' +
                      escapeHTML(s.doc) + (s.article ? ' — Neni ' + escapeHTML(s.article) : '') + '</a>';
            }
            sourcesDiv.innerHTML = html;
          }
        }
      }
    }
    // The server owns session history; the client keeps only sessionId.
  } catch (e) {
    statusDiv.textContent = '';
    contentDiv.innerHTML = '<span style="color:#f66">Gabim: ' + escapeHTML(e.message) + '</span>';
  }
  sendBtn.disabled = false;
  input.focus();
}

function escapeHTML(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escapeAttr(s) {
  return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
</script>
</body>
</html>"""

class TurnReq(BaseModel):
    """Voice/telephony request. Conversation history is owned by the server."""

    question: str = Field(min_length=2, max_length=1_500)
    session_id: str | None = Field(default=None, max_length=128)
    include_vetted_text: bool = False

    @field_validator("question")
    @classmethod
    def clean_turn_question(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Question must contain at least two characters")
        return value


def turn_done(outcome: Outcome, session_id: str, sources=None, handoff=False,
              pii_redacted=False, usage=None):
    return sse({
        "type": "done",
        "outcome": outcome.value,
        "session_id": session_id,
        "sources": sources or [],
        "handoff": handoff,
        "pii_redacted": pii_redacted,
        "usage": usage if usage is not None else {},
    })


def generate_turn(req: TurnReq):
    """SSE turn contract for web chat today and voice/telephony tomorrow."""
    started = time.perf_counter()
    session = sessions.get(req.session_id)
    decision = None
    outcome = None
    handoff = False
    first_sse_ms = None
    first_token_ms = None
    first_sentence_ms = None
    done_ms = None
    rewrite_used = False
    embedding_reused = False
    top_score = None
    handoff_score = None
    usage: dict[str, Any] = {}
    sources: dict[str, dict[str, str]] = {}
    streamed_text = ""

    def emit(event):
        nonlocal first_sse_ms, first_token_ms, first_sentence_ms, done_ms, streamed_text
        elapsed_ms = (time.perf_counter() - started) * 1000
        if first_sse_ms is None:
            first_sse_ms = elapsed_ms
        if event.get("type") == "token":
            if first_token_ms is None:
                first_token_ms = elapsed_ms
            streamed_text += str(event.get("text") or "")
            if first_sentence_ms is None and _SENTENCE_END_RE.search(streamed_text):
                first_sentence_ms = elapsed_ms
        elif event.get("type") == "done":
            done_ms = elapsed_ms
            if first_sentence_ms is None and streamed_text:
                first_sentence_ms = elapsed_ms
        return sse(event)

    def done_event(value, **kwargs):
        event = json.loads(turn_done(value, session.session_id, usage=usage, **kwargs)[6:])
        return emit(event)

    try:
        decision = decide(req.question, session.last_answer, session.history)
        handoff_score = decision.handoff_score
        if decision.outcome:
            safe_question = decision.question or "[turn i trajtuar nga politika e sigurisë]"
            sessions.record(session, safe_question, decision.message)
            outcome = decision.outcome
            handoff = decision.handoff
            yield emit({"type": "token", "text": decision.message})
            yield done_event(outcome, handoff=handoff,
                             pii_redacted=decision.pii_redacted)
            return

        rewrite_used = needs_rewrite(decision.question, session.history)
        standalone_query = rewrite(decision.question, session.history) \
                           if rewrite_used else decision.question
        yield emit({"type": "tool", "query": standalone_query})
        byte_identical = standalone_query.encode("utf-8") == decision.question.encode("utf-8")
        if byte_identical:
            assert decision.query_embedding is not None
        embedding_reused = byte_identical
        query_embedding = decision.query_embedding if byte_identical else None
        hits, refusal = retrieve_evidence(
            standalone_query,
            session.history,
            query_embedding=query_embedding,
            embedded_query=decision.question if query_embedding is not None else None,
        )
        if hits:
            top_score = float(hits[0]["score"])
        if refusal:
            sessions.record(session, decision.question, refusal)
            outcome = Outcome.UNSUPPORTED
            yield emit({"type": "token", "text": refusal})
            yield done_event(outcome)
            return
        for hit in hits:
            item = source(hit)
            if req.include_vetted_text:
                # Passage text is only for an authenticated voice bridge behind
                # production auth/TLS; default OFF keeps it from public/unaudited consumers.
                item["passage_text"] = hit["text"]
            sources[item["id"]] = item
        messages = grounded_messages(decision.question, session.history, hits)

        answer_parts = []
        for token in stream_answer(messages, session.session_id, usage):
            answer_parts.append(token)
            yield emit({"type": "token", "text": token})
        answer = "".join(answer_parts).strip()
        if not answer:
            answer = NO_EVIDENCE_MESSAGE
            outcome = Outcome.UNSUPPORTED
            yield emit({"type": "token", "text": answer})
        else:
            outcome = Outcome.ANSWER
        sessions.record(session, decision.question, answer)
        yield done_event(outcome, sources=list(sources.values()))
    except RAGError:
        logger.exception("Recoverable RAG error while serving /turn")
        question = decision.question if decision and decision.question else req.question
        sessions.record(session, question, HANDOFF_MESSAGE)
        outcome = Outcome.HANDOFF
        handoff = True
        yield emit({"type": "token", "text": HANDOFF_MESSAGE})
        yield done_event(outcome, handoff=True)
    except Exception:
        logger.exception("Unexpected error while serving /turn")
        question = decision.question if decision and decision.question else req.question
        sessions.record(session, question, HANDOFF_MESSAGE)
        outcome = Outcome.HANDOFF
        handoff = True
        yield emit({"type": "token", "text": HANDOFF_MESSAGE})
        yield done_event(outcome, handoff=True)
    finally:
        final_ms = (time.perf_counter() - started) * 1000
        prompt_details = usage.get("prompt_tokens_details") or {}
        finite_handoff_score = handoff_score
        if isinstance(finite_handoff_score, float) and not math.isfinite(finite_handoff_score):
            finite_handoff_score = None
        telemetry = {
            "session_id": session.session_id,
            "outcome": outcome.value if outcome else Outcome.HANDOFF.value,
            "handoff": handoff,
            "model": MODEL,
            "first_sse_ms": round(first_sse_ms if first_sse_ms is not None else final_ms, 3),
            "first_token_ms": round(first_token_ms if first_token_ms is not None else final_ms, 3),
            "first_sentence_ms": round(first_sentence_ms if first_sentence_ms is not None else final_ms, 3),
            "done_ms": round(done_ms if done_ms is not None else final_ms, 3),
            "rewrite_used": rewrite_used,
            "embedding_reused": embedding_reused,
            "top_score": top_score,
            "n_sources": len(sources),
            "handoff_score": finite_handoff_score,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "cached_tokens": usage.get("cached_tokens", prompt_details.get("cached_tokens", 0)),
        }
        logger.info(json.dumps(telemetry, ensure_ascii=False, allow_nan=False))


@app.post("/turn")
def turn(req: TurnReq):
    return StreamingResponse(generate_turn(req), media_type="text/event-stream")

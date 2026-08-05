# api.py — FastAPI wrapper with SSE.
# /chat : POST {question, history} -> text/event-stream
# events: tool, token, error, done
import json
import logging
from typing import Any

import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from rag import (API, KEY, MODEL, SYSTEM, TOOLS, RAGError, _post,
                 completion_message, retrieve_evidence, rewrite, tool_query)
from trust import (BUSINESS_DEPOSIT_MESSAGE, NO_EVIDENCE_MESSAGE, input_gate,
                   is_business_deposit_question)
from callcenter import HANDOFF_MESSAGE, Outcome, decide, sessions

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 12
MAX_MESSAGE_CHARS = 4_000


class Req(BaseModel):
    """The only client-supplied conversation fields the model may receive."""

    question: str = Field(min_length=2, max_length=1_500)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Question must contain at least two characters")
        return value

    @field_validator("history")
    @classmethod
    def validate_history(cls, history: list[dict[str, str]]) -> list[dict[str, str]]:
        clean_history = []
        for message in history:
            if set(message) != {"role", "content"} or message["role"] not in {"user", "assistant"}:
                raise ValueError("History messages must contain user/assistant role and content only")
            content = message["content"].strip()
            if not content or len(content) > MAX_MESSAGE_CHARS:
                raise ValueError("History message has invalid content length")
            clean_history.append({"role": message["role"], "content": content})
        return clean_history


def sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def stream_answer(messages):
    """Yield answer tokens and convert upstream protocol failures into RAG errors."""
    try:
        with requests.post(API, headers={"Authorization": f"Bearer {KEY}"},
                           json={"model": MODEL, "messages": messages, "stream": True},
                           stream=True, timeout=90) as response:
            response.raise_for_status()
            # OpenRouter may omit a charset on SSE responses; default decoding is then ISO-8859-1.
            response.encoding = "utf-8"
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                body = line[6:]
                if body.strip() == "[DONE]":
                    break
                try:
                    delta = json.loads(body)["choices"][0]["delta"]
                except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                    raise RAGError("Model provider returned an invalid stream") from exc
                if delta.get("content"):
                    yield delta["content"]
    except requests.RequestException as exc:
        raise RAGError("Model provider stream failed") from exc


def source(hit: dict[str, Any]) -> dict[str, str]:
    """Return citation metadata without sending retrieved passages to the browser."""
    return {key: str(hit.get(key) or "") for key in ("id", "doc", "article", "url")}


def generate(req: Req):
    preflight = input_gate(req.question)
    if not preflight.allowed:
        yield sse({"type": "token", "text": preflight.message})
        yield sse({"type": "done", "sources": []})
        return
    if is_business_deposit_question(req.question, req.history):
        yield sse({"type": "token", "text": BUSINESS_DEPOSIT_MESSAGE})
        yield sse({"type": "done", "sources": []})
        return
    messages = [{"role": "system", "content": SYSTEM}] + req.history \
               + [{"role": "user", "content": req.question}]
    sources: dict[str, dict[str, str]] = {}

    try:
        for _ in range(3):
            message = completion_message(_post({"model": MODEL, "messages": messages,
                                                "tools": TOOLS}))
            messages.append(message)

            if not message.get("tool_calls"):
                if not sources:
                    yield sse({"type": "token", "text": NO_EVIDENCE_MESSAGE})
                    yield sse({"type": "done", "sources": []})
                    return
                yield sse({"type": "token", "text": message.get("content", "")})
                yield sse({"type": "done", "sources": list(sources.values())})
                return

            for tool_call in message["tool_calls"]:
                query = tool_query(tool_call)
                standalone_query = rewrite(query, req.history)
                yield sse({"type": "tool", "query": standalone_query})
                hits, refusal = retrieve_evidence(standalone_query, req.history)
                if refusal:
                    yield sse({"type": "token", "text": refusal})
                    yield sse({"type": "done", "sources": []})
                    return
                for hit in hits:
                    item = source(hit)
                    sources[item["id"]] = item
                messages.append({"role": "tool", "tool_call_id": tool_call["id"],
                                 "content": json.dumps(hits, ensure_ascii=False, default=str)})

            for token in stream_answer(messages):
                yield sse({"type": "token", "text": token})
            yield sse({"type": "done", "sources": list(sources.values())})
            return
    except RAGError:
        logger.exception("Recoverable RAG error while serving /chat")
        yield sse({"type": "error", "code": "upstream_error",
                   "message": "Shërbimi i përgjigjeve nuk është i disponueshëm për momentin. Provoni përsëri."})
    except Exception:
        logger.exception("Unexpected error while serving /chat")
        yield sse({"type": "error", "code": "internal_error",
                   "message": "Ndodhi një gabim gjatë kërkimit. Provoni përsëri."})
    yield sse({"type": "done", "sources": list(sources.values())})
@app.post("/chat")
def chat(req: Req):
    return StreamingResponse(generate(req), media_type="text/event-stream")

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

    @field_validator("question")
    @classmethod
    def clean_turn_question(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Question must contain at least two characters")
        return value


def turn_done(outcome: Outcome, session_id: str, sources=None, handoff=False, pii_redacted=False):
    return sse({
        "type": "done",
        "outcome": outcome.value,
        "session_id": session_id,
        "sources": sources or [],
        "handoff": handoff,
        "pii_redacted": pii_redacted,
    })


def generate_turn(req: TurnReq):
    """SSE turn contract for web chat today and voice/telephony tomorrow."""
    session = sessions.get(req.session_id)
    decision = decide(req.question, session.last_answer, session.history)
    if decision.outcome:
        safe_question = decision.question or "[turn i trajtuar nga politika e sigurisë]"
        sessions.record(session, safe_question, decision.message)
        yield sse({"type": "token", "text": decision.message})
        yield turn_done(decision.outcome, session.session_id, handoff=decision.handoff,
                        pii_redacted=decision.pii_redacted)
        return

    messages = [{"role": "system", "content": SYSTEM}] + session.history + [
        {"role": "user", "content": decision.question},
    ]
    sources: dict[str, dict[str, str]] = {}

    try:
        message = completion_message(_post({
            "model": MODEL, "messages": messages, "tools": TOOLS,
        }))
        messages.append(message)
        if not message.get("tool_calls"):
            sessions.record(session, decision.question, NO_EVIDENCE_MESSAGE)
            yield sse({"type": "token", "text": NO_EVIDENCE_MESSAGE})
            yield turn_done(Outcome.UNSUPPORTED, session.session_id)
            return

        for tool_call in message["tool_calls"]:
            query = tool_query(tool_call)
            standalone_query = rewrite(query, session.history)
            yield sse({"type": "tool", "query": standalone_query})
            hits, refusal = retrieve_evidence(standalone_query, session.history)
            if refusal:
                sessions.record(session, decision.question, refusal)
                yield sse({"type": "token", "text": refusal})
                yield turn_done(Outcome.UNSUPPORTED, session.session_id)
                return
            for hit in hits:
                item = source(hit)
                sources[item["id"]] = item
            messages.append({"role": "tool", "tool_call_id": tool_call["id"],
                             "content": json.dumps(hits, ensure_ascii=False, default=str)})

        answer_parts = []
        for token in stream_answer(messages):
            answer_parts.append(token)
            yield sse({"type": "token", "text": token})
        answer = "".join(answer_parts).strip()
        if not answer:
            answer = NO_EVIDENCE_MESSAGE
            outcome = Outcome.UNSUPPORTED
        else:
            outcome = Outcome.ANSWER
        sessions.record(session, decision.question, answer)
        yield turn_done(outcome, session.session_id, list(sources.values()))
    except RAGError:
        logger.exception("Recoverable RAG error while serving /turn")
        sessions.record(session, decision.question, HANDOFF_MESSAGE)
        yield sse({"type": "token", "text": HANDOFF_MESSAGE})
        yield turn_done(Outcome.HANDOFF, session.session_id, handoff=True)
    except Exception:
        logger.exception("Unexpected error while serving /turn")
        sessions.record(session, decision.question, HANDOFF_MESSAGE)
        yield sse({"type": "token", "text": HANDOFF_MESSAGE})
        yield turn_done(Outcome.HANDOFF, session.session_id, handoff=True)


@app.post("/turn")
def turn(req: TurnReq):
    return StreamingResponse(generate_turn(req), media_type="text/event-stream")


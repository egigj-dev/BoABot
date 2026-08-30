# api.py — FastAPI wrapper with SSE.
# events: tool, token, error, done
import dataclasses
import json
import hmac
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .rag import (API, MODEL, RAGError, api_key, grounded_messages, needs_rewrite,
                 retrieve_evidence, rewrite)
from .callcenter import (CARD_CLARIFY_MESSAGE, LEGAL_ADVICE_MESSAGE, DecisionEvent,
                        DecisionReason, Outcome, _structured_rate_decision, decide,
                        is_ambiguous_card_maintenance, next_structured_frame,
                        sessions)
from .comparison import is_elliptical_rate_turn
from .retrieve import embedding_stats
from .retrieve import open_pool as open_retrieval_pool
from .retrieve import shutdown as shutdown_retrieval
from .retrieve import warmup as warmup_retrieval
from .trust import NO_EVIDENCE_MESSAGE
from .text_norm import fold as _fold_text
from .answerability import ABSTAIN_MESSAGE, judge
from voice.shared.fidelity_guard import FidelityGuard
from voice.shared.sentence_buffer import SentenceBuffer

logger = logging.getLogger(__name__)
FIRST_TOKEN_BUDGET_MS = 6000
DEGRADED_MESSAGE = (
    "Ka një problem teknik të përkohshëm. Ju lutem provoni përsëri pas pak."
)
_SENTENCE_END_RE = re.compile(r"[.!?][\"'»”\)\]]?(?:\s|$)")
_MODEL_SOURCE_IDS_RE = re.compile(
    r"^\s*sources?\s*:\s*\[(?:\s*(?:rate|reg)_\d+\s*,?)+\]\s*$",
    re.IGNORECASE,
)
# Post-generation all-or-nothing backstop: any generated sentence that DIRECTS a
# legal conclusion at the caller (2nd-person "ju / you"), rather than a neutral
# third-person statement of the law, turns "what the law says" into "what YOU
# should do / whether YOU are liable" — i.e. personalized legal advice. The whole
# turn must be replaced (a per-sentence soft-drop would leave a redacted advice
# sentence standing). Model is primed to avoid these via rag.py SYSTEM; this is
# defense-in-depth. Kept to specific 2nd-person legal constructions so benign uses
# of "ju / Ju lutem" and neutral "klienti detyrohet" do not fire.
_LEGAL_DIRECT_RE = re.compile(
    r"\b(?:"
    r"ju\s+duhet\b|"
    r"duhet\s+te\s+paguani\b|"
    r"jeni\s+pergjegjes\b|"
    r"keni\s+te\s+drejte\s+te\b|"
    r"keni\s+detyrim\b|"
    r"detyrim\s+juaj\b|"
    r"(?:duhet\s+te\s+|mund\s+te\s+)kerkoni\s+(?:demshperblim|kompensim)\b|"
    r"mund\s+te\s+padisni\b|"
    r"ju\s+mund\s+te\b"
    r")\b",
    re.I,
)


def _has_legal_advice_direct(text: str) -> bool:
    return _LEGAL_DIRECT_RE.search(_fold_text(text)) is not None
_fidelity_guard = FidelityGuard()

# ---- Step 3: no-repeat across turns (bge-m3 cosine vs predecessor) ---------
# The model already receives history (and now an explicit no-repeat SYSTEM
# rule), but a provider can still echo a verbatim sentence from the previous
# answer. Each generated sentence is cosine-checked against the previous
# answer and dropped when near-identical. Threshold is calibrated (not
# eyeballed) and fires only on near-verbatim repetition, so a legitimate
# follow-up that re-uses a phrase is not suppressed.
NO_REPEAT_COSINE_THRESHOLD = 0.92


def _repeat_embed(text: str):
    """Embed one string for the no-repeat check (injectable seam for tests)."""
    from .retrieve import model
    return np.asarray(model().encode([text], normalize_embeddings=True)[0], dtype=np.float32)


def _is_near_duplicate(sentence: str, prior_embedding, threshold: float = NO_REPEAT_COSINE_THRESHOLD) -> bool:
    """True when a generated sentence is a near-verbatim repeat of the prior answer."""
    if prior_embedding is None or not str(sentence).strip():
        return False
    sentence_embedding = _repeat_embed(sentence)
    return float(np.dot(sentence_embedding, prior_embedding)) >= threshold


@asynccontextmanager
async def lifespan(_app: FastAPI):
    open_retrieval_pool()
    warmup_retrieval()
    try:
        yield
    finally:
        logger.warning("query embedding reuse totals: %s", embedding_stats())
        shutdown_retrieval()


app = FastAPI(lifespan=lifespan)
_allowed_origins = [
    origin.strip() for origin in os.environ.get(
        "BOABOT_ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000"
    ).split(",") if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-BoABot-Voice-Key"],
)


def sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def stream_answer(messages, session_id=None, usage=None):
    """Yield answer tokens and convert upstream protocol failures into RAG errors."""
    payload = {"model": MODEL, "messages": messages, "stream": True,
               "temperature": 0,
               "usage": {"include": True}}
    if session_id:
        # OpenRouter uses this as the sticky-routing key, keeping one conversation
        # on a cache-capable provider without changing model selection.
        payload["session_id"] = session_id
    content_yielded = False
    usage = usage if usage is not None else {}
    started = time.perf_counter()
    for attempt in range(2):
        usage.clear()
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
            return
        except requests.RequestException as exc:
            if content_yielded or attempt == 1:
                raise RAGError("Model provider stream failed") from exc


def source(hit: dict[str, Any]) -> dict[str, str]:
    """Return citation metadata without sending retrieved passages to the browser."""
    return {key: str(hit.get(key) or "") for key in ("id", "doc", "article", "url", "issuer")}


def authorized_sentences(token_stream, hits, prior_answer: str | None = None):
    """Yield complete model sentences only after evidence-fidelity validation.

    Callers must buffer this iterator to completion before releasing any
    sentence. A late rejection then cannot contradict already-spoken output.
    """
    evidence = tuple({
        "doc": str(hit.get("doc") or ""),
        "article": str(hit.get("article") or ""),
        "passage_text": str(hit.get("text") or ""),
    } for hit in hits)
    prior_embedding = None
    if prior_answer and str(prior_answer).strip():
        prior_embedding = _repeat_embed(prior_answer)
    buffer = SentenceBuffer()
    for token in token_stream:
        for sentence in buffer.feed_token(token):
            if _MODEL_SOURCE_IDS_RE.fullmatch(sentence):
                continue
            if _is_near_duplicate(sentence, prior_embedding):
                # Step 3 no-repeat: verbatim echo of the previous answer is
                # dropped (soft-fail, same pattern as fidelity drops).
                logger.warning("Dropped repeated sentence: %s", sentence)
                continue
            verdict = _fidelity_guard.verify_sources(sentence, evidence)
            if not verdict.approved:
                # Soft-fail: drop only this sentence, keep the rest of the
                # answer. A single unverifiable sentence should not collapse
                # the whole (already buffered) response into DEGRADED.
                logger.warning("Dropped unverified sentence: %s", verdict.reason)
                continue
            yield sentence
    for sentence in buffer.finish():
        if _MODEL_SOURCE_IDS_RE.fullmatch(sentence):
            continue
        if _is_near_duplicate(sentence, prior_embedding):
            logger.warning("Dropped repeated sentence: %s", sentence)
            continue
        verdict = _fidelity_guard.verify_sources(sentence, evidence)
        if not verdict.approved:
            logger.warning("Dropped unverified sentence: %s", verdict.reason)
            continue
        yield sentence


def safe_sentences(text: str) -> list[str]:
    """Split deterministic policy prose into the same speakable sentence units."""
    buffer = SentenceBuffer()
    return buffer.feed_token(text) + buffer.finish()


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


def _voice_bridge_authorized(request: Request) -> bool:
    """Authenticate access to full vetted corpus passages server-side."""
    expected = os.environ.get("BOABOT_VOICE_BRIDGE_KEY")
    supplied = request.headers.get("X-BoABot-Voice-Key")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def turn_done(outcome: Outcome, session_id: str, sources=None, handoff=False,
              pii_redacted=False, usage=None, reason=None, answer_text=None,
              answer_display=None):
    event = {
        "type": "done",
        "outcome": outcome.value,
        "session_id": session_id,
        "sources": sources or [],
        "handoff": handoff,
        "pii_redacted": pii_redacted,
        "usage": usage if usage is not None else {},
        "reason": reason,
    }
    # Step 9 (answer_text / answer_display split): consumers that render the
    # answer (voice/telephony TTS) read answer_text (plain speakable prose);
    # browser/chat consumers may render answer_display (presently the same
    # plain prose, kept as a distinct field so a formatting/citation layer can
    # diverge later without a contract break). Omitted (None) on non-answer
    # outcome paths so the payload stays backward-compatible.
    if answer_text is not None:
        event["answer_text"] = answer_text
    if answer_display is not None:
        event["answer_display"] = answer_display
    return sse(event)


def generate_turn(req: TurnReq, *, include_vetted_text: bool = False):
    """SSE turn contract for web chat today and voice/telephony tomorrow."""
    started = time.perf_counter()
    session = sessions.get(req.session_id)
    decision = None
    outcome = None
    handoff = False
    handoff_reason = None
    first_sse_ms = None
    first_token_ms = None
    first_sentence_ms = None
    done_ms = None
    rewrite_used = False
    embedding_reused = False
    abstain_reason = None
    top_score = None
    retrieval_source = None
    retrieval_stats: dict[str, Any] = {}
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
        if os.environ.get("BOABOT_DEBUG") == "1":
            event["trace_flags"] = sorted(
                flag.value for flag in (decision.trace_flags if decision else ())
            )
        return emit(event)

    def emit_policy_message(message: str):
        """Emit deterministic policy prose with one consistent SSE contract."""
        for index, sentence in enumerate(safe_sentences(message)):
            piece = sentence if index == 0 else f" {sentence}"
            yield emit({"type": "token", "text": piece})
            yield emit({"type": "approved_sentence", "text": sentence})

    try:
        decision = decide(
            req.question, session.last_answer, session.history,
            getattr(session, "last_outcome", None),
            getattr(session, "last_handoff", False),
            getattr(session, "last_structured_frame", None),
        )
        session.last_structured_frame = next_structured_frame(
            decision, getattr(session, "last_structured_frame", None),
        )
        handoff_score = decision.handoff_score
        if decision.outcome:
            safe_question = decision.question or "[turn i trajtuar nga politika e sigurisë]"
            sessions.record(
                session, safe_question, decision.message,
                decision.outcome, decision.handoff,
            )
            outcome = decision.outcome
            handoff = decision.handoff
            handoff_reason = decision.reason.value
            yield from emit_policy_message(decision.message)
            yield done_event(outcome, handoff=handoff,
                             pii_redacted=decision.pii_redacted,
                             reason=decision.reason.value)
            return

        rate_intent = getattr(decision, "rate_intent", None)
        # A carried typed key is already standalone and must bypass every rewrite
        # and embedding seam. The fused LLM router never ran on this path.
        if rate_intent is not None:
            standalone_query = decision.question
        else:
            # Step 2b: when the fused router (ON) supplied a standalone query,
            # use it directly and skip the separate rewrite call.
            fused_rewrite = getattr(decision, "rewritten_query", None) or None
            if fused_rewrite:
                standalone_query = fused_rewrite
                rewrite_used = True
            else:
                rewrite_used = needs_rewrite(decision.question, session.history)
                standalone_query = rewrite(decision.question, session.history) \
                                   if rewrite_used else decision.question
            if rewrite_used:
                decision = dataclasses.replace(
                    decision,
                    trace_flags=decision.trace_flags | {
                        DecisionEvent.query_rewritten,
                    },
                )
        if (rate_intent is None
                and (rewrite_used or is_elliptical_rate_turn(decision.question))):
            reparsed = _structured_rate_decision(standalone_query)
            if reparsed is not None:
                if reparsed.rate_intent is None:
                    decision = dataclasses.replace(
                        decision,
                        trace_flags=decision.trace_flags | reparsed.trace_flags,
                    )
                else:
                    decision = dataclasses.replace(
                        reparsed,
                        trace_flags=reparsed.trace_flags | decision.trace_flags,
                    )
                    session.last_structured_frame = next_structured_frame(
                        decision, getattr(session, "last_structured_frame", None),
                    )
                    rate_intent = decision.rate_intent
        yield emit({"type": "tool", "query": standalone_query})
        if rate_intent is None and is_ambiguous_card_maintenance(standalone_query):
            sessions.record(
                session, decision.question, CARD_CLARIFY_MESSAGE, Outcome.CLARIFY,
            )
            outcome = Outcome.CLARIFY
            handoff_reason = DecisionReason.REWRITE_CARD_CLARIFY.value
            yield from emit_policy_message(CARD_CLARIFY_MESSAGE)
            yield done_event(outcome, reason=handoff_reason)
            return
        byte_identical = standalone_query.encode("utf-8") == decision.question.encode("utf-8")
        if byte_identical and rate_intent is None:
            assert decision.query_embedding is not None
        embedding_reused = byte_identical and rate_intent is None
        query_embedding = decision.query_embedding if embedding_reused else None
        hits, refusal = retrieve_evidence(
            standalone_query,
            session.history,
            query_embedding=query_embedding,
            embedded_query=decision.question if query_embedding is not None else None,
            stats=retrieval_stats,
            rate_intent=rate_intent,
        )
        if hits:
            retrieval_source = str(hits[0].get("retrieval_source") or "dense")
            try:
                top_score = float(hits[0]["dense_score"])
            except (KeyError, TypeError, ValueError):
                top_score = None
        if refusal:
            sessions.record(session, decision.question, refusal, Outcome.UNSUPPORTED)
            outcome = Outcome.UNSUPPORTED
            handoff_reason = (
                DecisionReason.CATALOG_MISSING_KEY.value if rate_intent is not None
                else DecisionReason.DENSE_NO_TRUSTED_HITS.value
            )
            yield from emit_policy_message(refusal)
            yield done_event(outcome, reason=handoff_reason)
            return
        # ---- Answerability / abstain (3-way gate — Step 6) ----
        # Retrieval admitted evidence, but before generation we check whether that
        # evidence actually ANSWERS the question. judge() classifies into
        # SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED; only UNSUPPORTED
        # abstains deterministically (no model call, no hallucination risk).
        support_level, abstain_reason = judge(
            standalone_query, hits, rate_intent=rate_intent,
        )
        if support_level == "UNSUPPORTED":
            sessions.record(
                session, decision.question, ABSTAIN_MESSAGE, Outcome.UNSUPPORTED,
            )
            outcome = Outcome.UNSUPPORTED
            handoff_reason = DecisionReason.ANSWERABILITY_ABSTAIN.value
            yield from emit_policy_message(ABSTAIN_MESSAGE)
            yield done_event(outcome, reason=handoff_reason)
            return
        for hit in hits:
            item = source(hit)
            if include_vetted_text:
                # Full passages are available only after server-side bridge auth.
                item["passage_text"] = hit["text"]
            sources[item["id"]] = item

        if rate_intent is not None:
            from .comparison import render_rate_answer

            answer = render_rate_answer(rate_intent, hits)
            if not answer:
                abstain_reason = "structured_rate_empty_render"
                outcome = Outcome.UNSUPPORTED
                handoff_reason = DecisionReason.STRUCTURED_EMPTY_RENDER.value
                answer = NO_EVIDENCE_MESSAGE
                yield from emit_policy_message(answer)
            else:
                # [SUPERSEDED] Structured rows previously flowed through
                # grounded_messages -> stream_answer -> authorized_sentences.
                # The exact renderer is now the authority for this typed path.
                yield emit({"type": "token", "text": answer})
                yield emit({"type": "approved_sentence", "text": answer})
                outcome = Outcome.ANSWER
                handoff_reason = DecisionReason.CATALOG_EXACT_HIT.value
            sessions.record(session, decision.question, answer, outcome)
            yield done_event(
                outcome, sources=list(sources.values()),
                answer_text=answer if outcome is Outcome.ANSWER else None,
                answer_display=answer if outcome is Outcome.ANSWER else None,
                reason=handoff_reason,
            )
            return
        # Generation must see the same standalone query that selected the
        # evidence. Passing an elliptical original (for example "Dhe neni 7?")
        # made the model occasionally ignore an exact article hit.
        messages = grounded_messages(standalone_query, session.history, hits)

        # Buffer through the complete fidelity pass before any generated text is
        # released. A rejection can therefore never invalidate spoken output.
        answer_parts = list(authorized_sentences(
            stream_answer(messages, session.session_id, usage), hits,
            prior_answer=session.last_answer,
        ))
        full_answer = " ".join(answer_parts).strip()
        answer_text = answer_display = None
        if not full_answer:
            answer = NO_EVIDENCE_MESSAGE
            outcome = Outcome.UNSUPPORTED
            handoff_reason = DecisionReason.EMPTY_ANSWER.value
            yield from emit_policy_message(answer)
        elif _has_legal_advice_direct(full_answer):
            # All-or-nothing: any caller-directed legal conclusion must replace
            # the whole turn. A per-sentence drop would leave a redacted
            # personal-advice sentence standing as an answer.
            answer = LEGAL_ADVICE_MESSAGE
            outcome = Outcome.UNSUPPORTED
            handoff_reason = DecisionReason.LEGAL_ADVICE_POSTGEN.value
            yield from emit_policy_message(answer)
        else:
            for index, sentence in enumerate(answer_parts):
                piece = sentence if index == 0 else f" {sentence}"
                yield emit({"type": "token", "text": piece})
                yield emit({"type": "approved_sentence", "text": sentence})
            answer = full_answer
            outcome = Outcome.ANSWER
            handoff_reason = DecisionReason.DENSE_ANSWER.value
            # Step 9 split: answer_text = plain speakable prose (TTS/voice);
            # answer_display = browser-renderable form (same prose today).
            answer_text = full_answer
            answer_display = full_answer
        sessions.record(session, decision.question, answer, outcome)
        yield done_event(outcome, sources=list(sources.values()),
                         answer_text=answer_text, answer_display=answer_display,
                         reason=handoff_reason)
    except GeneratorExit:
        outcome = Outcome.ABANDONED
        handoff_reason = DecisionReason.CLIENT_DISCONNECT.value
        raise
    except RAGError:
        logger.exception("Recoverable RAG error while serving /turn")
        question = decision.question if decision and decision.question else req.question
        sessions.record(session, question, DEGRADED_MESSAGE, Outcome.DEGRADED)
        outcome = Outcome.DEGRADED
        handoff_reason = DecisionReason.PROVIDER_ERROR.value
        yield from emit_policy_message(DEGRADED_MESSAGE)
        yield done_event(outcome, reason=handoff_reason)
    except Exception:
        logger.exception("Unexpected error while serving /turn")
        question = decision.question if decision and decision.question else req.question
        sessions.record(session, question, DEGRADED_MESSAGE, Outcome.DEGRADED)
        outcome = Outcome.DEGRADED
        handoff_reason = DecisionReason.INTERNAL_ERROR.value
        yield from emit_policy_message(DEGRADED_MESSAGE)
        yield done_event(outcome, reason=handoff_reason)
    finally:
        final_ms = (time.perf_counter() - started) * 1000
        prompt_details = usage.get("prompt_tokens_details") or {}
        telemetry = {
            "session_id": session.session_id,
            "outcome": outcome.value if outcome else Outcome.ABANDONED.value,
            "handoff": handoff,
            "handoff_reason": handoff_reason,
            "abstain_reason": abstain_reason,
            "model": MODEL,
            "first_sse_ms": round(first_sse_ms if first_sse_ms is not None else final_ms, 3),
            "first_token_ms": round(first_token_ms if first_token_ms is not None else final_ms, 3),
            "first_sentence_ms": round(first_sentence_ms if first_sentence_ms is not None else final_ms, 3),
            "done_ms": round(done_ms if done_ms is not None else final_ms, 3),
            "rewrite_used": rewrite_used,
            "embedding_reused": embedding_reused,
            "top_score": top_score,
            "retrieval_source": retrieval_source,
            "retrieval_dropped_hits": retrieval_stats.get("dropped_hits", 0),
            "retrieval_admission_reason": retrieval_stats.get("admission_reason"),
            "n_sources": len(sources),
            "handoff_score": handoff_score,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "cached_tokens": usage.get("cached_tokens", prompt_details.get("cached_tokens", 0)),
        }
        logger.info(json.dumps(telemetry, ensure_ascii=False, allow_nan=False))


@app.post("/turn")
def turn(req: TurnReq, request: Request):
    include_vetted_text = req.include_vetted_text and _voice_bridge_authorized(request)
    return StreamingResponse(
        generate_turn(req, include_vetted_text=include_vetted_text),
        media_type="text/event-stream",
    )

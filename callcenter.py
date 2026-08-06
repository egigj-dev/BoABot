"""Call-center conversation policy and in-memory session state."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import uuid
import zlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np

from retrieve import model
from trust import (BUSINESS_DEPOSIT_MESSAGE, UNSAFE_INPUT_MESSAGE, input_gate,
                   is_business_deposit_question)

MAX_HISTORY_MESSAGES = 12
SESSION_TTL_SECONDS = 60 * 60
MAX_SESSIONS = 1_000

CLARIFY_MESSAGE = (
    "Mund ta sqaroni pak pyetjen? Për shembull, tregoni bankën, produktin "
    "ose rregulloren për të cilën po pyesni."
)
HANDOFF_MESSAGE = (
    "Për sigurinë tuaj, kjo kërkesë duhet të trajtohet nga një agjent njerëzor. "
    "Mos ndani PIN-in, fjalëkalimin ose kodet e verifikimit në këtë bisedë."
)
REPEAT_MESSAGE = "Nuk kam ende një përgjigje për ta përsëritur. Si mund t’ju ndihmoj?"

class Outcome(str, Enum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"
    HANDOFF = "handoff"
    REPEAT = "repeat"

@dataclass(frozen=True)
class Decision:
    outcome: Outcome | None
    message: str = ""
    question: str = ""
    handoff: bool = False
    pii_redacted: bool = False
    query_embedding: np.ndarray | None = None  # Normalized caller vector for downstream retrieval reuse.
    handoff_score: float | None = None  # Frozen positive-vs-negative neighbour margin.

@dataclass
class Session:
    session_id: str
    history: list[dict[str, str]]
    last_answer: str
    updated_at: float

class SessionStore:
    """Bounded, process-local state; replace with Redis for multi-instance deployment."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    def get(self, requested_id: str | None) -> Session:
        now = time.time()
        with self._lock:
            self._evict(now)
            if requested_id and requested_id in self._sessions:
                session = self._sessions[requested_id]
                session.updated_at = now
                return session
            session = Session(uuid.uuid4().hex, [], "", now)
            self._sessions[session.session_id] = session
            return session

    def record(self, session: Session, question: str, answer: str) -> None:
        with self._lock:
            session.history.extend((
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ))
            session.history = session.history[-MAX_HISTORY_MESSAGES:]
            session.last_answer = answer
            session.updated_at = time.time()

    def _evict(self, now: float) -> None:
        stale = [sid for sid, value in self._sessions.items()
                 if now - value.updated_at > SESSION_TTL_SECONDS]
        for sid in stale:
            del self._sessions[sid]
        overflow = len(self._sessions) - MAX_SESSIONS
        if overflow > 0:
            oldest = sorted(self._sessions.values(), key=lambda value: value.updated_at)[:overflow]
            for value in oldest:
                del self._sessions[value.session_id]

sessions = SessionStore()

# Fast-path only credential disclosures or active access incidents; general PIN/CVV questions use semantic routing.
_SECRET_FAST_RE = re.compile(
    r"(?:\b(?:pin|cvv|cvc|otp)\b.{0,80}\b(?:zbulu|kompromet|vjedh|pa|dha|ndava|tregova|"
    r"kerk|doli|nuk funksion)|\b(?:zbulu|kompromet|vjedh|pa|dha|ndava|tregova|kerk|doli|"
    r"nuk funksion).{0,80}\b(?:pin|cvv|cvc|otp)\b)", re.I)

# Frozen grouped-train nearest-neighbour classifier; serving needs NumPy only.
_PROBE_PATH = Path(__file__).with_name("handoff_probe.json")
_PROBE_DATA = json.loads(_PROBE_PATH.read_text(encoding="utf-8"))  # Exported classifier metadata and exemplars.
_PROBE_BYTES = zlib.decompress(base64.b64decode(_PROBE_DATA["vectors_zlib_b64"]))  # Compressed frozen vectors.
_PROBE_VECTORS = np.frombuffer(_PROBE_BYTES, dtype="<f4").reshape(_PROBE_DATA["shape"])  # Train embeddings.
_PROBE_LABELS = np.asarray(_PROBE_DATA["labels"], dtype=bool)  # Positive/negative class for each exemplar.
_HANDOFF_THRESHOLD = float(_PROBE_DATA["margin"])  # Train-tuned FP<=2% class-margin threshold.
if _PROBE_DATA["k"] != 1 or _PROBE_VECTORS.shape[1] != _PROBE_DATA["dimensions"]:
    raise RuntimeError("handoff_probe.json has invalid nearest-neighbour data")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?355\s*)?(?:6[789]|0)\d(?:[\s-]?\d){6,8}(?!\d)")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)(?:\d[ -]?){8,18}\d(?!\d)")

def _redact_pii(text: str) -> tuple[str, bool]:
    redacted = _EMAIL_RE.sub("[email i fshehur]", text)
    redacted = _PHONE_RE.sub("[numër telefoni i fshehur]", redacted)
    redacted = _LONG_NUMBER_RE.sub("[numër i fshehur]", redacted)
    return redacted, redacted != text

def _is_repeat(text: str) -> bool:
    lowered = text.casefold()
    return any(term in lowered for term in (
        "përsërite", "perserite", "ma përsërit", "ma perserit",
        "nuk dëgjova", "nuk degjova", "thuaje prap", "repeat",
    ))

def _encode_question(question: str) -> np.ndarray:
    """Sole callcenter embedding entry point: exactly one normalized encode per routed turn."""
    return np.asarray(model().encode([question], normalize_embeddings=True)[0], dtype=np.float32)


def _probe_score(query_embedding: np.ndarray) -> float:
    """Return positive-minus-negative cosine margin, or negative infinity on a negative nearest neighbour."""
    similarities = _PROBE_VECTORS @ query_embedding
    if not _PROBE_LABELS[int(np.argmax(similarities))]:
        return float("-inf")
    positive_similarity = float(np.max(similarities[_PROBE_LABELS]))
    negative_similarity = float(np.max(similarities[~_PROBE_LABELS]))
    return positive_similarity - negative_similarity


def decide(question: str, last_answer: str, history: list[dict[str, str]]) -> Decision:
    """Route a caller turn before it can reach retrieval or the model."""
    gate = input_gate(question)
    if not gate.allowed:
        return Decision(Outcome.UNSUPPORTED, UNSAFE_INPUT_MESSAGE)

    if _is_repeat(question):
        return Decision(Outcome.REPEAT, last_answer or REPEAT_MESSAGE)

    if _SECRET_FAST_RE.search(question):
        return Decision(Outcome.HANDOFF, HANDOFF_MESSAGE, handoff=True)

    if is_business_deposit_question(question, history):
        return Decision(Outcome.UNSUPPORTED, BUSINESS_DEPOSIT_MESSAGE)

    clean_question, pii_redacted = _redact_pii(question)
    if pii_redacted:
        return Decision(Outcome.HANDOFF, HANDOFF_MESSAGE, handoff=True, pii_redacted=True)

    if len(clean_question.split()) < 2:
        return Decision(Outcome.CLARIFY, CLARIFY_MESSAGE)

    query_embedding = _encode_question(clean_question)
    handoff_score = _probe_score(query_embedding)
    if handoff_score >= _HANDOFF_THRESHOLD:
        return Decision(Outcome.HANDOFF, HANDOFF_MESSAGE, handoff=True,
                        query_embedding=query_embedding, handoff_score=handoff_score)
    if len(clean_question.split()) < 3:
        return Decision(Outcome.CLARIFY, CLARIFY_MESSAGE,
                        query_embedding=query_embedding, handoff_score=handoff_score)
    return Decision(None, question=clean_question, query_embedding=query_embedding,
                    handoff_score=handoff_score)

